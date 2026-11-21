# 父子委派、回传与回收

## 实现与范围

`Delegate` 表示父 Step 已提交带稳定身份的委派命令，`CreateChild` 和 `Start` 分别建立子任务和启动执行。子回复命令或结束事实先留在子时间线，`Deliver` 与父的 `Accept` 可以隔着父进程故障。普通回复和结束通知使用不同投递身份。对应 [委派投影、回传身份与 rehydrate](../../konden/assistant/subagent/subagent.py)、[Host 的进程与消息路由](../../konden/assistant/host/supervisor.py) 和 [Worker 的中断恢复](../../konden/assistant/host/worker.py)。

一个父、一个子、一次普通回复，最多一次父崩溃或子操作中断。父的待协作关系从已提交委派和结束事实恢复，子可以空闲；显式回收和中断通知都经过子的结束事实。实际中断通知使用 `failure` 字段说明存在结果未取得的操作，模型用 `unknown` 保留这个含义。多轮指令、已识别的业务失败、工作区内容与父的成果验收均在边界之外。

## 检查的性质

`AtMostOnceAccept` 核对父按回传身份接纳一次。`DispatchIsNotCompletion` 检查回传前父只看到委派；`InterruptionStaysUnknown` 禁止中断后再启动或报告确定结果。`RecoveryFromOwnDelegations` 和 `ReclaimIsExplicit` 检查恢复依据及显式回收原因。`EventuallyReported` 以传输可推进、子最终愿意回复或已走到回收或中断为前提。健康但持续沉默的子可以一直等候。

## 实测与变异复核

在仓库根目录运行 `python scripts/ci.py formal --model 7`。Windows、Temurin 21.0.13+9、tla2tools 1.7.4、单个 TLC worker，生成 332 个状态，去重后 127 个。已缓存 jar 的整条命令用时 1.12 秒。

在模型临时副本中，删除 `Accept` 中的 `r.id \notin AcceptedIds` 条件，使同一投递可以再写一次。用同一条命令运行，TLC 报告 `AtMostOnceAccept`。子写下普通回复并投递，父连续接纳同一身份两次，共 8 个状态。原模型复核运行通过。

将 `WriteClosure` 的中断值换成 `failed`，沿原路径复核，TLC 在第 6 个状态报 `InterruptionStaysUnknown`。

## 日落条件

如果父子协作协议移除，或委派、回传去重和显式结束全部下沉到已经验证的统一协议，删除本模型。
