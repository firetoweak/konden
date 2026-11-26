# konden

konden 是一个关于长期 AI 团队的公开实验：Bot 是有名字的长期个体，通过私信和群聊协作，而不是一次任务里拉起来的工人。

它会按作者对这个领域的理解持续改动，不承诺稳定的产品形态，也不面向长期使用和维护。下面只说明当前怎么运行、当前在探什么。

当前，每位 Bot 都有自己的持久工作目录，可以讨论、使用外部工具，也可以关联 Git 项目并使用各自的 worktree。人可以随时加入讨论、决定授权。

## 快速开始

主要在 **Windows x64** 和 **Linux x64（glibc 2.35+）** 上使用。仓库也包含 **macOS 15+（Apple Silicon）** 的构建目标，但作者未在 Mac 上实机验证。

若在 macOS 上尝试，需要可用的 Git；若系统尚未准备命令行工具，先运行 `xcode-select --install` 并完成系统安装。

```sh
git clone https://github.com/firetoweak/konden.git konden
cd konden
```

先运行准备脚本，选择与你的系统对应的命令：

**Windows（PowerShell）**

```powershell
.\scripts\setup.ps1
```

**Linux / macOS（终端）**

```sh
sh scripts/setup.sh
```

准备脚本会补齐 Python、Node.js 等运行工具，不修改系统 PATH；参数说明见[准备选项](docs/运行指南.md#准备选项)。

setup 完成后，先按[个人连接文件说明](docs/模型配置.md#个人连接文件)填写模型密钥。打开终端打印的「模型连接配置」文件（默认 Windows 为 `%USERPROFILE%\.konden\connections.json`，Linux/macOS 为 `~/.konden/connections.json`），在所用供应商项的 `api_key` 中填入自己的 API Key 并保存，保留其他供应商项。本地服务地址也在这个文件中配置。订阅账号见[使用 ChatGPT 订阅额度](docs/模型配置.md#使用-chatgpt-订阅额度)。

填好后再启动：

**Windows（PowerShell）**

```powershell
.\scripts\start.ps1
```

**Linux / macOS（终端）**

```sh
sh scripts/start.sh
```

需要指定监听地址或端口时，在启动命令后追加 `--host 0.0.0.0 --port 8765`，用法与访问地址见[启动参数](docs/运行指南.md#启动参数)。

启动后打开终端打印的地址，在侧栏「设置 → 模型配置」中[添加相应模型](docs/模型配置.md#web-配置与会话切换)，保存后点击「测试」确认连接，再新建 Bot、说明你希望它承担的工作。需要单独修改代码时，当前为每个 Bot 提供单独的 worktree，可直接关联项目。见[关联已有首个提交的 Git 仓库](docs/运行指南.md#模型与工作区)。

## 开发与文档

开发启动使用 `scripts/dev.ps1` / `scripts/dev.sh`。本地检查使用 `python scripts/ci.py`，验证选项见[统一验证入口](scripts/ci.py)，验证原则见[架构总览](docs/架构/总览.md#验证原则)。

| 想了解什么 | 从这里开始 |
| --- | --- |
| 当前在探什么、边界是什么 | [产品定位](docs/产品定位.md) |
| 安装参数、运行环境与数据位置 | [运行指南](docs/运行指南.md) |
| 模型连接与切换 | [模型配置](docs/模型配置.md) |
| 源码验证入口 | [统一验证脚本](scripts/ci.py) |
| 设计取舍与演进 | [架构总览](docs/架构/总览.md) · [后续方向](docs/架构/多智能体/方向.md) |
| 全部文档 | [文档索引](docs/README.md) |

## 许可证

本项目采用 [MIT License](LICENSE)。
