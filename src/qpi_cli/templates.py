# -*- coding: utf-8 -*-
"""qpi-cli new 子命令生成应用工程时使用的文件模板。"""

MAIN_C_TMPL = """#include <stdio.h>

int main(void)
{
    printf("Hello from %s on QuecPi QCS6490!\\n");
    return 0;
}
"""

# 交叉编译环境由 env.sh 从 SDK 的 eSDK 加载；未加载时直接报错，避免误用宿主 gcc。
MAKEFILE_TMPL = """# %(app)s — QuecPi QCS6490 application
ifeq ($(origin CC),default)
$(error 未初始化交叉编译环境: 请用 ./build.sh 或 qpi-cli build-app %(app)s)
endif

CFLAGS  ?= -O2 -Wall -Wextra
LDFLAGS ?=
TARGET   = %(app)s
SRCS     = $(wildcard src/*.c)

all: $(TARGET)

$(TARGET): $(SRCS)
\t$(CC) $(CFLAGS) $(LDFLAGS) -o $@ $(SRCS)

clean:
\trm -f $(TARGET)

install: $(TARGET)
\tinstall -d $(DESTDIR)/usr/bin
\tinstall -m 0755 $(TARGET) $(DESTDIR)/usr/bin/$(TARGET)

.PHONY: all clean install
"""

ENV_SH_TMPL = """#!/usr/bin/env bash
# 定位并加载 SDK eSDK 的交叉编译环境 (environment-setup-*)
# 用法: source env.sh  或  qpi-cli build-app <app>

_qpi_sdk_root() {
    local d
    d="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    while [ "$d" != "/" ]; do
        [ -f "$d/quectel_build/compile/build.sh" ] && { printf '%s' "$d"; return 0; }
        d="$(dirname "$d")"
    done
    return 1
}

_root="${QPI_SDK_ROOT:-}"
if [ -z "$_root" ]; then
    _root="$(_qpi_sdk_root)" || {
        echo "[env.sh] 未找到 SDK 根目录 (需包含 quectel_build/compile/build.sh)" >&2
        [ "${BASH_SOURCE[0]}" != "$0" ] && return 1 || exit 1
    }
fi

_env="${QPI_ESDK_ENV:-}"
if [ -z "$_env" ]; then
    for _f in \\
        "$_root"/environment-setup-* \\
        "$_root"/*/environment-setup-* \\
        "$_root"/sdk/environment-setup-*; do
        [ -f "$_f" ] && { _env="$_f"; break; }
    done
fi

if [ -z "$_env" ] || [ ! -f "$_env" ]; then
    cat >&2 <<EOF
[env.sh] 未找到交叉编译环境脚本 environment-setup-*。

先生成并安装 eSDK:
  cd $_root
  source quectel_build/compile/build.sh
  buildconfig <项目> <版本> <系统> <版本>   # 例: buildconfig QSM565DWF ... LINUX STD
  qpi-cli sdk                # 等价于 buildsdk
  tar xf <版本>_SDK.tar -C <安装目录>
  <安装目录>/<版本>_SDK/setup_sdk.sh

或直接指定: export QPI_ESDK_ENV=/path/to/environment-setup-armv8-2a-qcom-linux
EOF
    [ "${BASH_SOURCE[0]}" != "$0" ] && return 1 || exit 1
fi

# shellcheck disable=SC1090
. "$_env"
echo "[env.sh] 已加载: $_env"
echo "[env.sh] CC=${CC:-?}"
"""

BUILD_SH_TMPL = """#!/usr/bin/env bash
# 编译入口: 自动加载 eSDK 环境后执行 make
set -e
cd "$(dirname "$0")"
if ! source ./env.sh; then
    exit 1
fi
exec make "$@"
"""

CODE_WORKSPACE_TMPL = """{
  "folders": [
    { "name": "%(app)s", "path": "." },
    { "name": "sdk:quectel_build", "path": "../../quectel_build" },
    { "name": "sdk:layers", "path": "../../layers" }
  ],
  "settings": {
    "files.exclude": { "**/.git": true }
  }
}
"""

# 用户应用层: 自动注册到 bblayers.conf, recipe 用 externalsrc 指向 projects/<app>
LAYER_CONF_TMPL = """# qpi-cli 用户应用层
BBPATH .= ":${LAYERDIR}"
BBFILES += "${LAYERDIR}/recipes-*/*/*.bb \\
            ${LAYERDIR}/recipes-*/*/*.bbappend"

BBFILE_COLLECTIONS        += "qpi-apps"
BBFILE_PATTERN_qpi-apps := "^${LAYERDIR}/"
BBFILE_PRIORITY_qpi-apps = "10"

LAYERSERIES_COMPAT_qpi-apps = "nanbield scarthgap"
"""

RECIPE_TMPL = """SUMMARY = "%(app)s application"
LICENSE = "CLOSED"

inherit externalsrc

# 源码位于 SDK 的 projects 目录, bitbake 直接从该目录编译
EXTERNALSRC = "${WORKSPACE}/projects/%(app)s"

do_compile() {
    oe_runmake
}

do_install() {
    oe_runmake install DESTDIR=${D}
}
"""

