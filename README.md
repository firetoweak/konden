# konden

<p align="center">
  <img src="assets/brand/konden-social-preview-v1.png" width="1280" alt="konden — 你的 AI 团队，持续协作。独立验证、持续跟进、随时介入。">
</p>

<p align="center">
  <a href="#快速开始">快速开始</a> ·
  <a href="#codex-接入">接入 Codex</a> ·
  <a href="docs/README.md">文档</a>
</p>

**一支长期协作的 AI 团队，接上你正在进行的工作。**

konden 提供一支有名字、能互相交流的长期 Bot 团队。你可以直接与它们讨论、检索资料、生成内容、完成简单代码，也可以接上外部工具，开展独立验证与持续跟进。

每位 Bot 都有自己的持久工作目录，通过私信和群聊协作。你设定目标、决定授权，也可以随时加入讨论、调整方向。


## 快速开始

支持 **Windows x64**、**Linux x64（glibc 2.35+）** 与 **macOS 15+（Apple Silicon）（注意，本项目并未在MAC系统上实机使用过！！！）**。

macOS 需要可用的 Git；若系统尚未准备命令行工具，先运行 `xcode-select --install` 并完成系统安装。

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

启动后打开终端打印的地址，在侧栏「设置 → 模型配置」中[添加相应模型](docs/模型配置.md#web-配置与会话切换)，保存后点击「测试」确认连接，再新建 Bot、说明你希望它承担的工作。需要单独修改代码时，konden为每个bot提供单独的worktree，可直接关联项目。见[关联已有首个提交的 Git 仓库](docs/运行指南.md#模型与工作区)。

> !Mac 未实际实机使用！

Bot 可按需使用随环境安装的 Chrome DevTools MCP、Playwright 和应用专用 Chrome，进行浏览器操作与真实流程验证。搜索优先使用你已启用的搜索 MCP，其次使用已配置密钥的内置 DeepSeek；配置、数据位置与其他入口见[运行指南](docs/运行指南.md)。

内置 OfficeCLI 可直接读取、创建、编辑和预览 Word、Excel、PowerPoint 文件，无需安装 Microsoft Office。让 Bot 生成文档并交付文件即可，工具与使用指南按需加载。

内置 PDF Reader（AnyMD）可提取文字与目录，按页查看 PDF 原始画面，并放大局部。双栏、图表和扫描页由支持视觉的聊天模型结合图片理解，无需配置 OCR；Bot 按需加载工具与阅读指南。

## Codex 接入

安装并登录同一台电脑上的 Codex Desktop 后，可以在 konden 的「外部会话」中继续使用 Codex 原生会话。

- **从 konden 开始。** 点「新建 Codex 会话」，首条消息被 Codex 接收后，会话就会出现在列表中。也可以点「添加已有 Codex 会话」按名称接入。通过「在 Desktop 打开」可以回到同一个聊天。
- **从 Codex 开始。** 首次在 konden 打开「设置 → Codex 接入」，点「安装到本机 Codex」，再重启一次 Codex Desktop 载入 MCP 配置。之后在任意旧会话或新会话里说「连上 Konden」，如有工具授权提示，按提示确认。

一份 MCP 配置可供多个会话共用，每条会话独立登记并保留自己的往来。Codex 与 konden 的日常消息通过 WebSocket 收发。Codex 侧断线时，在原会话里再次联系 konden 即可重连。konden 侧与本机 Codex 断开时，点击「重新连接 Codex」。**后续接入会话和断线重连都无需重启 Desktop。**

konden Bot 和 Codex 可以通过 A2A 互相派单。konden Bot 根据任务选择新建 Codex 会话或沿用已有会话。报告和文件回到发起派单的对话，图片可在 konden 中预览，文件可下载。

使用示例、权限范围、自动接续与撤销方式见[Codex 会话接入指南](docs/外部协作.md)。

## 开发与文档

开发启动使用 `scripts/dev.ps1` / `scripts/dev.sh`。本地检查使用 `python scripts/ci.py`，验证选项见[统一验证入口](scripts/ci.py)，验证原则见[架构总览](docs/架构/总览.md#验证原则)。

| 想了解什么 | 从这里开始 |
| --- | --- |
| 产品解决的问题、当前场景与边界 | [产品定位](docs/产品定位.md) |
| 安装参数、运行环境与数据位置 | [运行指南](docs/运行指南.md) |
| Codex 会话接入、派单与文件交付 | [Codex 会话接入指南](docs/外部协作.md) |
| 在外部对话中交付独立验证 | [独立验证](docs/外部协作.md#独立验证) |
| 模型连接与切换 | [模型配置](docs/模型配置.md) |
| 源码验证入口 | [统一验证脚本](scripts/ci.py) |
| 设计取舍与演进 | [架构总览](docs/架构/总览.md) · [后续方向](docs/架构/多智能体/方向.md) |
| 全部文档 | [文档索引](docs/README.md) |

## 许可证

本项目采用 [MIT License](LICENSE)。
