---- MODULE DecisionCancellation ----
EXTENDS Naturals, Sequences, FiniteSets
CONSTANTS Inputs, Workers
VARIABLES arrived, pending, prep, claims, decisions, cancelled, closed, ticket, cancelScope, rolledBack
vars == <<arrived, pending, prep, claims, decisions, cancelled, closed, ticket, cancelScope, rolledBack>>
Batches == {decisions[i].batch : i \in 1..Len(decisions)}
Consumed == UNION Batches
Position == Cardinality(arrived) + Len(decisions) + Cardinality(cancelled)

Init ==
    /\ arrived = {} /\ pending = {} /\ prep = {} /\ claims = {} /\ decisions = <<>>
    /\ cancelled = {} /\ closed = {} /\ ticket = {} /\ cancelScope = {} /\ rolledBack = FALSE

Arrive(i) ==
    /\ i \notin arrived /\ arrived' = arrived \cup {i} /\ pending' = pending \cup {i}
    /\ UNCHANGED <<prep, claims, decisions, cancelled, closed, ticket, cancelScope, rolledBack>>

Prepare(w) ==
    /\ prep = {} /\ pending # {}
    /\ prep' = {[worker |-> w, batch |-> pending, seen |-> Position]}
    /\ UNCHANGED <<arrived, pending, claims, decisions, cancelled, closed, ticket, cancelScope, rolledBack>>
Acquire(p) ==
    /\ claims = {} /\ p.seen = Position /\ p.batch \subseteq pending
    /\ ticket \cap p.batch = {} \/ ticket = p.batch
    /\ claims' = claims \cup {[worker |-> p.worker, batch |-> p.batch, seen |-> p.seen, atClaim |-> Position]}
    /\ prep' = {}
    /\ UNCHANGED <<arrived, pending, decisions, cancelled, closed, ticket, cancelScope, rolledBack>>
Discard(p) ==
    /\ p.seen # Position /\ prep' = {}
    /\ UNCHANGED <<arrived, pending, claims, decisions, cancelled, closed, ticket, cancelScope, rolledBack>>

Commit(f) ==
    /\ f.batch \cap Consumed = {} /\ f.batch \cap cancelled = {}
    /\ decisions' = Append(decisions, f) /\ pending' = pending \ f.batch /\ claims' = claims \ {f}
    /\ UNCHANGED <<arrived, prep, cancelled, closed, ticket, cancelScope, rolledBack>>
Release(f) ==
    /\ (f.batch \cap cancelled # {} \/ ~rolledBack)
    /\ rolledBack' = TRUE /\ claims' = claims \ {f}
    /\ UNCHANGED <<arrived, pending, prep, decisions, cancelled, closed, ticket, cancelScope>>

RequestCancel ==
    /\ cancelScope = {} /\ claims # {}
    /\ ticket' = (CHOOSE f \in claims : TRUE).batch /\ cancelScope' = ticket'
    /\ UNCHANGED <<arrived, pending, prep, claims, decisions, cancelled, closed, rolledBack>>
CancelIdle ==
    /\ cancelScope = {} /\ claims = {} /\ pending # {}
    /\ cancelScope' = pending /\ cancelled' = cancelled \cup pending /\ pending' = {}
    /\ UNCHANGED <<arrived, prep, claims, decisions, closed, ticket, rolledBack>>
Cancel ==
    /\ ticket # {} /\ ticket \subseteq pending
    /\ cancelled' = cancelled \cup ticket /\ pending' = pending \ ticket /\ ticket' = {}
    /\ UNCHANGED <<arrived, prep, claims, decisions, closed, cancelScope, rolledBack>>
CloseContinuation ==
    /\ ticket # {} /\ ticket \in Batches /\ closed' = closed \cup {ticket} /\ ticket' = {}
    /\ UNCHANGED <<arrived, pending, prep, claims, decisions, cancelled, cancelScope, rolledBack>>

Work == \/ \E w \in Workers : Prepare(w)
        \/ \E p \in prep : Acquire(p) \/ Discard(p)
        \/ \E f \in claims : Commit(f) \/ Release(f)
        \/ Cancel \/ CloseContinuation
Next == Work \/ RequestCancel \/ CancelIdle \/ \E i \in Inputs : Arrive(i)
Spec == Init /\ [][Next]_vars /\ WF_vars(Work)

AtMostOnceConsume == \A i \in 1..Len(decisions), j \in 1..Len(decisions) :
    i # j => decisions[i].batch \cap decisions[j].batch = {}
NoStaleDecision == \A i \in 1..Len(decisions) : decisions[i].seen = decisions[i].atClaim
CancelCommitExclusive == cancelled \cap Consumed = {}
NoConcurrentDecision == Cardinality(claims) <= 1
NoSwallowedInput ==
    /\ pending = arrived \ (Consumed \cup cancelled) /\ cancelled \subseteq cancelScope
    /\ closed \subseteq Batches
    /\ (cancelScope \in Batches /\ ticket = {} => cancelScope \in closed)
EventuallyProgress == \A i \in Inputs : i \in arrived ~> i \in (Consumed \cup cancelled)
CancellationSettles == (ticket # {}) ~> (ticket = {})
====
