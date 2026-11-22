# 附件分享身份与快照

## 实现与范围

`BeginShare` 查询稳定消息身份，`Snapshot` 按路径为普通文件保存独立字节，`Publish` 提交带附件引用的消息。`Read` 从当前外部会话内查消息和附件引用，`Revoke` 撤销连接访问。对应 [ExternalMessages 的发送、重试和读取](../../konden/assistant/host/external_messages.py)、[快照生成](../../konden/assistant/host/external_attachments.py) 和 [普通文件落盘](../../konden/assistant/file_attachments.py)。

一个活动会话、一条发送身份、两个不同的普通文件，允许一次源文件变化及一次提交前放弃。另一个调用方身份只探测可见性。`Abort` 表示发送方有机会运行补偿并成功清理，回收本次已写入的部分或整批原件。Host 被强制结束、清理 I/O 失败、源文件被就地修改、图片共享、用户挂载指针和材料硬链接都在模型范围之外。

## 检查的性质

`ShareOncePerIdentity` 禁止已发布的发送身份再次写快照或发布消息。`SnapshotIsStable` 对照捕获的版本、文件内容与实际读出的字节。`NoDuplicateArtifact` 检查消息中的引用各自对应磁盘上本次分享的文件。`NoDuplicateOnDisk` 检查每个来源在这次发送及其重试中至多有一份原件。`ScopeIsSession` 同时核对连接当时有效、会话、消息和附件引用。

## 实测与变异复核

在仓库根目录运行 `python scripts/ci.py formal --model 10`。Windows、Temurin 21.0.13+9、tla2tools 1.7.4、单个 TLC worker，耗时为已缓存 jar 的整条命令。两项变异分别在独立临时副本中执行。

| 动作 | 生成 / 去重状态 | 耗时 | 结果 |
|---|---:|---:|---|
| 原模型 | 261 / 116 | 1.07 秒 | 全部性质通过 |
| 在 `BeginShare` 移除 `Len(messages) = 0` | 67 / 42 | 1.13 秒 | 已发消息重入快照，第 7 个状态违反 `ShareOncePerIdentity` |
| 在 `Abort` 保持 `disk` 不变 | 65 / 46 | 1.12 秒 | 放弃后按原身份重试，第 6 个状态违反 `NoDuplicateOnDisk` |

原先实测的提交前孤儿文件已修复。两个发送方向都先写完整字节，已知未提交时只清理本次新建的原件。回执丢失时按投递身份核对 Bot 的 Journal，提交后的通知失败保留原引用。图片按内容共享，其他上传已取得的字节继续可读。批次部分失败、重试后的物理文件数及共享图片由[发送入口回归](../../tests/channels/test_external_attachments.py)核对。

## 日落条件

如果附件快照及按消息授权的分享被移除，或原件和消息改成统一原子提交且其身份与可见性已有下层验证，删除或收缩本模型。
