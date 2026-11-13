# -*- coding: utf-8 -*-
"""qpi-cli new 子命令生成应用工程时使用的文件模板。"""

MAIN_C_TMPL = """#include <stdio.h>

int main(void)
{
    printf("Hello from %s on QuecPi QCS6490!\\n");
    return 0;
}
"""

MAKEFILE_TMPL = """# %(app)s — QuecPi QCS6490 application
# The cross toolchain comes from the SDK (eSDK); without it CC falls back to
# the host gcc and is only good for a syntax check.
CC      ?= aarch64-poky-linux-gcc
CFLAGS  ?= -O2 -Wall
TARGET   = %(app)s

all: $(TARGET)

$(TARGET): main.c
\t$(CC) $(CFLAGS) -o $@ $<

clean:
\trm -f $(TARGET)

.PHONY: all clean
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
