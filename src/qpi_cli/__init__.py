# -*- coding: utf-8 -*-
"""qpi-cli — QuecPi QCS6490 (PI H1) Yocto Linux SDK 命令行。

对 Quectel-Pi/Pi-H1-Linux-SDK 仓库做一层薄封装, 不改变任何构建逻辑。
真正的构建/打包/烧录仍由 quectel_build/compile/build.sh 里的函数完成:
    buildconfig / buildall / buildpackage / flash / buildenv / buildkernel / builddtb

只依赖 Python 3 标准库, 不引入任何第三方包。
"""

__version__ = "0.2.2"
CLI_NAME = "qpi-cli"

__all__ = ["__version__", "CLI_NAME", "main"]


def main(argv=None):
    """控制台入口 (qpi-cli / python -m qpi_cli)。"""
    from .cli import main as _main

    return _main(argv)
