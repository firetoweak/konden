# Codex A2A POC

在一段 konden 对话里请 Codex 处理代码任务。konden 通过已启用的 MCP 能力发送请求，连接器通过 A2A 1.0 把请求交给本机 Codex，报告回到同一段 konden 对话。Codex 在启动服务时选定的目录中执行，每次委托开启一段新的 Codex 会话。

## 启动 Codex 服务

在仓库根目录用 Python 3.13 准备独立环境。先确认 `codex login status` 显示本机已有可用的 Codex 登录。

```powershell
python -m venv .tools/a2a-codex-env
$pocPython = (Resolve-Path '.tools/a2a-codex-env/Scripts/python.exe').Path
& $pocPython -m pip install -r scripts/a2a_codex_poc/requirements.txt
[Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32)) |
  Set-Content -LiteralPath '.tools/a2a-codex-token' -Encoding ascii
```

将工作目录替换为本次授权给 Codex 的目录，再启动服务。保持此终端运行。两端均读取同一个本机凭据文件。

```powershell
& $pocPython scripts/a2a_codex_poc/adapter.py `
  --workspace 'C:/path/to/test-worktree' `
  --port 48781 `
  --token-file .tools/a2a-codex-token
```

Windows 11 支持 MXC 的机器可在启动命令加 `--windows-mxc`，为本次运行选择该沙箱。[Codex Windows 沙箱](https://learn.chatgpt.com/docs/windows/windows-sandbox)

服务仅监听本机。可访问 `http://127.0.0.1:48781/.well-known/agent-card.json` 查看公开的 A2A 信息。任务提交和查询需要本机接入凭据。

### 在「外部会话」显示 Codex 会话

在发起任务的 konden Web 实例打开「设置 → Codex 接入」，新建或查看连接配置。在「高级：手动配置 TOML」复制配置，存为本机文件，例如 `.tools/konden-codex-connection.toml`。这个文件含有接入凭据，请保存在本机。

在启动命令追加 `--konden-mcp-config .tools/konden-codex-connection.toml`。随后每次委托新开的 Codex 会话会自动出现在侧栏「外部会话」，点名称即可打开三方会话页。A2A 任务与报告在发起委托的 Bot 对话查看。这项配置可选，省略时可独立运行 A2A 服务。

## 从 konden 委托工作

在另一个 PowerShell 窗口，从仓库根目录生成 MCP 登记命令。将说明里的工作目录替换为启动服务时选定的目录。

```powershell
$pocMcp = @{
  id = 'codex-a2a'
  display_name = 'Codex'
  description = '请本机 Codex 在 C:/path/to/test-worktree 处理代码任务并回传报告。'
  transport = 'stdio'
  enabled = $true
  transport_config = @{
    command = (Resolve-Path '.tools/a2a-codex-env/Scripts/python.exe').Path
    args = @(
      (Resolve-Path 'scripts/a2a_codex_poc/connector.py').Path
      '--port'
      '48781'
      '--token-file'
      (Resolve-Path '.tools/a2a-codex-token').Path
    )
  }
}
'/mcp upsert ' + ($pocMcp | ConvertTo-Json -Depth 5 -Compress)
```

在 konden 终端中粘贴生成的命令。然后输入「请用 Codex 外部能力检查当前目录的失败用例，修复后把报告带回来，再由你运行测试复验。」终端的工作区可以设为同一个项目，以便 konden 复验。Web 入口可在对话中提供登记 JSON 并确认 MCP 安装提案。

`delegate_to_codex` 等候本次 Codex 报告。`get_codex_task` 可用返回的任务编号查询状态。任务编号在本次服务运行期间有效。收到结果未知时，先根据任务编号或工作目录里的实际变化核对结果，再决定是否交付下一项工作。

## 验证

2026-11-22，在 Windows 11 的隔离 Web 实例中创建一个 Bot，请它委托真实 Codex 修复一个加法样例。样例测试从失败变为通过，Git 核对仅 `calculator.py` 有改动。Web 最终回复与 A2A 查询返回相同的任务编号和完成状态。另在独立终端对话中验证了 MCP 登记、报告失败时的反馈和样例复验。

启用外部会话登记后，隔离 Web 实测在委托执行期间侧栏已显示新会话。点开并刷新后仍只有一条记录，编号与 Codex 本机保存的会话一致。Bot 原对话和 A2A 查询均收到已完成的只读核对报告。

仓库的 Python、进程冒烟及相关 MCP、Codex 进程用例通过。下面的 POC 用例核对原生会话登记、登记被拒绝和 A2A 报告。实测使用 Windows MXC，其他平台、Web MCP 安装交互以及运行中断后的接续待验证。

```powershell
& $pocPython -m pip install pytest
& $pocPython -m pytest -q tests/scripts/test_a2a_codex_poc.py
```
