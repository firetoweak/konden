# konden 验证功能地图

本目录是验证konden的功能地图。先读本页，再按改动打开对应的功能文件。说明用中文写；路由、按钮文字、命令、选择器、API 路径、文件名一律照源码原样写，不翻译。

原有条目的历史验证基线与日期保留在各自状态里。2026-10-13 按当前工作区新增会话变更审阅的增量地图，不表示全量重验，也不将源码核对或自动化测试标为真实 UI 通过。

2026-10-22 按 `origin/main@4c000ca` 核对全部 27 个功能文件，使用同一个隔离 Web 实例、真实 DeepSeek 和 Windows Chrome，每个功能至少实跑一条路径；终端另用独立 home 与 ConPTY。该日期保留历史轮次，不代表当前所有子功能和错误路径都通过。该轮记录的窄屏输入缺口属于当时结果；当前状态见下表及 layout-appearance 的增量记录，维护不修改产品代码。

2026-11-10 需求对齐以 `origin/main@bab1ca4b` 为基线：27 个功能逐项关联现行需求并补判据，同一隔离 Windows Web／Chrome、真实 DeepSeek 复核入口，终端另用独立 home 与 ConPTY。新增判据未全部实跑，尤其外部独立验证修复复验闭环、长时间条件与故障交错不能计为通过。

2026-11-21 主线核对使用 `129998ef23ea2a12e0527231303ec1f483c084fa`、隔离 Windows Web／Chrome 和真实 DeepSeek，终端使用独立 ConPTY 与数据目录。27 个功能均核对了源码并实跑至少一条路径。范围见下表，证据在任务 worktree 的 `.tools/verification/main-20261121/evidence/<功能 id>/`。外部交付与修复复验、主动记忆检查的真实模型表现、长时间条件和故障交错仍按各条目的未验证范围记录。

## 需求与验收怎么对齐

每个功能文件开头的**需求依据**链接到应满足的现行规则，`Driving it with ...` 把规则落实为用户操作、可观察结果与不变量。产品目标先看[产品定位](../../../../docs/产品定位.md)，跨层边界先看[运行语义规约](../../../../docs/架构/运行语义规约.md)；项目约束及登记例外对照 [AGENTS.md](../../../../AGENTS.md) 与[异常边界登记](../../../../tests/architecture/test_exception_boundaries.py)；实现入口再用代码和测试核对。历史与后续方向不作为当前验收要求。

发现差异时：遗漏的需求补判据；与明确需求不符的实际行为记**产品缺口**并保留正确预期；现行依据互相冲突时记**依据冲突**，不凭当前实现替需求作决定。每个功能实跑一条只说明功能可达，不等于其全部需求通过。记录须区分需求已映射、实际通过、失败、受阻和未验证，附实际提交号、场景与证据。

首个外部场景的验收见 [external-conversations：独立验证](external-conversations.md#driving-it-with-the-browser)：交付验收要求与版本、独立操作取证、反馈回原聊天并被处理、新版本复验。消息往返、提醒入队、CI 或报告完成均不能替代这个闭环。

### 当前差异的处理

| 类型 | 对照 | 验收处理 |
|---|---|---|
| 已登记设计例外 | [通用工具 handler](tool-coding-round.md#gotchas) 在架构测试中明确登记，允许转为可见 `TOOL_HANDLER_ERROR` | 按该显式例外验收，不误报为 Worker 故障；不得扩展到其他内部异常。 |
| 依据冲突 | [运行指南](../../../../docs/运行指南.md#运行环境)仍提“只读子 Agent”，[当前 SubAgent](../../../../docs/架构/运行/SubAgent.md#执行能力)与委派入口没有此模式 | 当前父子按实际可达的读写能力验收；受限目录过滤与不存在的只读子模式分开，不凭此报浏览器回归。 |
| 依据冲突 | [Web](../../../../docs/架构/入口/Web.md#对话与操作)仍定义回复耗时，地图和界面已只显示提交时间 | 时间来源继续核对；是否恢复耗时未作需求决定。 |
| 依据冲突 | [模型配置指南](../../../../docs/模型配置.md)称首次准备自动进入终端向导，[运行指南](../../../../docs/运行指南.md#准备选项)规定默认网页、显式选项才进向导 | 分别记录实际默认流程与显式向导范围，不误报默认不进向导为产品失败。 |

## 基线前置条件

2026-11-02 默认目录访问增量：Windows 隔离 Web + 本机受控模型，验证新建 Bot 无目录权限选项、E 盘文件写入与命令 cwd、关闭自动放行后逐项批准，以及 714px 资料抽屉。见 [tool-coding-round.md](tool-coding-round.md)，不代表其他功能全量重验。

编码 Skill 增量：编码方法与 Git 协作分别按需加载，普通目录支持自定义 Skill；验证见 tool-coding-round 与 extensions，既有记录不代表全部重验。

Bot 可不挂项目独立使用；代码任务需显式选择项目。名册以 `projects` 列表描述挂载关系，落地、同步需要项目名；子任务共享父工作区，委派可选择已有工作目录。

- 按 `../SKILL.md` 启动一个隔离实例：地址 `$KONDEN_VERIFY_URL`，数据目录 `$KONDEN_HOME`，临时工作区 `$SCRATCH_WORKSPACE`（内含 `note.txt`，内容为一行 `verify`）。
- 第一次点击前 `doctor.py` 必须对该 pid 和 home 返回 0。
- 视口至少 1280×800，侧栏才在屏幕上（窄屏时侧栏收在 `打开 Agent 名册` 后面）。
- 不要驱动 `~/.konden`、不要占用别人的 8765 端口、不要用带 `--workspace` 或 `--dev` 启动的服务。

## 数据落点速查（判据里反复用到）

来源 `konden/paths.py`、`konden/model_settings.py`、`konden/assistant/host/supervisor.py`、`konden/assistant/host/agent_worktrees.py`：

| 内容 | 位置 |
|---|---|
| 默认模型与候选模型 | `$KONDEN_HOME/config.json` |
| 供应商连接（含密钥） | `$KONDEN_HOME/connections.json` |
| 可选代码项目登记 | `$KONDEN_HOME/workspaces.json` |
| 会话运行数据（journal、附件） | `$KONDEN_HOME/runtime_sessions/<sha256(session_id)>/journal.sqlite` |
| 每会话模型选择 | `$KONDEN_HOME/runtime_sessions/models.json` |
| 定时任务 | `$KONDEN_HOME/state/automation.sqlite` |
| 例行任务 | `$KONDEN_HOME/state/routines.sqlite` |
| Bot 个人目录 | `$KONDEN_HOME/state/bots/<sha256(session_id) 前 20 位>/` |
| Bot 的项目 worktree | Bot 个人目录下 `projects/<项目名>/` |
| 集成 worktree | `$KONDEN_HOME/state/coding_bot/<sha256(workspace_id) 前 20 位>/integration` |
| MCP / Skill / CLI 安装 | `$KONDEN_HOME/mcp/`、`skills/`、`clis/` |

## 判据写法约定

每个功能文件的 `Driving it with ...` 一节汇总以下四类可检查判据；同一场景可以覆盖多个需求，共享边界优先链接已有场景，不为每条规则复制一套操作：

- **(a) 状态**：磁盘、配置文件、journal 或 `GET` 接口里应看到什么。
- **(b) 信号**：HTTP 状态码、SSE 事件、日志或界面文字。
- **(c) 不变量**：哪些东西必须保持不变。
- **(d) 错误路径**：至少一条故意走错的路径及其预期表现。

页面显示「成功」不算证据；以 (a)(c) 的实际读数为准。改动请用对应的 `GET` 复核，不要用直接 `POST` 接口代替用户操作（错误路径探测除外，判据里会写明）。

已经落地的边界用例留在所属功能的 `Driving it with ...` 中，记录可控触发、验收不变量和自动化入口，作为后续相关开发的复用基线；单次日期、结果、证据与未覆盖范围留在 `Gotchas`。例如 [实时响应时序基线](realtime-events.md#driving-it-with-the-browser) 覆盖旧快照晚到、旧选择确认、Bot／群切换和重连流式交错。修改相关流程时先复跑受影响的既有场景，新增用例仍须守住一条有实际外部失败源的契约，不按清单无差别扩张测试。

## 证据与跳过

- 证据（快照、截图、HTTP 响应体、`note.txt`）放在 `$EVIDENCE_DIR/<功能 id>/`，`note.txt` 里写功能 id 与入口。
- `live_model_ready` 为 false 时，跳过所有需要模型回复的步骤，并在 note 里写明。
- 走不到的入口就记为走不到，换一条路走通不算验证了它。

## 功能条目约定

每个功能文件：一个 H1、开场说明与需求依据，然后按顺序四个 H2：`Sub-features`、`How to get to it (user POV)`、`Driving it with <harness>`、`Gotchas`。

## 功能列表（27 个）

| id | 文件 | 一句话 | 2026-11-21 实跑范围 |
|---|---|---|---|
| web-session-chat | [web-session-chat.md](./web-session-chat.md) | 空首页 → 新建 Bot → 发消息、引用、回答澄清 | 实跑选区引用和短选项澄清，提交后在会话中可查。到期补答、长历史与部署换版未重验。 |
| workspace-management | [workspace-management.md](./workspace-management.md) | 创建 Bot 同页选目录、按目录名展示、复用与后续关联 | 新建 Bot 时关联隔离 Git 项目，工作树与人的检出独立。原生目录选择、复用、解除与重挂未重验。 |
| session-sidebar | [session-sidebar.md](./session-sidebar.md) | 全局 Bot 名册、创建、改名、删除与搜索 | 删除后用原名新建，新旧会话独立，旧群和私信未转给新 Bot。曾用名释放、头像和重启未重验。 |
| colleague-tools | [colleague-tools.md](./colleague-tools.md) | 名册工具、共同和个人记忆、主动检查 | Bot 实际调用 `list_colleagues` 并报告名册。主动检查通过九项受控回归，真实主动写入、页面编辑与冲突未重验。 |
| private-messages | [private-messages.md](./private-messages.md) | `send_message` 送达、同话题入口、交流边界 | 双向送达且弹窗可读。样本答复省略 `reply_to`，形成两个话题，同话题合并未实跑。 |
| external-conversations | [external-conversations.md](./external-conversations.md) | 原生聊天独立身份、通用双向消息与 Web 对话 | 连接命名后取消无新增配置，私聊验证 Bot 可加载内置独立验证 Skill。外部登记、报告回传与修复复验未重跑。 |
| land-and-sync | [land-and-sync.md](./land-and-sync.md) | `land_branch`、`sync_branch`、提交与草稿边界 | 实际落地已提交样本，集成 Git 历史包含原提交，草稿只留在 Bot 工作树，人的 HEAD 不变。同步、冲突与未知结果未重验。 |
| composer-attachments | [composer-attachments.md](./composer-attachments.md) | 附件挂入、发送、图片预览与下载 | 实跑草稿和已发 PNG 预览、放大与拖动，附件 GET 字节与原件一致。实体触屏与本机选择未重验。 |
| inbox | [inbox.md](./inbox.md) | Bot 旁提示 → 对应对话处理控制提案、交流超限与无人查看询问 | 控制提案的判据见能力管理；交流超限与无人查看见本条目。 |
| group-chat | [group-chat.md](./group-chat.md) | 首页 `新建群聊`、群资料、常用切换、人发言与点名 | 首页创建临时组，设为常用后刷新保留，再改回临时。Bot 自行建组、七天淡出和群内回应未重验。 |
| tool-coding-round | [tool-coding-round.md](./tool-coding-round.md) | 文件工具、前台/后台命令与退出结果 | Web 触发 `execute_command` 运行 `git --version`，计划回合也实际写入并读回样本。命令环境隔离、后台组合与非零退出未重验。 |
| execution-trace | [execution-trace.md](./execution-trace.md) | Bot 执行轨迹、模型输入、工具结果和子轨迹 | 从 Bot 对话进入，用隔离实例回看已提交的长会话。 |
| run-control | [run-control.md](./run-control.md) | 运行中继续发送、停止、失败再试与后台命令停止 | 父页面「停止」后两子均结束，停止按钮消失，已写入的文件保留。前台命令中断与后台组合未重验。 |
| subagents | [subagents.md](./subagents.md) | 委派、追加指令、查看过程、共享文件与回收 | 从父会话打开真实子过程，两位子写入共享目录后仍等待。追加指令与附件隔离未重验。 |
| work-plan | [work-plan.md](./work-plan.md) | 计划创建、状态标记、回收与完成归档 | 实际更新两步计划并展开已完成计划，样本写入与读回一致。变更目标、清除、压缩及子计划未重验。 |
| work-plan-and-usage | [work-plan-and-usage.md](./work-plan-and-usage.md) | 消息时间、`上下文用量` 和 compact | 输入区显示 22.7K tokens，GET 对应 22654。真实 compact 与交接未重验。 |
| model-settings | [model-settings.md](./model-settings.md) | `设置 → 模型配置`、连接测试、保存及会话选择 | 通过「已选模型」发起真实 DeepSeek 测试，前后配置一致。供应商目录失败、子模型和在途切换未重验。 |
| scheduling | [scheduling.md](./scheduling.md) | `schedule_once` / `cancel_schedule` 定时唤醒 | 安排 20 秒提醒，GET 有等待时间，无新输入就收到显式提醒。取消与跨到期停机恢复未重验。 |
| routines | [routines.md](./routines.md) | `Bot 资料 → 例行任务` 查看、暂停、恢复与删除 | 实际 `save_routine`，人从页面暂停后 GET 为 `paused_by_user`、无下次安排。无人查看与跨周期未重验。 |
| reply-delivery | [reply-delivery.md](./reply-delivery.md) | 长期 Bot 显式私信与群发言、`send_file` 文件交付 | 实际 `send_message` 发给 `user` 可见，`send_file` 交付 PNG 并下载原图。源文件变更、重启和图文混排未重验。 |
| extensions | [extensions.md](./extensions.md) | 侧栏 `能力` 清单、MCP / Skill / CLI 的管理与检测 | 空安装清单可发现内置 `independent-verification`。单独批准本地测试包安装，关闭操作结果后草稿和会话版本保留。PDF 与其他扩展未重验。 |
| realtime-events | [realtime-events.md](./realtime-events.md) | `/api/events` SSE、连接状态、多标签页同步 | 验证连接状态、会话更新及重连同步。 |
| layout-appearance | [layout-appearance.md](./layout-appearance.md) | 三栏布局、资料栏、`外观`、侧栏宽度与窄屏名册 | 桌面键盘调宽、64px 头像栏搜索及刷新保留通过。390px 名册仍显示全名，选中后输入可达。减少动态效果与实体触屏未验。 |
| subagents-parallel | [subagents-parallel.md](./subagents-parallel.md) | 并行委派、共享文件、选择性回收与父会话控制 | 同一决策派出两子，两者有运行重叠，各写独立文件。选择性回收、后台结果归属及回收失败未重验。 |
| crash-recovery | [crash-recovery.md](./crash-recovery.md) | 进程、网络与模型故障恢复、重试与去重 | 隔离连接使用无效 Key，失败刷新保留。恢复连接后点击「再试」收到显式回复，原用户输入只有一条。杀进程、429、断流和未知副作用恢复未重验。 |
| setup-and-launch | [setup-and-launch.md](./setup-and-launch.md) | `setup.sh`/`setup.ps1` 四步准备、模型向导、`start.sh`/`dev.sh` | 新 worktree 完成 Windows setup 四步、构建、`--check`、Doctor 和隔离首页。复用固定工具缓存，未重验从零下载、向导或其他平台。 |

## 入口 → 地图条目 覆盖表

### Web 路由（`web/src/app/App.tsx`）

| 入口 | 条目 |
|---|---|
| `/` → `AgentHome` | session-sidebar、web-session-chat |
| `/capabilities` → `CapabilitiesPage` | extensions |
| `/sessions/:sessionId` → `SessionConversation` | web-session-chat、inbox 及所有会话类条目 |
| `/groups/:groupId` → `GroupChatPage` | group-chat |
| `/external/:identity` → `ExternalSessionPage` | external-conversations |
| 侧栏 `设置 → 模型配置` / `共同记忆` → `SettingsMenu` 抽屉（保持聊天路由） | model-settings、colleague-tools |
| 未知前端路径回落 `index.html`（`app.py` 的 `_FrontendFiles`）；未知 API 保持 404 | web-session-chat |

### HTTP / SSE 接口（`konden/channels/web/app.py`）

| 接口 | 条目 |
|---|---|
| `GET /api/events` | realtime-events、reply-delivery（思考、显式私信及文件的实时展示） |
| `GET /api/capabilities`、`PUT /api/capabilities/{kind}/{id}/enabled`（kind 为 skills 或 mcp）、`POST /api/capabilities/mcp/{id}/test` | extensions |
| `GET/PUT /api/model-settings`、`POST /api/model-settings/test` | model-settings |
| `GET/PUT /api/shared-memory` | colleague-tools |
| `GET/PUT /api/sessions/{id}/model` | model-settings |
| `GET /api/agents`、`POST /api/agents`、`DELETE /api/agents/{id}` | session-sidebar、colleague-tools |
| `POST /api/agents/{id}/projects`、`POST /api/agents/{id}/projects/{name}/unlink` | workspace-management |
| `GET /api/sessions/{id}` | web-session-chat、reply-delivery（显式私信、澄清与附件） |
| `GET /api/sessions/{id}/commands`、`POST /api/sessions/{id}/commands/{command_id}/stop` | tool-coding-round |
| `GET /api/sessions/{id}/routines`、`PUT /api/sessions/{id}/routines/{routine_id}/state` | routines |
| `PUT /api/agents/{id}/name` | session-sidebar |
| `GET/PUT /api/agents/{id}/memory` | colleague-tools |
| `GET /api/inbox` | inbox |
| `GET/POST /api/groups`、`GET/DELETE /api/groups/{group_id}`、`POST /api/groups/{group_id}/messages`、`PUT /api/groups/{group_id}/favorite` | group-chat |
| `GET/POST /api/external-connections`、`GET /api/external-sessions`、`GET/DELETE /api/external-sessions/{identity}`、`POST /api/external-sessions/{identity}/messages` | external-conversations |
| `POST /api/sessions/{id}/select` | session-sidebar、web-session-chat |
| `GET /api/workspaces`、`POST /api/workspaces/select-directory` | workspace-management |
| `POST .../attachments`（拖入上传）、`POST .../attachments/select`（后端本机选择）、`GET .../attachments/{attachment_id}` | composer-attachments |
| `POST /api/sessions/{id}/inputs` | web-session-chat |
| `PUT /api/sessions/{id}/clarifications/{question_id}/answer` | web-session-chat |
| `POST .../control` | extensions、inbox |
| `POST .../cancel`、`POST .../retry` | run-control、crash-recovery（故障后重试） |
| `GET .../subagents`、`GET .../subagents/{command_id}` | subagents、subagents-parallel |
| `/assets/*`、`/` 静态挂载与前端回落 | web-session-chat |

### 模型可调用的工具（Assistant 能力，经对话触发）

| 工具 | 来源 | 条目 |
|---|---|---|
| `read_file` `glob` `grep` `write_file` `apply_patch` `replace_all` `execute_command` | `konden/tools/builtin/` | tool-coding-round |
| `delegate` `instruct_subagent` `list_subagents` `reclaim` | `konden/assistant/subagent/subagent.py` | subagents、subagents-parallel |
| `read_image` | `konden/assistant/attachments.py` | composer-attachments |
| `update_plan` | `konden/assistant/work_plan.py` | work-plan |
| `find_history`（`model_usage` 不是工具，是用量元数据键，模型调不到） | `konden/assistant/compact/core.py` | work-plan-and-usage |
| `read_command` `stop_command` `list_commands`（后台命令，Linux/Windows/macOS） | `konden/tools/builtin/background_commands.py` | tool-coding-round |
| `schedule_once` `cancel_schedule` | `konden/automation/tool.py` | scheduling |
| `save_routine` `set_routine_state` `list_routines` | `konden/automation/routine_tool.py` | routines |
| `list_colleagues` | `konden/assistant/agent_messages.py` | colleague-tools |
| `read_shared_memory` `update_shared_memory`、`read_personal_memory` `update_personal_memory` | `konden/assistant/shared_memory.py`、`personal_memory.py` | colleague-tools |
| `send_message` | `konden/assistant/agent_messages.py` | private-messages |
| `ask_user` | `konden/assistant/clarification.py` | web-session-chat |
| 全局设置「Codex 接入」、侧栏「外部会话」；MCP 身份与消息；Bot 外部查询和发送 | `features/settings/ExternalAccess.tsx`、`features/external/`、`konden/channels/mcp/`、`assistant/host/external_messages.py` | external-conversations |
| `create_group`、`send_group_message` | `konden/assistant/agent_messages.py` | group-chat |
| `link_project` `unlink_project` | `konden/assistant/agent_tools.py` | workspace-management |
| `land_branch` `sync_branch` | `konden/assistant/agent_tools.py` | land-and-sync |
| `send_message` 发给 `user`、`send_file` | `konden/assistant/agent_messages.py`、`konden/assistant/conversations.py` | reply-delivery |
| `load_management_tools` `load_toolset` `list_mcp_servers` `test_mcp_server` `propose_mcp_*` `load_skill` `read_skill_resource` `list_installed_skills` `test_installed_skill` `propose_skill_*` `load_cli` `list_installed_clis` `inspect_cli` `test_cli` `propose_cli_*` | `konden/mcp/`、`konden/skills/`、`konden/cli/`、`konden/assistant/management.py` | extensions |
| `read_content` | `konden/assistant/content.py` | tool-coding-round（读取或检索工具结果/历史返回的真实引用） |

### 命令行与脚本

| 入口 | 条目 |
|---|---|
| `web_chat.py [--dev] [--host] [--port] [--workspace]` | web-session-chat（`--workspace` 见 workspace-management Gotchas） |
| `scripts/setup.sh`、`scripts/setup.ps1`、`scripts/setup.py [--configure] [--check]` | setup-and-launch |
| `python -m konden.initialize --configure / --if-needed` | setup-and-launch |
| `scripts/start.sh`、`scripts/start.ps1`（→ `scripts/launch.py` → `web_chat.py`） | setup-and-launch |
| `scripts/dev.sh`、`scripts/dev.ps1`（→ `scripts.launch --dev`） | setup-and-launch |

### 非入口类场景（故障与边界）

| 场景 | 源码 | 条目 |
|---|---|---|
| Worker 失败 → SSE `session_failed` | `konden/channels/web/app.py` `report_worker_failures`、`assistant/host/supervisor.py` | crash-recovery |
| 未完成命令 `CommandPhase.UNKNOWN` / `NO_RESULT_YET` | `runtime/state.py`、`assistant/context/projection.py`、`assistant/conversations.py` | crash-recovery |
| 模型错误分类与重试 | `thinllm/chat_completions.py`、`assistant/failures.py` | crash-recovery |
| 输入去重 `delivery_id` | `runtime/journal/sqlite.py` | crash-recovery |
| 子 Agent 重启接管与中断回传 | `assistant/subagent/subagent.py` `rehydrate`、`record_interrupted_return` | subagents-parallel、crash-recovery |
| compact 窗口继承 | `assistant/compact/core.py` | work-plan-and-usage |

### 有意不覆盖

- `thinllm/`：底层 LLM 适配库，不是用户入口；经 model-settings 的 `测试` 间接覆盖。
- `konden/runtime/journal` 的内部恢复逻辑：用户不可直接触发；判据里通过 `GET /api/sessions/{id}` 读 journal 投影。


## 历史轮次

以下轮次记录保留当时事实。2026-10-18 起文件回退整体移除：agent-workspace-restore、file-rewind、rewind-edge-cases、edit-and-branch 已删除，setup-and-sandbox-install 改为 setup-and-launch，Doctor 不再检查 `sandbox.ready`。

## 2026-10-12 live 实跑
证据根目录：`/workspace/verify-run-20261012`（下文路径相对于它），完整报告见该目录 `REPORT.md`。

该轮次对应源码基线 `main@1a4790a`。其后的 2026-10-12 修复轮次已把落地契约写入对应架构文档（如 `docs/架构/入口/Web.md` 与当时的文件操作与回退文档）：隔离 API 复跑记录在仓库 `.tools/verification/fixapi-20261012/`（16 项检查，覆盖附件入口边界、已知入口错误、模型失败可见与再试）；真实 UI 流程未在该环境复跑，本表中相关条目标注「UI 待复跑」。

## 2026-10-16 维护轮次

源码基线 `main@e191eab`，隔离实例 + DeepSeek 真模型 + Linux 沙箱，经 HTTP/SSE/磁盘驱动（本轮无浏览器，所有条目「界面未验证」）。修正：doctor 沙箱路径、工作区重命名/移除、持续进程工具、`execute_command` 授权范围、回滚产生新会话、`model_usage` 非工具、安装脚本参数与前置。表中旧状态未逐条改写。

## 2026-10-18 源码核对轮次

源码基线 `main@b7a21c3`。25 个功能文件逐个对照源码核对并改写，**没有驱动 Web 页面**：表中「live 验证状态」沿用旧记录，各文件 `Gotchas` 里的历史验证条目注明「2026-10-18 按源码改写，未 live 复跑」。主要漂移来自统一内容页（`文件改动`、`子 Agent`）、停止与再试语义、模型可见文案压缩、回滚结果提示上线和 `skill_help` 删除。各文件原有的额外 H2（`Live status` 等）已并入 `Gotchas`。

Harness 修正已在 Windows 上按 `../SKILL.md` 原文实跑 Launch → Doctor → Cleanup：`doctor.py` 在 Windows 上不再用 `os.kill(pid, 0)`（该调用在 Windows 等于发送 Ctrl+C），改为查询进程状态并核对命令行，并检查 Windows 命令执行组件；Windows 启动片段改为可直接运行的 PowerShell。Linux 上的 Doctor 路径未改动、未在本轮复跑。

源码核对发现、待 live 确认或修复的产品缺口（记录在对应文件 `Gotchas`，判据保持正确预期）：

- web-session-chat（历史缺口）：未知 API 被静态兜底吞掉、静态目录缺少路径约束；当前已改用标准 StaticFiles，2026-10-20 对照测试通过，尚待 Web live 复验。
- extensions：`/control` 决定冲突与无待确认请求没有异常处理器，返回 500 而非 409。
- crash-recovery（已修复）：同一 `delivery_id` 改文本重放，2026-10-20 live 确认修复前为 500 + Worker 失败，修复后为 409、原事件不变且后续对话可用。历史 Worker 卡死在同日后续实测未复现，范围见条目。
- workspace-management：空名称创建工作区可能 500（界面已拦）。
- composer-attachments：隐藏的文件 `input` 没有任何入口触发。

## 2026-10-18 Windows live 轮次

同日在 Windows 上按 `../SKILL.md` 隔离启动实跑（`setup.ps1` 全流程、Doctor `--require-model --require-sandbox` 通过、模型 `deepseek/deepseek-flash`、agentFS 0.5.1），用桌面 app 内置浏览器面板驱动，证据（`note.txt`、`state.json`、`page*.jpg`）留在本机证据目录。本节仅记录移除前版本的 live 结果，不作为当前版本通过的依据；执行方式与判据有变化的条目需重新 live 验证。

| 结果 | 功能 |
|---|---|
| 通过 | web-session-chat、session-file-changes、run-control、subagents、subagents-parallel（并行派发）、scheduling、model-settings、session-sidebar、realtime-events、work-plan、work-plan-and-usage |
| 失败（产品缺口） | tool-coding-round（Windows 退出码折叠为 1）、crash-recovery（Worker 崩溃后会话永久卡死） |
| 走不到 | workspace-management（重命名/移除）、layout-appearance（外观菜单）、composer-attachments、extensions、console-chat——中途浏览器面板隐藏后 `requestAnimationFrame`/`ResizeObserver` 不触发（实测 0 次），弹窗、菜单与虚拟时间线无法渲染；console-chat 需要交互终端 |

已从地图删除的功能包括终端聊天，以及当时记为通过 agent-workspace-restore、file-rewind、rewind-edge-cases、edit-and-branch。setup-and-sandbox-install 已改为 setup-and-launch。

本轮 live 新发现的产品缺口（已写入对应文件 `Gotchas`）：Windows 命令退出码折叠为 1；Worker 崩溃后会话永久卡死；自动放行批准后 `等待授权` 弹窗不关闭（已随待处理条消除）；会话页高频轮询 `/model` 与 `/model-settings`。地图漂移已按实测修正：停止命令后不出现 `继续`（run-control）、SSE keep-alive 实际为 `: ping`（realtime-events）。

## 已确认的设计边界（不当问题报）

- Web API 没有登录鉴权，是刻意设计。
- Web 不提供文件改动页；用真实工作目录与 Git 核对改动。没有系统内文件回退。
- 工作计划由模型制定和控制，没有完整状态机。
- `select-directory` 是 human-in-the-loop，人不选就一直等。
- 停止：只有一个 `停止`；一次性写入写完再停，长命令杀进程组，中断调用标 unknown 并给模型简短提示；后续新消息仍可推进，未知结果不自动重跑。

