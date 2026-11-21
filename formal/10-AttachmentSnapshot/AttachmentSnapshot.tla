---- MODULE AttachmentSnapshot ----
EXTENDS Naturals, Sequences, FiniteSets
CONSTANTS Session, Stranger, First, Second
Paths == <<First, Second>>
Attachments == {First, Second}
VARIABLES source, changed, writing, stage, disk, aborted, messages, active, reads
vars == <<source, changed, writing, stage, disk, aborted, messages, active, reads>>
Refs(seq) == {seq[i] : i \in 1..Len(seq)}
Files(a) == {i \in 1..Len(disk) : disk[i].path = a}

Init == /\ source = [a \in Attachments |-> 1] /\ changed = FALSE
        /\ writing = FALSE /\ stage = <<>> /\ disk = <<>> /\ aborted = FALSE
        /\ messages = <<>> /\ active = TRUE /\ reads = {}
BeginShare ==
    /\ active /\ ~writing /\ Len(messages) = 0 /\ writing' = TRUE
    /\ UNCHANGED <<source, changed, stage, disk, aborted, messages, active, reads>>
Snapshot ==
    /\ writing /\ Len(stage) < Len(Paths)
    /\ LET a == Paths[Len(stage)+1]
       IN disk' = Append(disk, [path |-> a, captured |-> source[a], bytes |-> source[a]])
    /\ stage' = Append(stage, Len(disk'))
    /\ UNCHANGED <<source, changed, writing, aborted, messages, active, reads>>
Publish ==
    /\ writing /\ Len(stage) = Len(Paths)
    /\ messages' = Append(messages, [session |-> Session, id |-> "send", refs |-> stage])
    /\ writing' = FALSE /\ stage' = <<>>
    /\ UNCHANGED <<source, changed, disk, aborted, active, reads>>
Abort ==
    /\ writing /\ ~aborted /\ aborted' = TRUE
    /\ disk' = SubSeq(disk, 1, Len(disk) - Len(stage))
    /\ writing' = FALSE /\ stage' = <<>>
    /\ UNCHANGED <<source, changed, messages, active, reads>>
Change(a) ==
    /\ ~changed /\ changed' = TRUE /\ source' = [source EXCEPT ![a] = 2]
    /\ UNCHANGED <<writing, stage, disk, aborted, messages, active, reads>>
Retry ==
    /\ active /\ Len(messages) > 0
    /\ UNCHANGED vars
Revoke ==
    /\ active /\ ~writing /\ active' = FALSE
    /\ UNCHANGED <<source, changed, writing, stage, disk, aborted, messages, reads>>
Read(who, id, ref) ==
    /\ active /\ who = Session
    /\ \E i \in 1..Len(messages) : messages[i].id = id /\ ref \in Refs(messages[i].refs)
    /\ reads' = reads \cup {[who |-> who, id |-> id, ref |-> ref, bytes |-> disk[ref].bytes,
                             active |-> active]}
    /\ UNCHANGED <<source, changed, writing, stage, disk, aborted, messages, active>>

Next == \/ BeginShare \/ Snapshot \/ Publish \/ Abort \/ Retry \/ Revoke
        \/ \E a \in Attachments : Change(a)
        \/ \E who \in {Session, Stranger}, id \in {"send", "other"}, ref \in 1..Len(disk) : Read(who, id, ref)
Spec == Init /\ [][Next]_vars
ShareOncePerIdentity == Len(messages) <= 1 /\ (Len(messages) > 0 => Len(stage) = 0)
SnapshotIsStable ==
    /\ \A i \in 1..Len(disk) : disk[i].bytes = disk[i].captured
    /\ \A r \in reads : r.bytes = disk[r.ref].captured
NoDuplicateArtifact ==
    \A i \in 1..Len(messages) :
        /\ Cardinality(Refs(messages[i].refs)) = Len(Paths)
        /\ Refs(messages[i].refs) \subseteq 1..Len(disk)
        /\ \A j \in 1..Len(Paths) : disk[messages[i].refs[j]].path = Paths[j]
ScopeIsSession == \A r \in reads :
    r.active /\ \E i \in 1..Len(messages) :
        r.who = messages[i].session /\ r.id = messages[i].id /\ r.ref \in Refs(messages[i].refs)

NoDuplicateOnDisk == \A a \in Attachments : Cardinality(Files(a)) <= 1
====
