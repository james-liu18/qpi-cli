# qpi-cli 用户使用手册

**适用平台：** Linux 宿主（Ubuntu 22.04 LTS 为 SDK 验证环境）

qpi-cli 是 Quectel Pi H1（QCS6490）Yocto Linux SDK
（[Quectel-Pi/Pi-H1-Linux-SDK](https://github.com/Quectel-Pi/Pi-H1-Linux-SDK)）的命令行工具。
它对 SDK 自带的 `quectel_build/compile/build.sh` 做一层薄封装，完成**环境预检 → 环境安装 →
配置 → 构建 → 打包 → 烧录**的完整流程，不改变 SDK 的任何构建逻辑。

---

## 1. 前提条件

| 工具 | 说明 | 最低版本 |
|------|------|----------|
| Python | 运行 CLI 工具本身（仅用标准库） | 3.8+ |
| Git | 拉取 SDK 源码 | 2.40+ |
| Bash / coreutils | SDK 构建脚本依赖 | — |

qpi-cli 自身**不依赖任何第三方 Python 包**；真正的编译依赖由 SDK 的
`install_build_env.sh` 安装（见 `qpi-cli setup`）。

另外请确认宿主资源满足 SDK 构建要求（可用 `qpi-cli doctor` 逐项检查）：

- 磁盘可用空间 ≥ 300 GB（BitBake 构建产物很大）
- Swap > 32 GB、内存 ≥ 16 GB
- Ubuntu 22.04 LTS（其他发行版可能因依赖版本导致构建失败）

---

## 2. 安装

```bash
pip install qpi-cli
```

安装完成后 `qpi-cli` 命令即可在终端全局使用。

升级到最新版：

```bash
pip install --upgrade qpi-cli
```

推荐的隔离安装方式（不污染系统 Python）：

```bash
pipx install qpi-cli
```

验证安装：

```bash
qpi-cli --version
qpi-cli --help
```

---

## 3. 快速开始

```bash
# 1. 拉取 SDK
git clone https://github.com/Quectel-Pi/Pi-H1-Linux-SDK.git
cd Pi-H1-Linux-SDK

# 2. 宿主环境预检（只读，不改动系统）
qpi-cli doctor

# 3. 安装宿主构建环境（调用仓库里的 install_build_env.sh）
qpi-cli setup

# 4. 确认 SDK 与当前 buildconfig
qpi-cli version

# 5. 完整构建（bitbake，耗时较长）
qpi-cli build --os LINUX --ver STD

# 6. 打包出可烧录固件
qpi-cli package

# 7. 烧录到开发板
qpi-cli flash ufs
```

---

## 4. 命令一览

| 命令 | 作用 |
|------|------|
| `qpi-cli version` | 显示 CLI 版本与 SDK 元信息（平台 / buildconfig / 烧录入口） |
| `qpi-cli doctor` | 只读预检宿主环境，逐项 PASS/WARN/FAIL |
| `qpi-cli setup` | 安装宿主构建环境（执行 `install_build_env.sh`） |
| `qpi-cli config` | 显示 / 初始化 / 读写配置 |
| `qpi-cli new <app>` | 创建应用工程并接入镜像（源码框架 + recipe + `.code-workspace`） |
| `qpi-cli ls-projects` | 列出应用工程 |
| `qpi-cli build-app <app>` | 加载 eSDK 交叉工具链编译应用工程（`--clean` 先清理） |
| `qpi-cli ls-versions` | 列出固件版本及状态（未构建 / 已构建未打包 / 已打包） |
| `qpi-cli build` | `buildconfig` + `buildall`，完整构建 |
| `qpi-cli package` | `a_key_generation`，生成可烧录固件包 |
| `qpi-cli sdk [--ext]` | 导出交叉编译 SDK（`buildsdk` / `buildesdk`） |
| `qpi-cli flash [ufs\|emmc] [--fw-dir DIR]` | 烧录固件到开发板（可指定固件目录） |
| `qpi-cli kernel` | 内核相关操作（重编内核 / DTB / menuconfig） |
| `qpi-cli clean` | 清理已打包固件或 bitbake 临时目录 |

### 全局参数

| 参数 | 说明 |
|------|------|
| `--sdk <path>` | 指定 SDK 根目录（默认自动探测，或读 `QPI_SDK_ROOT`） |
| `--dry-run` | 只打印将要执行的命令，不真正执行 |
| `--verbose` | 显示 SDK 环境脚本输出 |
| `--version` / `-h` | 打印版本 / 帮助 |

全局参数既可以放在 `qpi-cli` 之后，也可以放在子命令之后，例如
`qpi-cli build --dry-run` 与 `qpi-cli --dry-run build` 等价。

### 常用示例

```bash
# 只想看看会执行什么，不真的动手
qpi-cli build --os LINUX --ver STD --dry-run
qpi-cli flash ufs --dry-run

# 烧录现成的固件包（不改 buildconfig）
qpi-cli ls-versions
qpi-cli flash ufs --fw-dir SG565DWFPARL1A01_BL01BP01K0M01_QDP_LP6.6.0XX.01.00X_V0X

# 调试版本（DBG）+ 安全启动
qpi-cli build --os LINUX --ver DBG --sec

# 只重编内核 / DTB
qpi-cli kernel
qpi-cli kernel --dtb
qpi-cli kernel --menuconfig

# 清理
qpi-cli clean --packages --yes     # 删除已打包的固件目录
qpi-cli clean --build --yes        # 删除 build-qcom-wayland/tmp-glibc

# 机器可读输出
qpi-cli ls-versions --json
```

### 应用工程如何进镜像

`qpi-cli new <app>` 除了在 `projects/<app>/` 下生成源码工程，还会创建一个用户
bitbake 层 `layers/meta-qpi-apps/`：

| 文件 | 作用 |
|------|------|
| `conf/layer.conf` | 层定义（`LAYERSERIES_COMPAT = "nanbield scarthgap"`） |
| `recipes-apps/<app>/<app>.bb` | `inherit externalsrc`，源码指向 `projects/<app>` |
| `recipes-images/images/<image>.bbappend` | `IMAGE_INSTALL:append = " <app>"`，装进最终镜像 |

`<image>` 取自 `quectel_build/config/base-recipes`（默认 `quecpi-image`）。
层的注册写入 `layers/meta-qcom-distro/conf/bblayers.conf` 的 `EXTRALAYERS` ——
SDK 每次 source `build.sh` 都会用它重建 `build-*/conf/bblayers.conf`，所以必须改
模板才能持久生效（同时也会更新当前构建目录里的那份）。

因此应用有两条编译路径：

```bash
# 快速迭代：用 eSDK 工具链直接交叉编译，产物在 projects/<app>/<app>
qpi-cli build-app <app>

# 进镜像：bitbake 编译 recipe 并打包进 rootfs，之后 package + flash
qpi-cli build --os LINUX --ver STD
```

---

## 5. SDK 根目录的探测规则

`qpi-cli` 在每次执行时按以下顺序寻找 SDK 根目录：

1. 命令行 `--sdk <path>`；
2. 环境变量 `QPI_SDK_ROOT`；
3. 当前工作目录及其各级父目录（向上最多 6 层）。

判定条件是目录下存在 `quectel_build/compile/build.sh`；
若该目录下同时存在 `install_build_env.sh`（对外仓库的首次环境安装入口），
则优先采用。

因此常见用法有两种：

```bash
# A. 在 SDK 目录里直接用（自动探测）
cd Pi-H1-Linux-SDK && qpi-cli doctor

# B. 在任意目录指定 SDK
qpi-cli doctor --sdk ~/Pi-H1-Linux-SDK
```

---

## 6. 配置文件

配置的读取优先级：

1. 环境变量 `QPI_CONFIG` 指向的文件（可读写）；
2. SDK 根目录下的 `.qpi_config.json`（**只读覆盖**，方便团队统一约定）；
3. 用户级默认路径 `~/.config/qpi-cli/config.json`（默认写到这里）。

即：配置文件本身**不会**写进你 clone 的 SDK 仓库。只有显式执行 `qpi-cli new`
才会在 SDK 里生成 `projects/<app>/` 和 `layers/meta-qpi-apps/`（并修改
`layers/meta-qcom-distro/conf/bblayers.conf`），这些属于应用工程产物，按需提交或
加入 `.gitignore`。

```bash
qpi-cli config                      # 查看当前配置与来源
qpi-cli config --init               # 写入默认配置
qpi-cli config --set build.os=DEBIAN
qpi-cli config --get build.rev
```

配置内容示例：

```json
{
  "schema": 1,
  "sdk": { "name": "yocto-h1", "type": "yocto" },
  "build": {
    "project": "QSM565DWF",
    "rev": "SG565DWFPARL1A01_BL01BP01K0M01_QDP_LP6.6.0XX.01.00X_V0X",
    "os": "LINUX",
    "version": "STD"
  }
}
```

`qpi-cli build` 成功后会把你实际使用的 project / rev / OS / version 回写到配置中，
下次不带参数执行 `qpi-cli build` 即沿用上次的选择。

---

## 7. 烧录入口

烧录入口从 SDK 的 `tools/VERSION.txt` 读取：

```
FLASH_ENTRY=scripts/flash.sh
```

- `FLASH_ENTRY` 指向的文件存在时使用它；
- 键缺失或文件不存在时，回落到 `quectel_build/tools/flash.sh`；
- 当前版本**仅支持 Linux 宿主**：检测到 Windows 平台或 `.bat` 入口时会直接报错退出。

`qpi-cli flash` 最终仍然是通过 `source build.sh` 后调用 `flash <storage>` 完成，
因此 SDK 里的烧录逻辑（自动识别 Linux/WSL 平台、选择 qdl 或 QFIL 等）完全保持不变。

烧录前会先检查固件目录：当前 buildconfig 对应的版本未打包时直接给出提示，并列出
`quectel_build/` 下现成可烧录的包。用 `--fw-dir` 可以跳过 buildconfig，直接烧某个
已打包目录（目录名或路径均可），该值通过 `QPI_FW_DIR` 传给 `flash.sh`。

---

## 8. 环境变量

| 变量 | 作用 |
|------|------|
| `QPI_SDK_ROOT` | 指定 SDK 根目录 |
| `QPI_CONFIG` | 指定配置文件路径（覆盖用户级默认路径） |
| `QPI_FW_DIR` | 指定烧录用的固件目录（`flash.sh` 直接读取，优先于 buildconfig） |
| `NO_COLOR` | 设置后关闭彩色输出 |

---

## 9. 版本状态说明

`qpi-cli ls-versions` 中每个固件目录有三种状态：

| 状态 | 含义 |
|------|------|
| 未构建 | `quectel_build/<rev>` 目录不存在 |
| 已构建未打包 | 目录存在，但没有 `rawprogram*.xml` / `prog_firehose*.elf` 等打包指纹 |
| 已打包(可烧录) | 目录内存在打包指纹文件，可直接用于烧录 |

`quectel_build/` 下的 `compile`、`tools`、`downloads`、`sstate-cache` 等基础设施目录
不会被误认成固件版本。

---

## 10. 从源码安装与发布

```bash
git clone https://github.com/james-liu18/qpi-cli.git
cd qpi-cli

# 本地开发安装（可编辑）
pip install -e .

# 构建发布产物
python -m pip install --upgrade build twine
python -m build
# 产物在 dist/：qpi_cli-<version>-py3-none-any.whl 与 qpi_cli-<version>.tar.gz

# 上传到 PyPI
twine check dist/*
twine upload dist/*
```

版本号唯一来源是 `src/qpi_cli/__init__.py` 中的 `__version__`，
`pyproject.toml` 通过 `dynamic = ["version"]` 自动读取，无需两处同步。

### 自动发布（GitHub Actions）

仓库内的 `.github/workflows/release.yml` 实现打 tag 自动发布：

```bash
# 发布 0.2.1：先把 __version__ 改成 0.2.1 并提交，再打 tag
git tag v0.2.1
git push origin v0.2.1
```

工作流依次做三件事：

1. 校验 tag 与包内 `__version__` 一致（不一致直接失败，避免发错版本）；
2. `python -m build` + `twine check`，并在干净 venv 里
   `pip install --no-deps dist/*.whl` 后跑 `qpi-cli --version` / `--help` 自检；
3. 用 PyPI **Trusted Publishing（OIDC）** 上传，无需在仓库里存 API token。

首次发布前需在 PyPI 配置一次性授权（<https://pypi.org/manage/account/publishing/>
的 "pending publisher"，项目已存在则在 Project → Publishing 里加）：

| 字段 | 值 |
|------|-----|
| PyPI Project Name | `qpi-cli` |
| Owner | GitHub 账号或组织（例如 `james-liu18`） |
| Repository name | `qpi-cli` |
| Workflow name | `release.yml` |
| Environment name | `pypi` |

也支持手动触发（Actions → release → Run workflow）：只构建与自检，不会上传。

---

## 11. 常见问题

**Q：提示"未找到 SDK 根目录"？**
A：确认当前目录在 SDK 内，或显式指定 `--sdk ~/Pi-H1-Linux-SDK` / `QPI_SDK_ROOT`。

**Q：`qpi-cli doctor` 报磁盘 FAIL？**
A：BitBake 构建建议预留 ≥300 GB 可用空间，请换一个大分区或清理旧构建产物。

**Q：Ubuntu 23.10+ 上构建失败，提示用户命名空间？**
A：执行 `qpi-cli doctor` 会给出提示；构建前先让 SDK 的
`relax_userns_for_bitbake` 生效（`build.sh` 中已有该函数）。

**Q：`qpi-cli flash` 报"仅支持 Linux 宿主"？**
A：当前版本的烧录仅支持 Linux 宿主；Windows 下请直接使用 SDK 自带的
Windows 烧录流程。
