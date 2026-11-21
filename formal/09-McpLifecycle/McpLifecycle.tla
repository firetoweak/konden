---- MODULE McpLifecycle ----
EXTENDS Naturals, FiniteSets
CONSTANTS Handles, MaxFailures
VARIABLES phase, owners, openedAt, epoch, published, managerClosed, startErrors, cleanupErrors, reported, uses
vars == <<phase, owners, openedAt, epoch, published, managerClosed, startErrors, cleanupErrors, reported, uses>>
FailureCount == Cardinality(startErrors) + Cardinality(cleanupErrors)

Init == /\ phase = [h \in Handles |-> "unused"] /\ owners = [h \in Handles |-> 0]
        /\ openedAt = [h \in Handles |-> 0] /\ epoch = 0 /\ published = {} /\ managerClosed = FALSE
        /\ startErrors = {} /\ cleanupErrors = {} /\ reported = [h \in Handles |-> {}] /\ uses = {}
Open(h) ==
    /\ ~managerClosed /\ published = {} /\ phase[h] = "unused"
    /\ phase' = [phase EXCEPT ![h] = "opening"] /\ owners' = [owners EXCEPT ![h] = @ + 1]
    /\ openedAt' = [openedAt EXCEPT ![h] = epoch]
    /\ UNCHANGED <<epoch, published, managerClosed, startErrors, cleanupErrors, reported, uses>>
Started(h) ==
    /\ phase[h] = "opening" /\ phase' = [phase EXCEPT ![h] = "ready"]
    /\ UNCHANGED <<owners, openedAt, epoch, published, managerClosed, startErrors, cleanupErrors, reported, uses>>
StartFailed(h) ==
    /\ phase[h] = "opening" /\ FailureCount < MaxFailures
    /\ phase' = [phase EXCEPT ![h] = "cleaning"] /\ startErrors' = startErrors \cup {h}
    /\ UNCHANGED <<owners, openedAt, epoch, published, managerClosed, cleanupErrors, reported, uses>>
Adopt(h) ==
    /\ phase[h] = "ready"
    /\ IF ~managerClosed /\ openedAt[h] = epoch /\ published = {}
          THEN /\ phase' = [phase EXCEPT ![h] = "live"] /\ published' = published \cup {h}
          ELSE /\ phase' = [phase EXCEPT ![h] = "cleaning"] /\ published' = published
    /\ UNCHANGED <<owners, openedAt, epoch, managerClosed, startErrors, cleanupErrors, reported, uses>>
ReleaseAll ==
    /\ published' = {}
    /\ phase' = [h \in Handles |-> IF h \in published THEN "cleaning" ELSE phase[h]]
    /\ UNCHANGED <<owners, openedAt, startErrors, cleanupErrors, reported, uses>>
Invalidate == /\ ~managerClosed /\ epoch = 0 /\ epoch' = 1 /\ ReleaseAll /\ UNCHANGED managerClosed
CloseManager == /\ ~managerClosed /\ managerClosed' = TRUE /\ ReleaseAll /\ UNCHANGED epoch
Cleanup(h, fails) ==
    /\ phase[h] = "cleaning" /\ (~fails \/ FailureCount < MaxFailures)
    /\ LET primary == IF h \in startErrors THEN {"start"} ELSE {}
           cleanup == IF fails THEN {"cleanup"} ELSE {}
       IN reported' = [reported EXCEPT ![h] = primary \cup cleanup]
    /\ cleanupErrors' = IF fails THEN cleanupErrors \cup {h} ELSE cleanupErrors
    /\ phase' = [phase EXCEPT ![h] = "closed"]
    /\ UNCHANGED <<owners, openedAt, epoch, published, managerClosed, startErrors, uses>>
Call(h) ==
    /\ h \in published /\ phase[h] = "live"
    /\ uses' = uses \cup {[handle |-> h, phase |-> phase[h], closed |-> managerClosed,
                           opened |-> openedAt[h], acquired |-> epoch]}
    /\ UNCHANGED <<phase, owners, openedAt, epoch, published, managerClosed, startErrors, cleanupErrors, reported>>

Next == Invalidate \/ CloseManager \/ \E h \in Handles :
    Open(h) \/ Started(h) \/ StartFailed(h) \/ Adopt(h) \/ Call(h) \/ \E f \in BOOLEAN : Cleanup(h, f)
Spec == Init /\ [][Next]_vars
OwnerStartsOnce == Cardinality(published) <= 1 /\ \A h \in Handles : owners[h] <= 1
FailuresBothReported == \A h \in Handles : phase[h] = "closed" =>
    reported[h] = ((IF h \in startErrors THEN {"start"} ELSE {}) \cup
                   (IF h \in cleanupErrors THEN {"cleanup"} ELSE {}))
CloseIsFinal ==
    /\ (managerClosed => published = {})
    /\ \A h \in published : phase[h] = "live"
    /\ \A u \in uses : ~u.closed /\ u.phase = "live"
NoReuseAfterInvalidate ==
    /\ \A h \in published : openedAt[h] = epoch
    /\ \A u \in uses : u.opened = u.acquired
\* A stronger, per-server bound; concurrent opens can falsify it before adoption.
OneOwnerPerServer == Cardinality({h \in Handles : phase[h] \notin {"unused", "closed"}}) <= 1
====
