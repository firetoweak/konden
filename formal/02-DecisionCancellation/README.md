# 冻结批次、提交与取消

## 实现与范围

`Prepare` 读取推进位置，`Acquire` 在该位置取得冻结批次。`Commit` 原子消费整批。取消请求保存目标，若目标已经提交，`CloseContinuation` 只关闭对应 Step 的续步。实现在 [SessionScheduler](../../konden/assistant/runner.py)、[AgentRuntime](../../konden/runtime/runtime.py) 和 [Journal 的 claim 与消费事务](../../konden/runtime/journal/sqlite.py)。

两个输入身份可以分别到达或合成一批，两个 Worker 竞争有效 claim，最多一次取消与一次失败回滚。源码对预检与开始推进之间的 Journal 位置做比较，提交时核对已领取的请求和输入消费。模型调用期间收到新输入，当前 Step 仍可基于原冻结切面提交，后到输入保留给下一步。外部事实的优先级、跨进程续租和实际模型调用时长留给相应实现。

## 检查的性质

`AtMostOnceConsume`、`CancelCommitExclusive` 保证提交批次互不重复，取消与提交互斥。`NoStaleDecision` 对照预检和领取位置，`NoConcurrentDecision` 限制有效 claim，`NoSwallowedInput` 对照原取消范围、待处理输入和已提交批次。`EventuallyProgress` 在回滚次数有界且调度弱公平时要求已到达输入最终获提交或明确取消。 `CancellationSettles` 还要求已定位到运行中决策的取消最终收口。

## 实测与变异复核

在仓库根目录运行 `python scripts/ci.py formal --model 2`。Windows、Temurin 21.0.13+9、tla2tools 1.7.4、单个 TLC worker，生成 2,500 个状态，去重后 1,212 个。已缓存 jar 的整条命令用时 2.12 秒。

在模型临时副本中，删除 `Commit` 的 `f.batch \cap cancelled = {}` 条件，保留对已提交批次的检查。用同一条命令运行，TLC 报告 `CancelCommitExclusive`。TLC 找到 7 个状态，输入到达并冻结，取消先落账，同一批次随后又提交。原模型复核运行通过。

另一次变异删除 `Acquire` 的 `p.seen = Position`。预检和领取之间到达新输入后，旧位置取得 claim 并提交，TLC 报 `NoStaleDecision`。

## 日落条件

如果决策和取消不再争用已冻结的批次，或消费与关闭续步的整组竞争已由更低层模型验证，删除本模型。
