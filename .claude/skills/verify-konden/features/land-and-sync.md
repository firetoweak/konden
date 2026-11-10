# 落地与同步

`land_branch` 把 Agent 分支上已经提交的工作合入项目集成分支。`sync_branch` 把集成分支上同事已经落地的工作合进自己的分支。两个工具都只接受 `project`（Bot 挂上的项目名），都不进入待处理条，也不改人当前检出的分支和工作目录。未提交的修改不跟着落地。冲突时落地放弃、集成分支不动；同步则停在冲突状态，留给 Agent 用普通 Git 命令解决或放弃。来源：`konden/assistant/agent_tools.py`、`konden/assistant/host/agent_worktrees.py`。

Bot 的项目工作树位于其个人目录的 `projects/<挂载项目名>`；以名册 `projects[].directory` 或 `link_project` 返回的目录为准。集成工作树仍在 `$KONDEN_HOME/state/coding_bot/<sha256(workspace_id) 的前 20 位>/integration`。提交可以直接在 Agent 工作树里用 Git 准备，再请模型单独调用工具；工具只搬已提交的内容。

需求依据：[Git 历史、落地与同步](../../../../docs/架构/多智能体/项目与工作树.md#git-历史落地与同步)、[未知结果](../../../../docs/架构/运行语义规约.md#四条约束)。真实 Git 拥有成果关系，不自动提交、不压缩历史，不猜测中断操作的结果。

## Sub-features

- `land`：已提交的工作进入集成分支。能快进则快进，否则产生作者为该 Agent 名字、说明为 `合入 <名字> 的工作` 的合并提交。
- `withheld`：未提交的修改留在 Agent 工作树，不进入集成分支。
- `sync`：同事落地之后，另一位 `sync_branch` 才能在自己的工作树里拿到。说明为 `同步已落地的工作`。
- `conflict`：落地冲突不改集成分支。同步冲突把合并留在进行中。
- `blocked`：集成工作树里会被覆盖的文件挡住落地；Agent 工作树里会被覆盖的未提交文件挡住同步。

## How to get to it (user POV)

- 同一项目的 `Ada` 与 `Lin`，项目是 `$SCRATCH_WORKSPACE`。
- 在 Agent 工作树里准备提交，再在该 Agent 的 `消息` 里请它只调用 `land_branch` 或 `sync_branch`。
- 没有「落地」或「同步」按钮。

## Driving it with the browser

需要 `live_model_ready` 才能请模型调用工具。`code` 在该会话 journal 的命令结果里。成功调用的那一步 `tools` 里只有这一个工具。

- **落地已提交的工作**：在 `Ada` 的工作树提交 `landed.txt`，内容 `landed`。请它调用 `land_branch`。
  - (a) 结果 `LAND_COMPLETED`，`data.fast_forward` 为 true 时集成分支 HEAD 等于 `Ada` 分支 HEAD；否则集成工作树多一个作者为 `Ada`、说明为 `合入 Ada 的工作` 的合并提交。`integration` 工作树里有 `landed.txt`。用 `GET /api/sessions/<Ada>` 核对调用，再用 `git log`、`git diff` 和真实文件核对落地；Web 没有 changes 接口或文件改动页。
  - (b) 工具卡 `land_branch` 徽标 `完成`。输入框上方不出现这条调用的待处理条。
  - (c) `$SCRATCH_WORKSPACE` 的 HEAD、暂存区和 `note.txt` 不变。集成分支出现在该仓库的分支列表里是正常的，不要把人的检出切到那条分支。
  - (d) 没有新提交时再落地 → `LAND_UNCHANGED`，提示集成分支已经包含全部分支上已提交的工作，集成 HEAD 不变。
- **未提交的不带走**：`Ada` 落地之后，在它的工作树再写 `draft.txt` 且不提交，然后落地。
  - (a) 结果 `LAND_UNCHANGED`。`integration` 里没有 `draft.txt`。`Ada` 工作树里的未跟踪文件还在。
  - (b) Bot 的工作树里仍保留未提交的 `draft.txt`。
  - (c) 人的 `note.txt` 不变。
  - (d) 把 `draft.txt` 提交后再落地，它才出现在 `integration`。
- **同步**：`Ada` 已经把 `landed.txt` 落地。请 `Lin` 调用 `sync_branch`。
  - (a) 结果 `SYNC_COMPLETED`。`Lin` 的工作树里出现 `landed.txt`，内容与集成一致。能快进则 `fast_forward` 为 true，否则多一个说明为 `同步已落地的工作` 的合并提交。
  - (b) 工具卡 `sync_branch` 徽标 `完成`，没有待处理条。
  - (c) `Ada` 工作树里尚未提交的 `draft.txt` 不会出现在 `Lin` 的工作树。人的检出不变。
  - (d) `Lin` 已经含有集成分支的全部提交时再同步 → `SYNC_UNCHANGED`。
- **冲突**：`Ada` 与 `Lin` 各自提交对同一行的不同修改。`Ada` 先落地成功。请 `Lin` 落地，不要先同步。
  - (a) `Lin` 的结果为 `LAND_CONFLICT`，`data.files` 列出冲突文件，错误为 `你的分支与集成分支上已落地的工作冲突，集成分支没有改变。`。集成 HEAD 仍是 `Ada` 落地后的提交。
  - (b) 然后请 `Lin` `sync_branch`。结果 `SYNC_CONFLICT`，错误为 `同步有冲突，合并停在冲突状态。`。`Lin` 的工作树处于合并进行中，冲突文件里有冲突标记。
  - (c) 这次失败的落地没有改集成分支，也没有改人的检出。
  - (d) 冲突还在时再 `sync_branch` → `SYNC_MERGE_IN_PROGRESS`，`还有一次合并没有完成。`。在 `Lin` 的工作树 `git merge --abort` 后，工作树离开合并状态，集成分支不受影响。
- **挡住**：在 `integration` 工作树里改掉即将被落地覆盖的已跟踪文件且不提交，请 `Ada` 落地。
  - (a) 结果 `LAND_BLOCKED`，`data.files` 列出这些文件，集成 HEAD 不变，挡住的文件还在。
  - (b) 工具卡徽标 `完成`（这是 `ok: false` 的已知结果）。
  - (c) Agent 工作树里的提交都还在。不要让 Agent 去清理集成工作树。
  - (d) 在 `Lin` 的工作树留一个会被同步覆盖的未提交文件，再 `sync_branch` → `SYNC_BLOCKED`，错误含 `会被同步覆盖，同步没有执行`，`Lin` 的 HEAD 不变，该文件还在。

- **历史与恢复**：准备两次独立提交再落地，核对原提交均为集成 HEAD 的祖先，未被压缩替换；另留未提交草稿，普通回合、压缩与重启均不自动提交，草稿、HEAD 及用户 Git 配置保持。中断或停止后先等待操作收口，再按原操作核对 Git；无法证明结果时保留未知，不自动重跑，复用 [crash-recovery](crash-recovery.md)。

## Gotchas

- 2026-11-10 需求对齐（`origin/main@bab1ca4b`，Windows 隔离 Web／Chrome、真实 DeepSeek；终端为独立 ConPTY）：真实 land_branch 保留两次原始提交，未提交草稿排除，人的 HEAD 和文件不变。同步、冲突、停止与未知合并恢复未重验。证据与原始记录留在本地 `.tools/verification/requirements-20261110114551-evidence/`；本轮没有修改产品代码。

- 两个工具都必须单独调用。同一步里还有别的工具时，这一步不会落地或同步，Git 状态保持调用前的样子。
- 两个工具都要求唯一非空参数 `project`。缺失、空白或带其他参数时得到 `INVALID_ARGUMENT`，提示 `<工具名> 只接受非空的 project。`。
- Git 的其他失败（例如 `index.lock`、文件被占用）是 `LAND_FAILED` 或 `SYNC_FAILED`，`data.git` 为 Git 原文。不要把这类失败说成「文件会被覆盖」。
- 落地不经人批准。

- 2026-10-20 维护实测（`origin/main@32860f6`，Windows Chrome/ConPTY，隔离 home）：Ada 单独 land_branch 得到 LAND_COMPLETED，集成工作树包含两个子任务成果，人的 note.txt 与 HEAD 不变；Mo 单独 sync_branch 得到 SYNC_UNCHANGED。向旧分支同步新成果、冲突和阻挡未覆盖。

- 2026-10-20 编码 Skill 增量：真实 Web + deepseek-flash，按任务加载 builtin:git-collaboration，提交作者与提交者均为 Bot，land_branch 快进成功，集成目录 unittest 2 个通过；用户原目录与原始分支未变。证据 `.tools/verify-coding-skill/evidence/git-workflow/`；未重验同步、冲突与非快进合并。

- 2026-10-22 全量维护（`origin/main@4c000ca`，Windows Chrome/ConPTY，隔离 home、真实 DeepSeek）：真实指定 project 落地后同步，包含两个子任务成果，差异清零且人的原始 note.txt 不变；同步他人新增成果、冲突和阻挡未重验。
