---- MODULE CommandRecovery ----
EXTENDS Naturals, Sequences, FiniteSets
CONSTANT Commands
VARIABLES journal, generation, expired, crashed, evidence
vars == <<journal, generation, expired, crashed, evidence>>

Indices(kind, c) == {i \in 1..Len(journal) : journal[i].kind = kind /\ journal[i].cmd = c}
Started(c) == Indices("start", c) # {}
Finished(c) == Indices("outcome", c) # {}
Orphan(c) == Indices("orphan", c) # {}
Settled(c) == Finished(c) \/ Orphan(c)
Entry(kind, c, writer, value) ==
    [kind |-> kind, cmd |-> c, writer |-> writer, epoch |-> generation, value |-> value]

Init == /\ journal = <<>> /\ generation = 1 /\ expired = FALSE /\ crashed = FALSE
        /\ evidence = [c \in Commands |-> "unknown"]

StepCommit ==
    /\ journal = <<>> /\ (generation = 2 \/ ~crashed)
    /\ journal' = <<Entry("step", "group", generation, "authorized")>>
    /\ UNCHANGED <<generation, expired, crashed, evidence>>

Dispatch(c) ==
    /\ Len(journal) > 0 /\ ~Started(c) /\ (generation = 2 \/ ~crashed)
    /\ journal' = Append(journal, Entry("start", c, generation, "attempt"))
    /\ UNCHANGED <<generation, expired, crashed, evidence>>

Observe(c, value) ==
    /\ Started(c) /\ ~Settled(c) /\ evidence[c] = "unknown"
    /\ evidence' = [evidence EXCEPT ![c] = value]
    /\ UNCHANGED <<journal, generation, expired, crashed>>

Complete(c, w) ==
    /\ \E i \in Indices("start", c) : journal[i].writer = w
    /\ w = generation /\ (w = 2 \/ ~crashed) /\ ~Finished(c)
    /\ evidence[c] # "unknown"
    /\ journal' = Append(journal, Entry("outcome", c, w, evidence[c]))
    /\ UNCHANGED <<generation, expired, crashed, evidence>>

Crash == /\ generation = 1 /\ ~crashed /\ crashed' = TRUE
         /\ UNCHANGED <<journal, generation, expired, evidence>>
Expire == /\ generation = 1 /\ ~expired /\ expired' = TRUE
          /\ UNCHANGED <<journal, generation, crashed, evidence>>
Takeover == /\ generation = 1 /\ expired /\ generation' = 2
            /\ UNCHANGED <<journal, expired, crashed, evidence>>
MarkOrphan(c) ==
    /\ generation = 2 /\ ~Settled(c)
    /\ \E i \in Indices("start", c) : journal[i].writer < generation
    /\ journal' = Append(journal, Entry("orphan", c, generation, "unknown"))
    /\ UNCHANGED <<generation, expired, crashed, evidence>>

Progress == \/ StepCommit \/ Expire \/ Takeover
            \/ \E c \in Commands :
                \/ Dispatch(c) \/ MarkOrphan(c)
                \/ \E v \in {"ok", "failed"} : Observe(c, v)
                \/ \E w \in 1..2 : Complete(c, w)
Next == Progress \/ Crash
Spec == Init /\ [][Next]_vars /\ WF_vars(Progress)

NoDuplicateAttempt == \A c \in Commands : Cardinality(Indices("start", c)) <= 1
NoDoubleTerminal == \A c \in Commands : Cardinality(Indices("outcome", c)) <= 1
UnknownStaysUnknown ==
    \A c \in Commands :
        /\ \A i \in Indices("orphan", c) : journal[i].value = "unknown"
        /\ \A i \in Indices("outcome", c) :
            journal[i].value = evidence[c] /\ evidence[c] # "unknown"
SingleWriter == \A i \in 1..Len(journal) : journal[i].writer = journal[i].epoch
NoBlindRetry == \A c \in Commands : \A i \in Indices("orphan", c), j \in Indices("start", c) : j < i
EventuallyResolved == \A c \in Commands : Started(c) ~> Settled(c)
====
