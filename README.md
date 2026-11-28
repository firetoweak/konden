# konden

konden 是一个关于长期 AI 团队的公开实验：Bot 是有名字的长期个体，通过私信和群聊协作，而不是一次任务里拉起来的工人。

它会按作者对这个领域的理解持续改动。下面只说明当前怎么运行、当前在探什么，不作为稳定承诺。

当前，每位 Bot 都有自己的持久工作目录，也可以关联 Git 项目并使用各自的 worktree。人可以随时加入讨论，并决定是否批准敏感操作。

## 快速开始

主要在 **Windows x64** 和 **Linux x64（glibc 2.35+）** 上使用。仓库也包含 **macOS 15+（Apple Silicon）** 的构建目标，但作者未在 Mac 上实机验证。

```sh
git clone https://github.com/firetoweak/konden.git konden
cd konden
```

**Windows（PowerShell）**

```powershell
.\scripts\setup.ps1
# 按提示填写模型密钥后再启动
.\scripts\start.ps1
```

**Linux / macOS（终端）**

```sh
sh scripts/setup.sh
# 按提示填写模型密钥后再启动
sh scripts/start.sh
```

准备脚本会补齐 Python、Node.js 等运行工具，不修改系统 PATH。默认配置文件在 Windows 为 `%USERPROFILE%\.konden\connections.json`，在 Linux/macOS 为 `~/.konden/connections.json`。启动后打开终端打印的地址，在「设置 → 模型配置」中添加模型并新建 Bot。安装参数、数据位置和模型配置的详细说明见[运行指南](docs/运行指南.md)与[模型配置](docs/模型配置.md)。

## 文档

开发启动使用 `scripts/dev.ps1` / `scripts/dev.sh`。本地检查使用 `python scripts/ci.py`，验证原则见[架构总览](docs/架构/总览.md#验证原则)。

| 想了解什么 | 从这里开始 |
| --- | --- |
| 当前在探什么、边界是什么 | [当前定位与边界](docs/产品定位.md) |
| 安装参数、运行环境与数据位置 | [运行指南](docs/运行指南.md) |
| 模型连接与切换 | [模型配置](docs/模型配置.md) |
| 源码验证入口 | [统一验证脚本](scripts/ci.py) |
| 设计取舍与演进 | [架构总览](docs/架构/总览.md) · [后续方向](docs/架构/多智能体/方向.md) |
| 全部文档 | [文档索引](docs/README.md) |

## 许可证

本项目采用 [MIT License](LICENSE)。
