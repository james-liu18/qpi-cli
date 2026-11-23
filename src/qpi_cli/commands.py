# -*- coding: utf-8 -*-
"""qpi-cli 各子命令实现。"""

import json
import os
import re
import shlex
import shutil
import subprocess
import time

from . import CLI_NAME, __version__
from .core import (
    BUILD_DIR,
    BUILD_SH,
    FLASH_SH,
    GEN_HEADER,
    INSTALL_ENV_SH,
    VERSION_TXT,
    banner,
    configured_flash_entry,
    dget,
    dset,
    default_config,
    error,
    flash_entry_error,
    fw_dir_name,
    fw_dir_problem,
    has_fw_fingerprint,
    info,
    resolve_flash_entry,
    run_sdk,
    verdict,
    warn,
)
from .templates import (
    BUILD_SH_TMPL,
    CODE_WORKSPACE_TMPL,
    ENV_SH_TMPL,
    LAYER_CONF_TMPL,
    MAIN_C_TMPL,
    MAKEFILE_TMPL,
    RECIPE_TMPL,
)


# ---------------------------------------------------------------- 子命令 ----
def parse_dimensions(args):
    """把 CLI 参数归一化成 (project, rev, os_dim, ver_dim, sec)。"""
    cfg = args.ctx.config
    project = args.project or dget(cfg, "build.project", "QSM565DWF")
    rev = args.rev or args.ctx.current_rev()
    os_dim = args.os or dget(cfg, "build.os", "LINUX")
    ver_dim = args.variant or dget(cfg, "build.version", "STD")
    sec = bool(args.sec)
    return project, rev, os_dim, ver_dim, sec


def cmd_version(args):
    ctx = args.ctx
    meta = ctx.sdk_meta()
    hdr = ctx.current_header()
    print("%s %s" % (CLI_NAME, __version__))
    print(
        "SDK        : %s (%s)"
        % (meta.get("SDK_NAME", "-"), meta.get("SDK_TYPE", "-"))
    )
    print("SDK root   : %s" % ctx.root)
    print(
        "Platform   : MACHINE=%s DISTRO=%s IMAGE=%s"
        % (
            meta.get("MACHINE", "-"),
            meta.get("DISTRO", "-"),
            meta.get("TARGET_IMAGE", "-"),
        )
    )
    print(
        "Buildconfig: project=%s rev=%s custom=%s"
        % (
            hdr.get("QUECTEL_PROJECT_NAME", "-"),
            hdr.get("QUECTEL_PROJECT_REV", "-"),
            hdr.get("QUECTEL_CUSTOM_NAME", "-"),
        )
    )
    print("Flash entry: %s" % (configured_flash_entry(ctx) or "-"))
    return 0


def cmd_config(args):
    ctx = args.ctx
    if args.init:
        if os.path.isfile(ctx.config_path) and not ctx.read_only and not args.force:
            warn("%s 已存在, 使用 --force 覆盖" % ctx.config_path)
            return 1
        ctx.config = default_config()
        ctx.save_config()
        info("已写入默认配置: %s" % ctx.config_path)
        return 0
    if args.set:
        for item in args.set:
            if "=" not in item:
                error("--set 期望 KEY=VALUE, 实际: %s" % item)
                return 1
            key, value = item.split("=", 1)
            dset(ctx.config, key, value)
            info("已设置 %s = %s" % (key, value))
        ctx.save_config()
        return 0
    if args.get:
        val = dget(ctx.config, args.get)
        if val is None:
            error("没有该配置项: %s" % args.get)
            return 1
        print(val)
        return 0
    print("Config file: %s" % ctx.config_path)
    if ctx.read_only:
        print("            (SDK 根下 .qpi_config.json, 只读覆盖)")
    print(json.dumps(ctx.config, indent=2, ensure_ascii=False))
    return 0


APP_LAYER_NAME = "meta-qpi-apps"
# set_bb_env.sh 每次 source build.sh 都会拿这个模板重建 build-*/conf/bblayers.conf
BBLAYERS_TMPL = "layers/meta-qcom-distro/conf/bblayers.conf"


def _target_image(ctx):
    """读取当前构建的镜像 recipe 名 (base-recipes), 缺省 quecpi-image。"""
    try:
        with open(ctx.p("quectel_build/config/base-recipes"), "r") as fh:
            name = fh.read().strip()
    except OSError:
        name = ""
    return name or dget(ctx.config, "build.image", "quecpi-image")


def _add_layer_to_bblayers(path, layer_name):
    """把 ${WORKSPACE}/layers/<name> 插到 EXTRALAYERS 块首行, 返回是否修改。"""
    with open(path, "r") as fh:
        lines = fh.read().splitlines()
    entry = "${WORKSPACE}/layers/%s" % layer_name
    if any(entry in line for line in lines):
        return False
    for idx, line in enumerate(lines):
        if line.startswith("EXTRALAYERS ?="):
            lines.insert(idx + 1, "  %s \\" % entry)
            with open(path, "w") as fh:
                fh.write("\n".join(lines) + "\n")
            return True
    return False


def _ensure_app_layer(ctx):
    """创建用户应用层并注册到 bblayers.conf, 返回层目录。"""
    layer_dir = ctx.p("layers", APP_LAYER_NAME)
    os.makedirs(os.path.join(layer_dir, "conf"), exist_ok=True)
    layer_conf = os.path.join(layer_dir, "conf", "layer.conf")
    if not os.path.isfile(layer_conf):
        with open(layer_conf, "w") as fh:
            fh.write(LAYER_CONF_TMPL)

    # 模板是持久生效的, 构建目录里的那份只对当前会话生效 (下次 source 会被重建)
    found = False
    for rel in (BBLAYERS_TMPL, os.path.join(BUILD_DIR, "conf", "bblayers.conf")):
        path = ctx.p(rel)
        if not os.path.isfile(path):
            continue
        found = True
        try:
            if _add_layer_to_bblayers(path, APP_LAYER_NAME):
                info("已把 %s 加入 %s 的 EXTRALAYERS" % (APP_LAYER_NAME, rel))
        except OSError as exc:
            warn("更新 %s 失败: %s" % (rel, exc))
    if not found:
        warn(
            "未找到 bblayers.conf, 请手动把 ${WORKSPACE}/layers/%s 加入 EXTRALAYERS"
            % APP_LAYER_NAME
        )
    return layer_dir


def _write_app_recipe(layer_dir, app):
    rec_dir = os.path.join(layer_dir, "recipes-apps", app)
    os.makedirs(rec_dir, exist_ok=True)
    path = os.path.join(rec_dir, "%s.bb" % app)
    with open(path, "w") as fh:
        fh.write(RECIPE_TMPL % {"app": app})
    return path


def _append_image_install(layer_dir, image, app):
    """在镜像 bbappend 里追加 IMAGE_INSTALL, 保证应用进最终镜像。"""
    img_dir = os.path.join(layer_dir, "recipes-images", "images")
    os.makedirs(img_dir, exist_ok=True)
    path = os.path.join(img_dir, "%s.bbappend" % image)
    pkgs = []
    if os.path.isfile(path):
        with open(path, "r") as fh:
            for line in fh:
                m = re.match(r'^\s*IMAGE_INSTALL:append\s*=\s*"(.*)"\s*$', line)
                if m:
                    pkgs.extend(m.group(1).split())
    if app not in pkgs:
        pkgs.append(app)
    with open(path, "w") as fh:
        fh.write("# qpi-cli 生成: projects/ 下的应用装入镜像, 请勿手工编辑\n")
        fh.write('IMAGE_INSTALL:append = " %s"\n' % " ".join(pkgs))
    return path


def cmd_new(args):
    ctx = args.ctx
    app = args.name
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_.-]*$", app):
        error(
            "非法应用名 (仅允许字母/数字/_/./-, 且不能以数字开头): %s" % app
        )
        return 1
    # 应用工程落在 SDK 根下的 projects/ (对外仓库运行时生成, 不纳入版本管理)
    projects_dir = ctx.p(dget(ctx.config, "projects_dir", "projects"))
    app_dir = os.path.join(projects_dir, app)
    if os.path.exists(app_dir) and not args.force:
        error("%s 已存在, 使用 --force 覆盖" % app_dir)
        return 1
    src_dir = os.path.join(app_dir, "src")
    os.makedirs(src_dir, exist_ok=True)

    with open(os.path.join(src_dir, "main.c"), "w") as fh:
        fh.write(MAIN_C_TMPL % app)
    with open(os.path.join(app_dir, "Makefile"), "w") as fh:
        fh.write(MAKEFILE_TMPL % {"app": app})
    with open(os.path.join(app_dir, "env.sh"), "w") as fh:
        fh.write(ENV_SH_TMPL)
    with open(os.path.join(app_dir, "build.sh"), "w") as fh:
        fh.write(BUILD_SH_TMPL)
    os.chmod(os.path.join(app_dir, "env.sh"), 0o755)
    os.chmod(os.path.join(app_dir, "build.sh"), 0o755)
    meta = {
        "name": app,
        "sdk": "yocto-h1",
        "board": "QCS6490",
        "target": "qcm6490-idp",
        "toolchain": "eSDK (environment-setup-armv8-2a-qcom-linux)",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(os.path.join(app_dir, "qpi_app.json"), "w") as fh:
        json.dump(meta, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    with open(os.path.join(app_dir, "%s.code-workspace" % app), "w") as fh:
        fh.write(CODE_WORKSPACE_TMPL % {"app": app})

    # 接入 Yocto: 用户 layer + recipe, 并追加到镜像 IMAGE_INSTALL
    image = _target_image(ctx)
    layer_dir = _ensure_app_layer(ctx)
    recipe = _write_app_recipe(layer_dir, app)
    bbappend = _append_image_install(layer_dir, image, app)

    info("已创建应用工程: %s" % app_dir)
    print("  src/main.c / Makefile / env.sh / build.sh / qpi_app.json")
    print("  编译: qpi-cli build-app %s" % app)
    print("  首次使用需先生成 eSDK (见 env.sh 内提示), 或设置 QPI_ESDK_ENV")
    print("  打开: code %s" % os.path.join(app_dir, "%s.code-workspace" % app))
    info("已接入镜像 %s (bitbake 时自动交叉编译并打包)" % image)
    print("  recipe : %s" % recipe)
    print("  bbappend: %s" % bbappend)
    print("  构建: qpi-cli build --os LINUX --ver STD")
    return 0


def cmd_build_app(args):
    ctx = args.ctx
    app = args.name
    projects_dir = ctx.p(dget(ctx.config, "projects_dir", "projects"))
    app_dir = os.path.join(projects_dir, app)
    if not os.path.isdir(app_dir):
        error("应用工程不存在: %s (先执行 qpi-cli new %s)" % (app_dir, app))
        return 1
    script = os.path.join(app_dir, "build.sh")
    if not os.path.isfile(script):
        error("%s 缺少 build.sh, 不是 qpi-cli 创建的应用工程" % app_dir)
        error("可重新生成: qpi-cli new %s --force" % app)
        return 1

    cmd = ["bash", script]
    if args.clean:
        cmd.append("clean")
    banner("交叉编译应用工程: %s" % app)
    print("  cwd : %s" % app_dir)
    print("  cmd : %s" % " ".join(cmd))
    if args.dry_run:
        return 0
    return subprocess.call(cmd, cwd=app_dir)


def cmd_ls_projects(args):
    ctx = args.ctx
    projects_dir = ctx.p(dget(ctx.config, "projects_dir", "projects"))
    if not os.path.isdir(projects_dir):
        info("还没有应用工程目录: %s" % projects_dir)
        return 0
    names = sorted(
        d
        for d in os.listdir(projects_dir)
        if os.path.isdir(os.path.join(projects_dir, d))
    )
    if args.json:
        print(json.dumps(names, ensure_ascii=False))
        return 0
    if not names:
        info("还没有应用工程: %s" % projects_dir)
        return 0
    banner("应用工程 (%s)" % projects_dir)
    for n in names:
        print("  %s" % n)
    return 0


def _dir_size_mb(path):
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for f in files:
            fp = os.path.join(dirpath, f)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total / 1024.0 / 1024.0


def cmd_ls_versions(args):
    ctx = args.ctx
    dirs = ctx.rev_dirs()
    cur_dir = ctx.current_fw_name()

    rows = []
    for name, path, state in dirs:
        rows.append(
            {
                "name": name,
                "path": path,
                "state": state,
                "size_mb": round(_dir_size_mb(path), 1),
                "mtime": time.strftime(
                    "%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(path))
                ),
                "current": name == cur_dir,
            }
        )

    cur_state = ctx.rev_state(cur_dir)

    if args.json:
        print(
            json.dumps(
                {"current_rev": cur_dir, "current_state": cur_state, "versions": rows},
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0

    if not rows:
        info("还没有任何固件 (quectel_build/ 下没有版本目录)")
        info("当前 buildconfig rev: %s — %s" % (cur_dir, cur_state))
        return 0

    banner("固件版本状态 (共 %d 个)" % len(rows))
    for r in rows:
        mark = "*" if r["current"] else " "
        print(
            "%s %-56s %-14s %8.1f MB  %s"
            % (mark, r["name"], r["state"], r["size_mb"], r["mtime"])
        )
    print()
    print("  * = 与当前 buildconfig 匹配的版本")
    print("  当前 buildconfig rev: %s — %s" % (cur_dir, cur_state))
    return 0


def cmd_setup(args):
    """首次宿主环境安装: 调用 SDK 根下的 install_build_env.sh。"""
    ctx = args.ctx
    env_script = ctx.p(INSTALL_ENV_SH)
    if not os.path.isfile(env_script):
        error("未找到 %s" % env_script)
        error(
            "install_build_env.sh 是对外仓库的首次宿主环境安装入口, "
            "请确认 SDK 根目录正确 (可用 --sdk / QPI_SDK_ROOT 指定)"
        )
        return 1
    extra = list(getattr(args, "extra", None) or [])
    # argparse REMAINDER 会保留分隔用的 "--", 去掉它再透传给脚本
    if extra and extra[0] == "--":
        extra = extra[1:]

    if args.dry_run:
        banner("dry-run: 将要执行的环境安装命令")
        print("  cd %s" % shlex.quote(ctx.root))
        print(
            "  bash %s %s"
            % (shlex.quote(env_script), " ".join(shlex.quote(a) for a in extra))
        )
        return 0

    info("安装宿主构建环境: bash %s %s" % (INSTALL_ENV_SH, " ".join(extra)))
    try:
        proc = subprocess.run(["bash", env_script] + extra, cwd=ctx.root)
        return proc.returncode
    except OSError as exc:
        error("执行 %s 失败: %s" % (env_script, exc))
        return 1


# ------------------------------------------------------------- doctor 预检 ---
def _read_os_release():
    data = {}
    try:
        with open("/etc/os-release", "r") as fh:
            for line in fh:
                line = line.strip()
                if not line or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                data[k.strip()] = v.strip().strip('"')
    except OSError:
        pass
    return data


def _read_meminfo():
    data = {}
    try:
        with open("/proc/meminfo", "r") as fh:
            for line in fh:
                if ":" not in line:
                    continue
                k, v = line.split(":", 1)
                parts = v.strip().split()
                if parts:
                    try:
                        data[k.strip()] = int(parts[0])  # kB
                    except ValueError:
                        pass
    except OSError:
        pass
    return data


def _check_distro():
    rel = _read_os_release()
    if not rel:
        return "WARN", "无法读取 /etc/os-release", "推荐 Ubuntu 22.04 LTS 宿主"
    distro = rel.get("ID", "?").lower()
    ver = rel.get("VERSION_ID", "?")
    pretty = rel.get("PRETTY_NAME", distro)
    if distro == "ubuntu" and ver.startswith("22.04"):
        return "PASS", pretty, "推荐版本, 与 SDK 验证环境一致"
    if distro in ("ubuntu", "debian"):
        return "WARN", pretty, "推荐 Ubuntu 22.04 LTS, 请自行确认依赖兼容"
    return "FAIL", pretty, "SDK 仅验证 Ubuntu 22.04 LTS, 其他发行版可能失败"


def _check_disk(root):
    try:
        free = shutil.disk_usage(root).free
    except OSError as exc:
        return "WARN", "无法获取磁盘信息 (%s)" % exc, "确保工作分区可用空间 ≥300GB"
    gb = free / 1024.0 / 1024.0 / 1024.0
    human = "%.1f GB 可用 (%s)" % (gb, root)
    if gb >= 300:
        return "PASS", human, "满足 ≥300GB 建议"
    if gb >= 50:
        return "WARN", human, "BitBake 构建建议预留 ≥300GB 可用空间"
    return "FAIL", human, "可用空间严重不足, 建议 ≥300GB"


def _check_swap():
    mem = _read_meminfo()
    swap_kb = mem.get("SwapTotal")
    if swap_kb is None:
        return "WARN", "无法读取 /proc/meminfo", "建议配置 >32GB swap"
    gb = swap_kb / 1024.0 / 1024.0
    human = "%.1f GB" % gb
    if gb > 32:
        return "PASS", human, "满足 >32GB 建议"
    if gb > 0:
        return "WARN", human, "Swap 偏小, BitBake 建议 >32GB"
    return "WARN", human, "未检测到 Swap, BitBake 并行构建建议配置 >32GB"


def _check_memory():
    mem = _read_meminfo()
    total_kb = mem.get("MemTotal")
    if total_kb is None:
        return "WARN", "无法读取 /proc/meminfo", "建议 ≥16GB 内存"
    gb = total_kb / 1024.0 / 1024.0
    human = "%.1f GB" % gb
    if gb >= 16:
        return "PASS", human, "满足 ≥16GB 建议"
    return "WARN", human, "内存偏小, 构建可能较慢, 建议 ≥16GB"


def _check_userns():
    """检查 BitBake 用户命名空间可用性 (Ubuntu 23.10+ 限制)。"""
    clone = None
    maxns = None
    try:
        with open("/proc/sys/kernel/unprivileged_userns_clone", "r") as fh:
            clone = int(fh.read().strip())
    except (OSError, ValueError):
        clone = None
    try:
        with open("/proc/sys/user/max_user_namespaces", "r") as fh:
            maxns = int(fh.read().strip())
    except (OSError, ValueError):
        maxns = None

    if clone == 0:
        return (
            "FAIL",
            "kernel.unprivileged_userns_clone=0",
            "BitBake 用户命名空间不可用, 请在 build.sh 前执行 relax_userns_for_bitbake",
        )
    if maxns is not None and maxns == 0:
        return (
            "FAIL",
            "user.max_user_namespaces=0",
            "BitBake 用户命名空间不可用, 请在 build.sh 前执行 relax_userns_for_bitbake",
        )
    if clone is None and maxns is None:
        return (
            "WARN",
            "无法读取 userns 相关 sysctl",
            "若在 Ubuntu 23.10+ 上构建失败, 请执行 relax_userns_for_bitbake",
        )
    detail = "unprivileged_userns_clone=%s max_user_namespaces=%s" % (
        "n/a" if clone is None else clone,
        "n/a" if maxns is None else maxns,
    )
    return "PASS", detail, "BitBake 用户命名空间可用"


def cmd_doctor(args):
    """只读预检: 不改动系统, 逐项给出 PASS/WARN/FAIL 与建议。"""
    ctx = args.ctx
    banner("qpi-cli doctor — 宿主环境预检 (只读)")
    print("  SDK root: %s\n" % ctx.root)

    results = []

    state, detail, hint = _check_distro()
    verdict(state, "发行版 / 版本", "%s — %s" % (detail, hint))
    results.append(state)

    state, detail, hint = _check_disk(ctx.root)
    verdict(state, "可用磁盘 (≥300GB)", "%s — %s" % (detail, hint))
    results.append(state)

    state, detail, hint = _check_swap()
    verdict(state, "Swap (>32GB)", "%s — %s" % (detail, hint))
    results.append(state)

    state, detail, hint = _check_memory()
    verdict(state, "内存", "%s — %s" % (detail, hint))
    results.append(state)

    has_build = os.path.isfile(ctx.p(BUILD_SH))
    verdict(
        "PASS" if has_build else "FAIL",
        "构建入口 build.sh",
        ctx.p(BUILD_SH) if has_build else "缺失: %s" % ctx.p(BUILD_SH),
    )
    results.append("PASS" if has_build else "FAIL")

    has_ver = os.path.isfile(ctx.p(VERSION_TXT))
    verdict(
        "PASS" if has_ver else "WARN",
        "平台标识 VERSION.txt",
        ctx.p(VERSION_TXT) if has_ver else "缺失 (可选, 建议拉取完整仓库)",
    )
    results.append("PASS" if has_ver else "WARN")

    has_install = os.path.isfile(ctx.p(INSTALL_ENV_SH))
    verdict(
        "PASS" if has_install else "WARN",
        "环境安装 install_build_env.sh",
        ctx.p(INSTALL_ENV_SH)
        if has_install
        else "缺失 (首次安装入口, 可用 qpi-cli setup 定位)",
    )
    results.append("PASS" if has_install else "WARN")

    entry_err = flash_entry_error(configured_flash_entry(ctx))
    entry = resolve_flash_entry(ctx)
    entry_abs = ctx.p(entry)
    if entry_err:
        verdict("FAIL", "烧录入口 FLASH_ENTRY", entry_err)
        results.append("FAIL")
    elif not os.path.isfile(entry_abs):
        verdict(
            "WARN",
            "烧录入口 FLASH_ENTRY",
            "缺失: %s (FLASH_ENTRY 与回落路径均不存在)" % entry_abs,
        )
        results.append("WARN")
    elif entry_abs.lower().endswith(".sh") and not os.access(entry_abs, os.X_OK):
        verdict("WARN", "烧录入口 FLASH_ENTRY", "存在但不可执行: %s" % entry_abs)
        results.append("WARN")
    else:
        verdict("PASS", "烧录入口 FLASH_ENTRY", entry_abs)
        results.append("PASS")

    state, detail, hint = _check_userns()
    verdict(state, "BitBake 用户命名空间", "%s — %s" % (detail, hint))
    results.append(state)

    n_pass = results.count("PASS")
    n_warn = results.count("WARN")
    n_fail = results.count("FAIL")
    print()
    banner("总结: %d PASS / %d WARN / %d FAIL" % (n_pass, n_warn, n_fail))
    if n_fail:
        error("存在 FAIL 项, 建议修复后再开始构建")
        return 1
    if n_warn:
        warn("存在 WARN 项, 可视情况继续")
    else:
        info("预检全部通过")
    return 0


def cmd_build(args):
    ctx = args.ctx
    project, rev, os_dim, ver_dim, sec = parse_dimensions(args)

    cfg_cmd = "buildconfig %s %s %s %s" % (
        shlex.quote(project),
        shlex.quote(rev),
        shlex.quote(os_dim),
        shlex.quote(ver_dim),
    )
    if sec:
        cfg_cmd += " SEC"
    body = "\n".join([cfg_cmd, "buildall"])

    banner("构建计划 (Build plan)")
    print("  project : %s" % project)
    print("  rev     : %s" % rev)
    print("  OS      : %s" % os_dim)
    print("  VERSION : %s%s" % (ver_dim, " + SECBOOT" if sec else ""))
    print("  target  : %s" % ctx.sdk_meta().get("TARGET_IMAGE", "quecpi-image"))
    print()
    info("运行: %s && buildall (完整 bitbake 构建, 耗时较长)" % cfg_cmd)

    rc = run_sdk(body, ctx.root, verbose=args.verbose, dry_run=args.dry_run)
    if rc == 0 and not args.dry_run:
        dset(ctx.config, "build.project", project)
        dset(ctx.config, "build.rev", rev)
        dset(ctx.config, "build.os", os_dim)
        dset(ctx.config, "build.version", ver_dim)
        ctx.save_config()
        info("构建完成, 配置已回写: %s" % ctx.config_path)
    return rc


def cmd_sdk(args):
    ctx = args.ctx
    func = "buildesdk" if args.ext else "buildsdk"
    banner("导出交叉编译 SDK (%s)" % func)
    info("运行: %s (bitbake populate_sdk, 耗时较长)" % func)
    info("产物为 *_SDK.tar, 解压后执行其中 setup_sdk.sh 安装")
    info("安装后可执行: qpi-cli build-app <app>")
    return run_sdk(func, ctx.root, verbose=args.verbose, dry_run=args.dry_run)


def cmd_package(args):
    ctx = args.ctx
    # buildpackage 依赖 QUECTEL_PROJECT_* 环境变量, 而 source build.sh 并不会设置
    # 它们 (只有 buildconfig/buildenv 才会)。这里直接从 header 读出并显式导出,
    # 避免版本号为空导致 a_key_generation 把产物落到 quectel_build/ 根目录。
    hdr = ctx.current_header()
    project = hdr.get("QUECTEL_PROJECT_NAME") or dget(
        ctx.config, "build.project", "QSM565DWF"
    )
    rev = hdr.get("QUECTEL_PROJECT_REV", "")
    custom = hdr.get("QUECTEL_CUSTOM_NAME") or dget(
        ctx.config, "build.version", "STD"
    )
    if not rev:
        error("无法从 %s 读取 QUECTEL_PROJECT_REV" % ctx.p(GEN_HEADER))
        warn("请先执行 qpi-cli build, 或在 SDK 环境里执行 buildconfig")
        return 1

    body = "\n".join(
        [
            "export QUECTEL_PROJECT_NAME=%s" % shlex.quote(project),
            "export QUECTEL_PROJECT_REV=%s" % shlex.quote(rev),
            "export QUECTEL_CUSTOM_NAME=%s" % shlex.quote(custom),
            "buildpackage",
        ]
    )

    info("打包固件 (project=%s rev=%s custom=%s)" % (project, rev, custom))
    rc = run_sdk(body, ctx.root, verbose=args.verbose, dry_run=args.dry_run)
    if rc == 0 and not args.dry_run:
        name = fw_dir_name(rev, custom)
        out = ctx.p("quectel_build", name)
        if os.path.isdir(out) and has_fw_fingerprint(out):
            info("固件输出目录: %s" % out)
            info("烧录: qpi-cli flash ufs")
        else:
            warn("打包命令已返回, 但未检测到可烧录固件: %s" % out)
    return rc


def _resolve_fw_dir(ctx, value):
    """--fw-dir 支持绝对/相对路径, 也支持 quectel_build/ 下的目录名。"""
    expanded = os.path.abspath(os.path.expanduser(value))
    if os.path.isdir(expanded):
        return expanded
    cand = ctx.p("quectel_build", value)
    if os.path.isdir(cand):
        return cand
    return expanded


def _report_available_fw(ctx, storage):
    """固件不可烧录时, 列出 quectel_build/ 下现成可烧录的包。"""
    name, _path = ctx.current_fw_dir()
    info("当前 buildconfig 对应目录: %s" % (name or "<header 不可解析>"))
    dirs = ctx.packaged_fw_dirs()
    if not dirs:
        warn("quectel_build/ 下没有已打包的固件")
        info("先打包: qpi-cli package")
        return
    info("已打包可烧录的固件:")
    for n, _p in dirs:
        print("    %s%s" % (n, "  (当前)" if n == name else ""))
    print()
    print("  烧录现成固件: qpi-cli flash %s --fw-dir %s" % (storage, dirs[0][0]))


def cmd_flash(args):
    ctx = args.ctx
    storage = args.storage or "ufs"

    entry_err = flash_entry_error(configured_flash_entry(ctx))
    if entry_err:
        error(entry_err)
        return 1
    entry = resolve_flash_entry(ctx)
    entry_abs = ctx.p(entry)
    if not os.path.isfile(entry_abs):
        error("烧录入口脚本不存在: %s" % entry_abs)
        warn("检查 tools/VERSION.txt 的 FLASH_ENTRY 或回落路径 %s" % FLASH_SH)
        return 1

    # 固件目录预检: 提前给出可执行的建议, 而不是让 flash.sh 在末尾报难懂的错。
    fw_name, fw_path = ctx.current_fw_dir()
    if getattr(args, "fw_dir", None):
        fw_path = _resolve_fw_dir(ctx, args.fw_dir)
        fw_name = os.path.basename(fw_path.rstrip(os.sep))
    problem = fw_dir_problem(fw_path)
    if problem:
        error("固件目录不可烧录: %s (%s)" % (fw_path or "<空>", problem))
        _report_available_fw(ctx, storage)
        if getattr(args, "fw_dir", None):
            return 1
        info("或先打包当前 buildconfig: qpi-cli package")
        if not args.dry_run:
            return 1
        warn("dry-run: 继续打印烧录计划; 实际执行会在此退出")

    info("烧录固件 (storage=%s)" % storage)
    print("  entry   : %s" % entry_abs)
    print("  firmware: %s (%s)" % (fw_path, fw_name))
    if args.dry_run:
        banner("dry-run: 将要执行的烧录命令")
        print("  cd %s" % shlex.quote(ctx.root))
        print("  QPI_FW_DIR=%s" % shlex.quote(fw_path))
        print("  source %s" % shlex.quote(BUILD_SH))
        print("  flash %s" % shlex.quote(storage))
        print("  # FLASH_ENTRY 已解析为: %s" % entry_abs)
        return 0
    return run_sdk(
        "flash %s" % shlex.quote(storage),
        ctx.root,
        verbose=args.verbose,
        dry_run=False,
        extra_env={"QPI_FW_DIR": fw_path},
    )


def cmd_kernel(args):
    ctx = args.ctx
    if args.menuconfig:
        body = "bitbake -c menuconfig virtual/kernel"
        info("打开内核 menuconfig")
    elif args.dtb:
        body = "builddtb"
        info("重编 DTB (builddtb)")
    else:
        body = "buildkernel"
        info("重编内核 (buildkernel)")
    return run_sdk(body, ctx.root, verbose=args.verbose, dry_run=args.dry_run)


def cmd_clean(args):
    ctx = args.ctx
    targets = []
    if args.packages:
        targets += [path for _name, path in ctx.firmware_dirs()]
    if args.build:
        targets.append(ctx.p(BUILD_DIR, "tmp-glibc"))

    if not targets:
        info(
            "未选择清理对象。选项: --packages (已打包固件) / --build (bitbake tmp 目录)"
        )
        info("示例: qpi-cli clean --packages --yes")
        return 0

    banner("即将删除 (%d 项)" % len(targets))
    for t in targets:
        print("  %s" % t)

    if not args.yes:
        warn("该操作具有破坏性; 追加 --yes 以确认")
        return 1

    for t in targets:
        if os.path.exists(t):
            shutil.rmtree(t, ignore_errors=True)
            info("已删除 %s" % t)
    return 0
