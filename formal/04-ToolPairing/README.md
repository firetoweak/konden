# 并行结果收口与调用配对

## 实现与范围

`Receive` 按到达顺序接纳结果并判断相关命令是否收齐，`Output` 单独保留部分输出。`ProtocolPairs` 用调用身份配对已知结果，为尚未返回的调用生成等待占位。续步选择见 [StateProjector](../../konden/runtime/state.py)，提供给模型的协议消息见 [上下文投影](../../konden/assistant/context/projection.py)。

一个 Step 有三个调用，其中两个要求继续判断，一个只留结果。允许任意结果顺序，每个调用最多展示一次部分输出，终局可为成功、已知失败或收口后的未知。检查对象是由本组工具结果独自触发的决策。人的插话和领域恢复后补交的真实结果另有续步规则。

## 检查的性质

`OneFollowUpPerGroup` 和 `WakeAfterGroup` 合起来要求所有相关调用收口时恰有一次续步。`NoPartialAsTerminal` 区分输出证据与已收口结果，`NoWakeForIrrelevant` 限制唤醒来源。`EveryCallPaired` 检查每次投影中调用与结果身份一一对应，在结果处理弱公平且运行最终收口的前提下，占位最终获得可配对的终局。

## 实测与变异复核

在仓库根目录运行 `python scripts/ci.py formal --model 4`。Windows、Temurin 21.0.13+9、tla2tools 1.7.4、单个 TLC worker，生成 2,102 个状态，去重后 1,809 个。已缓存 jar 的整条命令用时 1.20 秒。

在模型临时副本中，把 `Receive` 中的 `closes` 改成 `c \in Relevant`，使每个相关结果都触发续步。用同一条命令运行，TLC 报告 `OneFollowUpPerGroup`。第一个相关结果到达时另一个仍在运行，TLC 在第 3 个状态就发现提前续步。原模型复核运行通过。

## 日落条件

如果工具不再并行收口，或同一协议层已经同时验证按身份配对与成组触发，删除本模型。
