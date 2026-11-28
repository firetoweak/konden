# 命令执行与接管

## 实现与范围

一个会话提交一组命令。`Dispatch` 追加尝试，`Observe` 表示执行者取得可核对的成功或失败证据，`Complete` 尝试把证据写入 Journal。接管与旧结果竞争，`MarkOrphan` 为旧执行者留下的尝试登记未知。事件种类与写入入口见 [Dispatcher](../../konden/runtime/dispatcher.py)、[SQLite Journal](../../konden/runtime/journal/sqlite.py) 和 [Worker 恢复入口](../../konden/assistant/host/worker.py)。

范围为两个命令、两个 Worker、一次接管。租约过期后旧执行者仍有提交机会，成功接管才换代。源码中的代次检查用于 Step claim，Attempt 终局按命令、尝试和派发事实核对；Host 先取得独占权并确认旧执行者离场。模型用代次围住两者，并逐条登记 orphan，实际 `prepare_recovery` 在一个事务中登记整批。这里没有验证操作系统的进程隔离，也没有覆盖领域以后补交确定结果的协议。

## 检查的性质

`NoDuplicateAttempt`、`NoDoubleTerminal` 和 `SingleWriter` 检查每个命令的尝试数、每个尝试的结果数以及写入时的代次。`UnknownStaysUnknown` 要求确定结果有对应证据，`NoBlindRetry` 禁止在 orphan 之后再启动。`EventuallyResolved` 在有限命令、有限故障和启用动作最终获得执行的弱公平前提下，要求已开始的尝试最终得到结果或 orphan 标记。

## 实测与变异复核

在仓库根目录运行 `python scripts/ci.py formal --model 1`。Windows、Temurin 21.0.13+9、tla2tools 1.7.4、单个 TLC worker，生成 2,222 个状态，去重后 1,366 个。已缓存 jar 的整条命令用时 1.41 秒。

在模型临时副本中，把 `Dispatch` 的 `~Started(c)` 改成 `(~Started(c) \/ (generation = 2 /\ Orphan(c)))`，让新 Worker 可以重派原命令。用同一条命令运行，TLC 报告 `NoDuplicateAttempt`。最短反例中，第一代启动命令，第二代接管、登记 orphan，随后为同一命令再次追加尝试，共 7 个状态。原模型复核运行通过。

同一命令还复核了两条变异。移除 `Complete` 的 `w = generation` 后，旧 Worker 在接管后写入，触发 `SingleWriter`。把 `MarkOrphan` 的未知值改成失败，触发 `UnknownStaysUnknown`。

## 日落条件

如果命令尝试与接管机制移除，或底层已把尝试唯一性、失权和未知结果的提交顺序一起纳入可执行验证，删除本模型。
