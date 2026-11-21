---- MODULE FactProjection ----
EXTENDS Naturals, Sequences, FiniteSets
CONSTANTS MaxFacts, MaxSnapshots, MaxWrites
VARIABLES journal, state, cache, discarded, snapshots, delivered, projection, highest, current, writes
vars == <<journal, state, cache, discarded, snapshots, delivered, projection, highest, current, writes>>

\* A history query independent of the incremental transition below.
Last(j, kind) ==
    LET indices == {0} \cup {i \in 1..Len(j) : j[i].kind = kind}
    IN CHOOSE i \in indices : \A k \in indices : k <= i
Replay(j) ==
    LET reset == Last(j, "reset")
        seen == Last(j, "observe")
    IN [count |-> Cardinality({i \in (reset+1)..Len(j) : j[i].kind = "add"}),
        memory |-> IF seen = 0 THEN "unseen" ELSE j[seen].value]
Apply(s, e) ==
    CASE e.kind = "add" -> [s EXCEPT !.count = @ + 1]
      [] e.kind = "reset" -> [s EXCEPT !.count = 0]
      [] e.kind = "observe" -> [s EXCEPT !.memory = e.value]
Snapshot == [rev |-> Len(journal), data |-> state]
Restored == IF cache # {} /\ (CHOOSE p \in cache : TRUE).rev = Len(journal)
            THEN (CHOOSE p \in cache : TRUE).data ELSE Replay(journal)

Init == /\ journal = <<>> /\ state = [count |-> 0, memory |-> "unseen"]
        /\ current = "v0" /\ writes = <<current>> /\ cache = {} /\ discarded = FALSE
        /\ snapshots = <<>> /\ delivered = {} /\ projection = Snapshot /\ highest = 0
Commit(kind) ==
    /\ Len(journal) < MaxFacts
    /\ LET e == [kind |-> kind, value |-> IF kind = "observe" THEN current ELSE "none"]
       IN /\ journal' = Append(journal, e) /\ state' = Apply(state, e)
    /\ UNCHANGED <<cache, discarded, snapshots, delivered, projection, highest, current, writes>>
WriteCurrent ==
    /\ Len(writes) <= MaxWrites
    /\ current' = IF Len(writes) = 1 THEN "v1" ELSE "deleted"
    /\ writes' = Append(writes, current')
    /\ UNCHANGED <<journal, state, cache, discarded, snapshots, delivered, projection, highest>>
Checkpoint ==
    /\ cache' = {Snapshot}
    /\ UNCHANGED <<journal, state, discarded, snapshots, delivered, projection, highest, current, writes>>
Discard ==
    /\ cache # {} /\ ~discarded /\ discarded' = TRUE /\ cache' = {}
    /\ UNCHANGED <<journal, state, snapshots, delivered, projection, highest, current, writes>>
Rebuild ==
    /\ state' = Restored
    /\ UNCHANGED <<journal, cache, discarded, snapshots, delivered, projection, highest, current, writes>>
Capture ==
    /\ Len(snapshots) < MaxSnapshots /\ snapshots' = Append(snapshots, Snapshot)
    /\ UNCHANGED <<journal, state, cache, discarded, delivered, projection, highest, current, writes>>
Deliver(i) ==
    /\ i \notin delivered /\ delivered' = delivered \cup {i}
    /\ LET response == snapshots[i]
       IN /\ projection' = IF response.rev >= projection.rev THEN response ELSE projection
          /\ highest' = IF response.rev > highest THEN response.rev ELSE highest
    /\ UNCHANGED <<journal, state, cache, discarded, snapshots, current, writes>>

Next == \/ \E k \in {"add", "reset", "observe"} : Commit(k)
        \/ WriteCurrent \/ Checkpoint \/ Discard \/ Rebuild \/ Capture
        \/ \E i \in 1..Len(snapshots) : Deliver(i)
Spec == Init /\ [][Next]_vars
ReplayDeterministic == state = Replay(journal)
MonotonicProjection ==
    projection.rev = highest /\ projection.data = Replay(SubSeq(journal, 1, projection.rev))
NoReverseRecovery == current = writes[Len(writes)]
CacheDiscardable == Restored = Replay(journal)
AppendOnly == [][Len(journal') >= Len(journal) /\ SubSeq(journal', 1, Len(journal)) = journal]_vars
====
