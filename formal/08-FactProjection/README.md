# 事实重放、缓存与投影版本

## 实现与范围

`Commit` 以增量归约推进状态，`Replay` 独立查询上次重置之后的增量及最后一次观察。`Capture` 产生有版本的响应，`Deliver` 可按任意顺序接纳。记忆当前值由独立写入动作拥有。对应 [Journal 的追加、快照及 Checkpoint](../../konden/runtime/journal/sqlite.py)、[运行态读取与重放](../../konden/runtime/runtime.py)、[状态归约](../../konden/runtime/state.py)、[Web 会话投影合并](../../web/src/api/kondenApi.ts) 和 [当前记忆存储](../../konden/assistant/memory.py)。

最多三条会话事实、两个可乱序抵达的投影响应、一次缓存丢弃和两次记忆写入。用加一、重置与观察区分顺序，用新值和删除区分当前正文与历史。Journal 长度代表此模型的有序版本；源码还校验 Checkpoint 指纹，Web 使用正式 revision 并接纳同版本响应。模型假定同版本来自同一事实切面，持久格式损坏、版本构造和浏览器渲染留给实现检查。

## 检查的性质

`ReplayDeterministic` 比对增量与历史查询两个结果。`MonotonicProjection` 将已展示版本与所有已送达响应的最高版本对照，并核对版本对应的内容。`NoReverseRecovery` 检查当前正文仍来自最后一次正式写入，`CacheDiscardable` 检查有无有效缓存时的重建结果。`AppendOnly` 跨相邻状态比较已提交日志的完整前缀。

## 实测与变异复核

在仓库根目录运行 `python scripts/ci.py formal --model 8`。Windows、Temurin 21.0.13+9、tla2tools 1.7.4、单个 TLC worker，生成 420,402 个状态，去重后 96,506 个。已缓存 jar 的整条命令用时 2.81 秒。

在模型临时副本中，将 `Deliver` 中赋给 `projection'` 的版本判断改为直接采用 `response`，保留独立记录的最高送达版本。用同一条命令运行，TLC 报告 `MonotonicProjection`。先捕获版本 0，提交事实后捕获版本 1，先送达 1 再送达 0，共 6 个状态。原模型复核运行通过。

## 日落条件

如果会话不再通过有版本投影向外供给事实，或事实追加、重放与投影接纳已由下层一体验证，删除或缩减对应切面。
