---- MODULE BackgroundReport ----
EXTENDS Naturals, Sequences, FiniteSets
CONSTANTS Front, Job
Commands == {Front, Job}
VARIABLES settled, background, closed, signalled, followed, stopped, completion, reports
vars == <<settled, background, closed, signalled, followed, stopped, completion, reports>>

Init == /\ settled = {} /\ background = {} /\ closed = FALSE /\ signalled = {}
        /\ followed = FALSE /\ stopped = {} /\ completion = "running" /\ reports = <<>>
Handoff ==
    /\ Job \notin settled /\ background' = {Job} /\ settled' = settled \cup {Job}
    /\ UNCHANGED <<closed, signalled, followed, stopped, completion, reports>>
ForegroundFinish(c) ==
    /\ c \notin settled /\ settled' = settled \cup {c}
    /\ UNCHANGED <<background, closed, signalled, followed, stopped, completion, reports>>
Close ==
    /\ ~closed /\ closed' = TRUE
    /\ UNCHANGED <<settled, background, signalled, followed, stopped, completion, reports>>
Live == (Commands \ settled) \cup (IF completion = "running" THEN background ELSE {})
Interrupt(c) ==
    /\ closed /\ c \in Live /\ c \notin background
    /\ signalled' = signalled \cup {c} /\ settled' = settled \cup {c}
    /\ UNCHANGED <<background, closed, followed, stopped, completion, reports>>
Follow ==
    /\ settled = Commands /\ ~closed /\ ~followed /\ followed' = TRUE
    /\ UNCHANGED <<settled, background, closed, signalled, stopped, completion, reports>>
StopBackground ==
    /\ Job \in background /\ completion = "running"
    /\ stopped' = {Job} /\ completion' = "stopped"
    /\ UNCHANGED <<settled, background, closed, signalled, followed, reports>>
FinishBackground(kind) ==
    /\ Job \in background /\ completion = "running" /\ completion' = kind
    /\ UNCHANGED <<settled, background, closed, signalled, followed, stopped, reports>>
Report ==
    /\ Job \in background /\ completion # "running"
    /\ Len(reports) = 0
    /\ reports' = Append(reports, [id |-> Job, kind |-> completion])
    /\ UNCHANGED <<settled, background, closed, signalled, followed, stopped, completion>>

Next == \/ Handoff \/ Close \/ Follow \/ StopBackground \/ Report
        \/ \E c \in Commands : ForegroundFinish(c) \/ Interrupt(c)
        \/ \E k \in {"exited", "unknown"} : FinishBackground(k)
Spec == Init /\ [][Next]_vars /\ WF_vars(Follow) /\ WF_vars(Report)
BackgroundEventuallyReported == (Job \in background /\ completion # "running") ~> (Len(reports) = 1)
BackgroundDoesNotBlockFollowUp == (settled = Commands /\ ~closed) ~> (followed \/ closed)
BackgroundReportedOnce == Len(reports) <= 1 /\ \A i \in 1..Len(reports) : reports[i].id \in background
InterruptIsForegroundOnly == signalled \cap background = {}
StopIsNotInterrupt == Job \in stopped =>
    completion = "stopped" /\ \A i \in 1..Len(reports) : reports[i].kind = "stopped"
====
