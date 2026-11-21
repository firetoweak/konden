# 后台交接与独立补报

## 实现与范围

`Handoff` 提交原调用的后台身份，`Follow` 处理原 Step 的续步。后续 `Report` 使用后台命令身份单独投递完成事实；`Interrupt` 与 `StopBackground` 拥有不同的目标。对应 [CommandHost](../../konden/assistant/host/commands.py)、[命令事实投影](../../konden/assistant/commands.py) 和 [进程管理](../../konden/execution/processes.py)。

两个命令中一个可转后台。模拟前台自然结束、后台完成或未知、显式停止、当前轮打断和重复补报机会。源码允许后台完成通知请求一次新的判断；它独立于原 Step 的收口。停止与自然退出相撞时源码也能认回已完成状态，这里用先发生的动作表示。跨 Host 重启后的文件和进程核对留给进程测试。

## 检查的性质

`BackgroundDoesNotBlockFollowUp` 只假设可执行续步会得到调度，后台可以一直运行。`BackgroundReportedOnce` 核对补报次数和身份，`BackgroundEventuallyReported` 在结果已知且投递弱公平时要求最终补报。`InterruptIsForegroundOnly` 限制前台打断目标，`StopIsNotInterrupt` 核对独立停止的结果。

## 实测与变异复核

在仓库根目录运行 `python scripts/ci.py formal --model 5`。Windows、Temurin 21.0.13+9、tla2tools 1.7.4、单个 TLC worker，生成 119 个状态，去重后 64 个。已缓存 jar 的整条命令用时 1.00 秒。

在模型临时副本中，删除 `Interrupt` 的 `c \notin background` 条件，保留对存活进程的检查。用同一条命令运行，TLC 报告 `InterruptIsForegroundOnly`。后台交接、关闭续步、对已经后台化的进程发出前台打断，最短轨迹共 4 个状态。原模型复核运行通过。

## 日落条件

如果原调用终局和后台补报合并为同一生命周期，或移除后台交接能力，删除本模型。
