# MCP 连接所有者、失效与清理

## 实现与范围

`Open` 建立物理连接及其 owner，`Started` 与 `Adopt` 分开，允许失效、关闭或另一候选先到。管理器选中可复用连接以后，迟到的连接进入清理。`StartFailed` 和 `Cleanup` 分别保留启动与整理错误。对应 [McpClientManager 和 _SdkConnectionOwner](../../konden/mcp/client_manager.py) 和 [生命周期错误用例](../../tests/mcp/test_lifecycle_errors.py)。

一个 server 记录，最多两个物理句柄、两次失败和一次失效。两个句柄可表示一次重试，也可争用一次发布机会。源码在管理器锁外打开连接，允许同一 server 同时有两个候选 owner；唯一性属于物理句柄和已发布缓存。这条模型只检查新取得句柄的调用，已借出的请求、SDK 传输与异常组的嵌套顺序留给运行时检查。

## 检查的性质

`OwnerStartsOnce` 同时检查每个物理句柄至多启动一个 owner 和至多发布一个可复用句柄。`FailuresBothReported` 对比实际发生的错误与关闭完成时报告的错误。`CloseIsFinal` 覆盖关闭后的可用性，`NoReuseAfterInvalidate` 对照打开时与新调用取得连接时的代次。

## 实测与变异复核

在仓库根目录运行 `python scripts/ci.py formal --model 9`。Windows、Temurin 21.0.13+9、tla2tools 1.7.4、单个 TLC worker，生成 2,481 个状态，去重后 1,000 个。已缓存 jar 的整条命令用时 1.02 秒。

在模型临时副本中，在 `Cleanup` 中将 `primary \cup cleanup` 改为 `IF primary = {} THEN cleanup ELSE primary`，使两次失败同时发生时只保留启动错误。用同一条命令运行，TLC 报告 `FailuresBothReported`。启动失败，清理再次失败，返回中缺少清理错误，共 4 个状态。原模型复核运行通过。

如需复核 server 级唯一性的边界，在临时副本的 `.cfg` 增加 `INVARIANT OneOwnerPerServer` 并重跑同一命令。原始动作连续打开两个候选，TLC 在第 3 个状态给出反例；两个候选都尚未发布。

真实管理器的补充核对使用两个同时请求同一 server 的调用，在 SDK 打开入口等待两个 owner 均到达后再放行。两个调用获得同一缓存句柄，两个 owner 中只有胜出的一个仍存活。

## 日落条件

如果 SDK 接管整个 owner 生命周期，并已有验证覆盖并发取得、失效、关闭和双错误传播，删除本模型。
