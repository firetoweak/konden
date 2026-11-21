# 前台打断与终态竞争

## 实现与范围

`CloseContinuation` 关闭续步，`Terminate` 对已退出、正在运行或尚未启动的同一命令完成终止核对，`RecordInterrupted` 才记录语义上的未知。`Exit` 与 `RecordNatural` 分开，允许自然退出证据与中断回执争同一终态。对应 [SessionScheduler.cancel_turn](../../konden/assistant/runner.py)、[CommandInterrupts](../../konden/tools/builtin/command_interrupts.py) 和 [CommandManager](../../konden/execution/processes.py)。

一个 Step 配两个前台命令，允许一次打断及终止失败。命令身份在此范围内唯一映射到尝试。源码把已取得的中断工具回执装入 `OutcomeStatus.SUCCEEDED`，其中 `COMMAND_INTERRUPTED` 才表示外部操作的结果未知。模型记录的是后者。进程树、信号送达和部分输出内容须用平台测试验证。

## 检查的性质

`InterruptOrdering` 核对结果登记时已经关闭续步并确认终止。`SingleTerminalPerIdentity` 约束自然结束与打断只接纳一个终局，`InterruptionIsUnknown` 检查其含义。`ProcessExitBeforeRecord` 按原身份接纳退出证据并检查启动次数，`NoLateStart` 检查每次实际启动时续步尚未关闭。终止失败可以留下尚未解决的命令。

## 实测与变异复核

在仓库根目录运行 `python scripts/ci.py formal --model 3`。Windows、Temurin 21.0.13+9、tla2tools 1.7.4、单个 TLC worker，生成 664 个状态，去重后 459 个。已缓存 jar 的整条命令用时 1.03 秒。

在模型临时副本中，把 `RecordInterrupted` 的 `c \in stops` 改成 `closed`，允许关闭续步后立即登记中断。用同一条命令运行，TLC 报告 `InterruptOrdering`。初态、关闭续步、登记中断共 3 个状态，记录出现时没有终止确认。原模型复核运行通过。

## 日落条件

如果前台打断被移除，或同一执行层已经共同验证关闭续步、终止确认和 Journal 终局的顺序，删除本模型。
