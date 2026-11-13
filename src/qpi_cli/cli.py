# -*- coding: utf-8 -*-
"""qpi-cli 参数解析与控制台入口。"""

import argparse
import sys

from . import CLI_NAME, __version__
from .commands import (
    cmd_build,
    cmd_clean,
    cmd_config,
    cmd_doctor,
    cmd_flash,
    cmd_kernel,
    cmd_ls_projects,
    cmd_ls_versions,
    cmd_new,
    cmd_package,
    cmd_setup,
    cmd_version,
)
from .core import BUILD_SH, Ctx, error, find_sdk_root, warn


def build_parser():
    parser = argparse.ArgumentParser(
        prog=CLI_NAME,
        description="QuecPi QCS6490 (PI H1) Yocto Linux SDK 命令行 (对外独立版, Linux 宿主)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
        epilog="示例:\n"
        "  qpi-cli doctor\n"
        "  qpi-cli setup\n"
        "  qpi-cli version\n"
        "  qpi-cli build --os LINUX --ver STD\n"
        "  qpi-cli package && qpi-cli flash ufs\n",
    )
    parser.add_argument(
        "--sdk", help="SDK 根目录 (默认自动探测, 或设置 QPI_SDK_ROOT)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="只打印将要执行的命令, 不真正执行"
    )
    parser.add_argument(
        "--verbose", action="store_true", help="显示 SDK 环境脚本输出"
    )
    parser.add_argument(
        "--version", action="version", version="%s %s" % (CLI_NAME, __version__)
    )

    # 全局开关同时挂到每个子命令上, 使 `qpi-cli build --dry-run` 与
    # `qpi-cli --dry-run build` 两种写法都可用 (SUPPRESS 避免覆盖主解析结果)。
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--sdk", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    common.add_argument(
        "--dry-run",
        action="store_true",
        default=argparse.SUPPRESS,
        help="只打印将要执行的命令, 不真正执行",
    )
    common.add_argument(
        "--verbose",
        action="store_true",
        default=argparse.SUPPRESS,
        help="显示 SDK 环境脚本输出",
    )

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("version", parents=[common], help="显示 CLI 与 SDK 信息")

    sub.add_parser(
        "doctor",
        parents=[common],
        help="只读预检宿主环境 (发行版/磁盘/swap/内存/依赖/userns)",
    )

    p_setup = sub.add_parser(
        "setup",
        parents=[common],
        help="安装宿主构建环境 (调用 install_build_env.sh)",
    )
    p_setup.add_argument(
        "extra",
        nargs=argparse.REMAINDER,
        help="透传给 install_build_env.sh 的参数 (示例: setup -- --help)",
    )

    p_cfg = sub.add_parser(
        "config",
        parents=[common],
        help="显示/初始化配置 (~/.config/qpi-cli/config.json)",
    )
    p_cfg.add_argument("--init", action="store_true", help="写入默认配置")
    p_cfg.add_argument(
        "--force", action="store_true", help="配合 --init, 覆盖已存在的配置"
    )
    p_cfg.add_argument(
        "--get", metavar="KEY", help="读取配置项 (点分路径, 如 build.os)"
    )
    p_cfg.add_argument("--set", metavar="KEY=VALUE", action="append", help="写入配置项")

    p_new = sub.add_parser(
        "new", parents=[common], help="创建应用工程 (含 .code-workspace)"
    )
    p_new.add_argument("name", help="应用名")
    p_new.add_argument("--force", action="store_true", help="已存在时覆盖")

    p_lsp = sub.add_parser("ls-projects", parents=[common], help="列出应用工程")
    p_lsp.add_argument("-j", "--json", action="store_true", help="JSON 输出")

    p_lsv = sub.add_parser(
        "ls-versions", parents=[common], help="列出固件版本及状态 (三态)"
    )
    p_lsv.add_argument("-j", "--json", action="store_true", help="JSON 输出")

    p_build = sub.add_parser(
        "build", parents=[common], help="buildconfig + buildall (完整构建)"
    )
    p_build.add_argument("--project", help="工程名 (默认取配置)")
    p_build.add_argument("--rev", help="revision (默认取当前 header)")
    p_build.add_argument("--os", choices=["LINUX", "DEBIAN", "UBUNTU"], help="OS 维度")
    p_build.add_argument(
        "--variant", "--ver", dest="variant", choices=["STD", "DBG"], help="发布维度"
    )
    p_build.add_argument("--sec", action="store_true", help="启用安全启动 (SEC)")

    sub.add_parser(
        "package", parents=[common], help="a_key_generation: 生成可烧录固件包"
    )

    p_flash = sub.add_parser("flash", parents=[common], help="烧录固件到开发板")
    p_flash.add_argument("storage", nargs="?", choices=["ufs", "emmc"], default="ufs")

    p_kern = sub.add_parser("kernel", parents=[common], help="内核相关操作")
    p_kern.add_argument("--dtb", action="store_true", help="仅重编 DTB")
    p_kern.add_argument("--menuconfig", action="store_true", help="打开内核 menuconfig")

    p_clean = sub.add_parser("clean", parents=[common], help="清理构建/固件产物")
    p_clean.add_argument("--packages", action="store_true", help="删除已打包固件目录")
    p_clean.add_argument(
        "--build", action="store_true", help="删除 build-qcom-wayland/tmp-glibc"
    )
    p_clean.add_argument("--yes", action="store_true", help="确认删除")

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    root = find_sdk_root(args.sdk)
    if root is None:
        error(
            "未找到 SDK 根目录 (需包含 %s)。请用 --sdk 或设置 QPI_SDK_ROOT。"
            % BUILD_SH
        )
        return 2
    args.ctx = Ctx(root)

    handlers = {
        "version": cmd_version,
        "doctor": cmd_doctor,
        "setup": cmd_setup,
        "config": cmd_config,
        "new": cmd_new,
        "ls-projects": cmd_ls_projects,
        "ls-versions": cmd_ls_versions,
        "build": cmd_build,
        "package": cmd_package,
        "flash": cmd_flash,
        "kernel": cmd_kernel,
        "clean": cmd_clean,
    }
    try:
        return handlers[args.command](args)
    except KeyboardInterrupt:
        warn("已中断")
        return 130
