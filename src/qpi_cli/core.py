# -*- coding: utf-8 -*-
"""qpi-cli 核心: 输出辅助 / SDK 根定位 / 配置读写 / SDK 环境内执行。"""

import fnmatch
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile

# ---- SDK 内部相对路径 (全部相对 SDK 根目录) --------------------------------
BUILD_SH = "quectel_build/compile/build.sh"
GEN_HEADER = "quectel_build/compile/quectel-features-config/quectel-buildconfig-gen.h"
FLASH_SH = "quectel_build/tools/flash.sh"
VERSION_TXT = "tools/VERSION.txt"
INSTALL_ENV_SH = "install_build_env.sh"
BUILD_DIR = "build-qcom-wayland"

# 固件版本目录的"打包指纹": buildpackage 会往版本目录里放这些 (支持通配)
FW_MARKER_PATTERNS = (
    "rawprogram*.xml",
    "prog_firehose*.elf",
    "partition*.xml",
    "contents.xml",
)

# quectel_build/ 下的基础设施目录, 一律不计入"固件版本目录"
INFRA_DIRS = {
    "compile",
    "tools",
    "output",
    "downloads",
    "sstate-cache",
    "BP",
    "build-qcom-wayland",
    "conf",
    "sources",
    "tmp",
    "logs",
}

DEFAULT_REV = "SG565DWFPARL1A01_BL01BP01K0M01_QDP_LP6.6.0XX.01.00X_V0X"


def has_fw_fingerprint(path):
    """目录 (仅顶层) 是否含任一打包指纹文件。"""
    try:
        names = os.listdir(path)
    except OSError:
        return False
    for name in names:
        for pat in FW_MARKER_PATTERNS:
            if fnmatch.fnmatch(name, pat):
                return True
    return False


# ---------------------------------------------------------------- 输出辅助 ---
def _color(code, text):
    if sys.stdout.isatty() and os.environ.get("NO_COLOR") is None:
        return "\033[%sm%s\033[0m" % (code, text)
    return text


def info(msg):
    print("%s %s" % (_color("32;1", "[INFO]"), msg), flush=True)


def warn(msg):
    print(
        "%s %s" % (_color("33;1", "[WARN]"), msg), file=sys.stderr, flush=True
    )


def error(msg):
    print(
        "%s %s" % (_color("31;1", "[ERROR]"), msg), file=sys.stderr, flush=True
    )


def banner(title):
    print(_color("36;1", "=== %s ===" % title), flush=True)


_VERDICT_COLOR = {"PASS": "32;1", "WARN": "33;1", "FAIL": "31;1"}


def verdict(state, label, msg):
    print(
        "  %s %-32s %s"
        % (_color(_VERDICT_COLOR[state], "[%s]" % state), label, msg)
    )


# ------------------------------------------------------------ SDK 根定位 ---
def _is_sdk_root(path):
    """硬条件: 目录下存在 quectel_build/compile/build.sh。

    tools/VERSION.txt 降级为可选 (仅作为辅助信息, 不作必要条件)。
    """
    return os.path.isfile(os.path.join(path, BUILD_SH))


def find_sdk_root(start=None):
    """定位 SDK 根目录。

    候选顺序: $QPI_SDK_ROOT -> --sdk/start -> 当前目录及其各级父目录。
    判定硬条件是存在 build.sh; 额外优先接受同时存在 install_build_env.sh 的
    目录 (对外仓库的首次环境安装入口, 可提高判定置信度)。
    """
    cand = []
    if os.environ.get("QPI_SDK_ROOT"):
        cand.append(os.environ["QPI_SDK_ROOT"])
    if start:
        cand.append(start)
    cur = os.getcwd()
    for _ in range(6):
        cand.append(cur)
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent

    best = None
    for c in cand:
        if not c or not os.path.isdir(c):
            continue
        if _is_sdk_root(c):
            # 同时具备 install_build_env.sh 视为强信号, 直接采用
            if os.path.isfile(os.path.join(c, INSTALL_ENV_SH)):
                return os.path.abspath(c)
            if best is None:
                best = os.path.abspath(c)
    return best


# --------------------------------------------------------------- 配置读写 ---
def user_config_path():
    """用户级配置路径 (默认落盘位置, 不写入用户 clone 的 SDK)。"""
    return os.path.join(
        os.path.expanduser("~"), ".config", "qpi-cli", "config.json"
    )


def resolve_config_path(root):
    """解析配置路径, 返回 (path, read_only)。

    优先级: $QPI_CONFIG > SDK 根下 .qpi_config.json (只读覆盖) > 用户级路径。
    """
    env = os.environ.get("QPI_CONFIG")
    if env:
        return os.path.abspath(os.path.expanduser(env)), False
    sdk_override = os.path.join(root, ".qpi_config.json")
    if os.path.isfile(sdk_override):
        return sdk_override, True
    return user_config_path(), False


def default_config():
    return {
        "schema": 1,
        "sdk": {"name": "yocto-h1", "type": "yocto"},
        "build": {
            "project": "QSM565DWF",
            "rev": DEFAULT_REV,
            "os": "LINUX",
            "version": "STD",
        },
    }


def dget(d, path, default=None):
    cur = d
    for k in path.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def dset(d, path, value):
    keys = path.split(".")
    cur = d
    for k in keys[:-1]:
        cur = cur.setdefault(k, {})
    cur[keys[-1]] = value


class Ctx:
    def __init__(self, root):
        self.root = root
        self.config_path, self.read_only = resolve_config_path(root)
        self.config = self._load_config()

    def _load_config(self):
        if os.path.isfile(self.config_path):
            try:
                with open(self.config_path, "r") as fh:
                    cfg = json.load(fh)
                base = default_config()
                # 浅合并, 缺字段时回落到默认值
                for k, v in base.items():
                    cfg.setdefault(k, v)
                return cfg
            except (ValueError, OSError) as exc:
                warn(
                    "读取 %s 失败 (%s), 回落到默认配置"
                    % (self.config_path, exc)
                )
        return default_config()

    def save_config(self):
        path = self.config_path
        if self.read_only:
            # SDK 根下的 .qpi_config.json 仅作为只读覆盖, 改写落到用户级
            path = user_config_path()
            warn(
                "%s 是 SDK 根下只读覆盖配置, 改写用户级配置: %s"
                % (self.config_path, path)
            )
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w") as fh:
            json.dump(self.config, fh, indent=2, ensure_ascii=False)
            fh.write("\n")

    def p(self, *parts):
        return os.path.join(self.root, *parts)

    # ---- SDK 元信息 ----
    def sdk_meta(self):
        """解析 tools/VERSION.txt。缺文件/缺键都不报错, 缺失键由调用方降级。"""
        meta = {}
        path = self.p(VERSION_TXT)
        if not os.path.isfile(path):
            return meta
        try:
            with open(path, "r") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    meta[k.strip()] = v.strip()
        except OSError:
            return {}
        return meta

    def current_header(self):
        """解析 quectel-buildconfig-gen.h, 得到当前 buildconfig 的落盘值。

        文件缺失或键缺失都安全降级, 不会抛异常。
        """
        out = {}
        path = self.p(GEN_HEADER)
        if not os.path.isfile(path):
            return out
        pat = re.compile(r'^#define\s+(QUECTEL_\w+)\s+"([^"]*)"')
        try:
            with open(path, "r") as fh:
                for line in fh:
                    m = pat.match(line.strip())
                    if m:
                        out[m.group(1)] = m.group(2)
        except OSError:
            return {}
        return out

    def current_rev(self):
        hdr = self.current_header()
        return hdr.get("QUECTEL_PROJECT_REV") or dget(
            self.config, "build.rev", DEFAULT_REV
        )

    def current_custom(self):
        hdr = self.current_header()
        return hdr.get("QUECTEL_CUSTOM_NAME") or dget(
            self.config, "build.version", "STD"
        )

    # ---- 固件版本目录 (三态) ----
    def rev_candidate_names(self):
        """当前 buildconfig 对应的候选版本目录名 (rev 与 rev_DBG)。"""
        rev = self.current_rev()
        return {rev, rev + "_DBG"}

    def rev_dirs(self):
        """枚举 quectel_build/ 下的固件版本目录及其状态。

        候选判定 (满足任一即为版本目录, 其余目录一律排除):
          1) 目录内 (顶层) 存在任一打包指纹文件 -> "已打包(可烧录)";
          2) 目录名等于当前 buildconfig 的 rev 或 rev_DBG (目录存在即算),
             有指纹即"已打包(可烧录)", 无指纹即"已构建未打包"。

        返回 [(name, path, state)]; 全新 clone (目录不存在/为空) 返回空列表, 不报错。
        """
        base = self.p("quectel_build")
        found = []
        if not os.path.isdir(base):
            return found
        rev_names = self.rev_candidate_names()
        try:
            entries = sorted(os.listdir(base))
        except OSError:
            return found
        for name in entries:
            path = os.path.join(base, name)
            if not os.path.isdir(path):
                continue
            if name.startswith(".") or name in INFRA_DIRS:
                continue
            packaged = has_fw_fingerprint(path)
            if not packaged and name not in rev_names:
                continue
            state = "已打包(可烧录)" if packaged else "已构建未打包"
            found.append((name, path, state))
        return found

    def rev_state(self, name):
        """按目录名判定单个版本的三态 (供 '未构建' 场景使用)。"""
        path = self.p("quectel_build", name)
        if not os.path.isdir(path):
            return "未构建"
        if has_fw_fingerprint(path):
            return "已打包(可烧录)"
        return "已构建未打包"

    def firmware_dirs(self):
        """仅返回已打包 (含指纹文件) 的版本目录, 供 clean 使用。"""
        return [
            (name, path)
            for name, path, state in self.rev_dirs()
            if state == "已打包(可烧录)"
        ]


# ------------------------------------------------------- SDK 环境内执行 -----
def run_sdk(cmd_body, sdk_root, verbose=False, dry_run=False):
    """在 source 过 build.sh 的 bash 环境里执行 cmd_body。

    build.sh 是 source 型脚本: 它会设置 TOPDIR/BUILDDIR 等变量、定义
    buildconfig/buildall/buildpackage/flash 等函数。所以这里生成一段临时
    bash, 先 source 再把调用体接在后面。
    """
    lines = [
        "#!/bin/bash",
        "cd %s || exit 1" % shlex.quote(sdk_root),
        "source %s %s || { echo '[qpi-cli] ERROR: source build.sh failed' >&2; exit 1; }"
        % (shlex.quote(BUILD_SH), ">/dev/null" if not verbose else ""),
        "set -e",
        cmd_body,
    ]
    script = "\n".join(lines) + "\n"

    if dry_run:
        banner("dry-run: 将要执行的 SDK 环境脚本")
        print(script, flush=True)
        return 0

    if verbose:
        banner("SDK environment script")
    sys.stdout.flush()
    sys.stderr.flush()

    fd, tmp = tempfile.mkstemp(prefix="qpi-cli-", suffix=".sh", text=True)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(script)
        proc = subprocess.run(["bash", tmp], cwd=sdk_root)
        return proc.returncode
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


# ------------------------------------------------------------- flash 探测 ---
def configured_flash_entry(ctx):
    """tools/VERSION.txt 中配置的 FLASH_ENTRY (原始值, 可能不存在/为空)。"""
    return (ctx.sdk_meta().get("FLASH_ENTRY") or "").strip()


def resolve_flash_entry(ctx):
    """按配置探测烧录入口 (相对 SDK 根的路径)。

    读 tools/VERSION.txt 的 FLASH_ENTRY; 若该键缺失或其指向的文件不存在,
    回落到 quectel_build/tools/flash.sh。
    """
    entry = configured_flash_entry(ctx)
    if entry and os.path.isfile(ctx.p(entry)):
        return entry
    return FLASH_SH


def flash_entry_error(entry):
    """校验配置的烧录入口是否可用于本 (Linux 宿主) 版本; 返回错误信息或 None。"""
    if sys.platform.startswith("win"):
        return "当前版本仅支持 Linux 宿主 (检测到 Windows 平台)"
    if entry and entry.lower().endswith(".bat"):
        return "当前版本仅支持 Linux 宿主, 不支持 .bat 烧录入口: %s" % entry
    return None
