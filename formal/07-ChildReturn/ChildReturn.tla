---- MODULE ChildReturn ----
EXTENDS Naturals, Sequences, FiniteSets
CONSTANT Child
VARIABLES delegated, phase, starts, childFacts, offered, accepted, view, reclaim, up, relation, fault
vars == <<delegated, phase, starts, childFacts, offered, accepted, view, reclaim, up, relation, fault>>
Reply == [child |-> Child, id |-> "reply", kind |-> "speech"]
Closure(kind) == [child |-> Child, id |-> "closed", kind |-> kind]
AcceptedIds == {accepted[i].id : i \in 1..Len(accepted)}
OwnPending == IF delegated /\ "closed" \notin AcceptedIds THEN {Child} ELSE {}

Init == /\ delegated = FALSE /\ phase = "absent" /\ starts = 0 /\ childFacts = {}
        /\ offered = {} /\ accepted = <<>> /\ view = "absent" /\ reclaim = FALSE
        /\ up = TRUE /\ relation = {} /\ fault = "none"
Delegate ==
    /\ up /\ ~delegated /\ delegated' = TRUE /\ view' = "delegated" /\ relation' = {Child}
    /\ UNCHANGED <<phase, starts, childFacts, offered, accepted, reclaim, up, fault>>
CreateChild ==
    /\ up /\ Child \in relation /\ phase = "absent" /\ phase' = "ready"
    /\ UNCHANGED <<delegated, starts, childFacts, offered, accepted, view, reclaim, up, relation, fault>>
Start ==
    /\ up /\ Child \in relation /\ phase = "ready" /\ phase' = "running" /\ starts' = starts + 1
    /\ UNCHANGED <<delegated, childFacts, offered, accepted, view, reclaim, up, relation, fault>>
Idle ==
    /\ phase = "running" /\ phase' = "idle"
    /\ UNCHANGED <<delegated, starts, childFacts, offered, accepted, view, reclaim, up, relation, fault>>
WriteReply ==
    /\ phase \in {"running", "idle"} /\ ~reclaim /\ Reply \notin childFacts
    /\ childFacts' = childFacts \cup {Reply}
    /\ UNCHANGED <<delegated, phase, starts, offered, accepted, view, reclaim, up, relation, fault>>
RequestReclaim ==
    /\ up /\ Child \in relation /\ phase \in {"running", "idle"} /\ ~reclaim /\ reclaim' = TRUE
    /\ UNCHANGED <<delegated, phase, starts, childFacts, offered, accepted, view, up, relation, fault>>
Interrupt ==
    /\ fault = "none" /\ phase = "running" /\ phase' = "interrupted" /\ fault' = "interrupt"
    /\ UNCHANGED <<delegated, starts, childFacts, offered, accepted, view, reclaim, up, relation>>
WriteClosure ==
    /\ phase = "interrupted" \/ (reclaim /\ phase \in {"running", "idle"})
    /\ LET kind == IF phase = "interrupted" THEN "unknown" ELSE "reclaimed"
       IN childFacts' = childFacts \cup {Closure(kind)}
    /\ phase' = "closed"
    /\ UNCHANGED <<delegated, starts, offered, accepted, view, reclaim, up, relation, fault>>

Deliver(r) ==
    /\ up /\ r \in childFacts /\ offered' = offered \cup {r}
    /\ UNCHANGED <<delegated, phase, starts, childFacts, accepted, view, reclaim, up, relation, fault>>
Accept(r) ==
    /\ up /\ r \in offered /\ r.id \notin AcceptedIds
    /\ r.id = "closed" \/ "closed" \notin AcceptedIds
    /\ accepted' = Append(accepted, r)
    /\ view' = IF r.id = "closed" THEN r.kind ELSE IF view = "delegated" THEN "heard" ELSE view
    /\ relation' = IF r.id = "closed" THEN {} ELSE relation
    /\ UNCHANGED <<delegated, phase, starts, childFacts, offered, reclaim, up, fault>>
CrashParent ==
    /\ up /\ fault = "none" /\ fault' = "parent" /\ up' = FALSE /\ relation' = {} /\ offered' = {}
    /\ UNCHANGED <<delegated, phase, starts, childFacts, accepted, view, reclaim>>
Recover ==
    /\ ~up /\ up' = TRUE /\ relation' = OwnPending
    /\ UNCHANGED <<delegated, phase, starts, childFacts, offered, accepted, view, reclaim, fault>>

Transport == CreateChild \/ Start \/ Recover \/ WriteClosure
             \/ (\E r \in childFacts : Deliver(r)) \/ (\E r \in offered : Accept(r))
Next == Delegate \/ Transport \/ WriteReply \/ Idle \/ RequestReclaim \/ Interrupt \/ CrashParent
Spec == Init /\ [][Next]_vars /\ WF_vars(Transport)
AtMostOnceAccept == Len(accepted) = Cardinality(AcceptedIds)
DispatchIsNotCompletion ==
    /\ IF Len(accepted) = 0 THEN view = (IF delegated THEN "delegated" ELSE "absent")
          ELSE view \in {"heard", "unknown", "reclaimed"}
    /\ \A i \in 1..Len(accepted) : accepted[i] \in childFacts
InterruptionStaysUnknown ==
    /\ starts <= 1
    /\ (fault = "interrupt" /\ phase = "closed" => Closure("unknown") \in childFacts)
    /\ (fault = "interrupt" /\ "closed" \in AcceptedIds => view = "unknown")
RecoveryFromOwnDelegations == up => relation = OwnPending
ReclaimIsExplicit == (Closure("reclaimed") \in childFacts \/ view = "reclaimed") => reclaim
EventuallyReported == WF_vars(WriteReply) => (delegated ~> Len(accepted) > 0)
====
