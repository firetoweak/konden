# 附件分享身份与快照

## 实现与范围

`BeginShare` 查询稳定消息身份，`Snapshot` 按路径为普通文件保存独立字节，`Publish` 提交带附件引用的消息。`Read` 从当前外部会话内查消息和附件引用，`Revoke` 撤销连接访问。对应 [ExternalMessages 的发送、重试和读取](../../konden/assistant/host/external_messages.py)、[快照生成](../../konden/assistant/host/external_attachments.py) 和 [普通文件落盘](../../konden/assistant/file_attachments.py)。

一个活动会话、一条发送身份、两个普通文件，允许一次源文件变化及一次提交前放弃。另一个调用方身份只探测可见性。源码可撤销连接或删除外部会话，尚无逐附件撤销动作。这里假定一次读取能取得一个文件版本；写入途中源文件被就地修改、图片内容寻址、用户挂载的可变指针和材料目录的硬链接均另有语义。

## 检查的性质

`ShareOncePerIdentity` 禁止已发布的发送身份再次写快照或发布消息。`SnapshotIsStable` 对照捕获的版本、文件内容与实际读出的字节。`NoDuplicateArtifact` 检查一个来源最多一份可发布的快照，已经放弃的副本不会进入消息。`ScopeIsSession` 同时核对连接当时有效、会话、消息和附件引用。

## 实测与变异复核

在仓库根目录运行 `python scripts/ci.py formal --model 10`。Windows、Temurin 21.0.13+9、tla2tools 1.7.4、单个 TLC worker，生成 572 个状态，去重后 271 个。已缓存 jar 的整条命令用时 0.99 秒。

在模型临时副本中，删除 `BeginShare` 的 `Len(messages) = 0` 条件，使同一发送身份再次进入文件生成。用同一条命令运行，TLC 报告 `ShareOncePerIdentity`。第一次分享已发布，重试开始为同一个文件写第二份快照，反例共 7 个状态。原模型复核运行通过。

**物理副本的已知缺口。** 在临时副本的 `.cfg` 增加 `INVARIANT NoDuplicateOnDisk`，重跑同一命令，无须修改动作。首次落盘后、提交消息前放弃，再按原身份重试，第 6 个状态出现两份物理文件。另在真实 `send_from_bot` 的事实投递前注入一次异常，使用相同命令身份重试，实测会话有一条消息，附件目录有两份内容相同的原件。当前快照写入和消息提交分属两个步骤。

## 日落条件

如果附件快照及按消息授权的分享被移除，或原件和消息改成统一原子提交且其身份与可见性已有下层验证，删除或收缩本模型。
