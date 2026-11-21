---- MODULE ForegroundInterrupt ----
EXTENDS Naturals, Sequences, FiniteSets
CONSTANT Commands
VARIABLES closed, launches, alive, exited, stops, stopErrors, records
vars == <<closed, launches, alive, exited, stops, stopErrors, records>>
Starts(c) == {i \in 1..Len(launches) : launches[i].cmd = c}
Results(c) == {i \in 1..Len(records) : records[i].cmd = c}
Record(c, source, value) ==
    [cmd |-> c, source |-> source, value |-> value, closed |-> closed, stopped |-> c \in stops]

Init == /\ closed = FALSE /\ launches = <<>> /\ alive = {} /\ exited = {}
        /\ stops = {} /\ stopErrors = {} /\ records = <<>>
Launch(c) ==
    /\ ~closed /\ Starts(c) = {}
    /\ launches' = Append(launches, [cmd |-> c, closed |-> closed]) /\ alive' = alive \cup {c}
    /\ UNCHANGED <<closed, exited, stops, stopErrors, records>>
CloseContinuation ==
    /\ ~closed /\ closed' = TRUE
    /\ UNCHANGED <<launches, alive, exited, stops, stopErrors, records>>
Exit(c) ==
    /\ c \in alive /\ alive' = alive \ {c} /\ exited' = exited \cup {c}
    /\ UNCHANGED <<closed, launches, stops, stopErrors, records>>
Terminate(c) ==
    /\ closed /\ Results(c) = {} /\ c \notin (stops \cup stopErrors)
    /\ alive' = alive \ {c} /\ stops' = stops \cup {c}
    /\ UNCHANGED <<closed, launches, exited, stopErrors, records>>
StopFailure(c) ==
    /\ closed /\ c \in alive /\ c \notin stopErrors /\ stopErrors' = stopErrors \cup {c}
    /\ UNCHANGED <<closed, launches, alive, exited, stops, records>>
RecordInterrupted(c) ==
    /\ c \in stops /\ Results(c) = {}
    /\ records' = Append(records, Record(c, "interrupt", "unknown"))
    /\ UNCHANGED <<closed, launches, alive, exited, stops, stopErrors>>
RecordNatural(c) ==
    /\ c \in exited /\ Results(c) = {}
    /\ records' = Append(records, Record(c, "exit", "observed"))
    /\ UNCHANGED <<closed, launches, alive, exited, stops, stopErrors>>

Next == CloseContinuation \/ \E c \in Commands :
    Launch(c) \/ Exit(c) \/ Terminate(c) \/ StopFailure(c) \/ RecordInterrupted(c) \/ RecordNatural(c)
Spec == Init /\ [][Next]_vars
InterruptOrdering ==
    /\ (stops # {} => closed)
    /\ \A i \in 1..Len(records) : records[i].source = "interrupt" => records[i].closed /\ records[i].stopped
SingleTerminalPerIdentity == \A c \in Commands : Cardinality(Results(c)) <= 1
InterruptionIsUnknown == \A i \in 1..Len(records) :
    records[i].source = "interrupt" => records[i].value = "unknown"
ProcessExitBeforeRecord ==
    /\ \A c \in exited : Cardinality(Starts(c)) = 1
    /\ \A i \in 1..Len(records) : records[i].source = "exit" => records[i].cmd \in exited
NoLateStart == \A i \in 1..Len(launches) : ~launches[i].closed
====
