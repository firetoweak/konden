---- MODULE ToolPairing ----
EXTENDS Naturals, Sequences, FiniteSets
CONSTANTS Calls, Relevant
VARIABLES sent, partial, arrivals, answers, wakes
vars == <<sent, partial, arrivals, answers, wakes>>
Paired == {r.call : r \in answers}
Arrived == {arrivals[i].call : i \in 1..Len(arrivals)}

Init == /\ sent = FALSE /\ partial = {} /\ arrivals = <<>> /\ answers = {} /\ wakes = <<>>
DispatchGroup == /\ ~sent /\ sent' = TRUE /\ UNCHANGED <<partial, arrivals, answers, wakes>>
Output(c) ==
    /\ sent /\ c \notin (partial \cup Arrived) /\ partial' = partial \cup {c}
    /\ UNCHANGED <<sent, arrivals, answers, wakes>>
Receive(c, kind) ==
    /\ sent /\ c \notin Arrived
    /\ LET result == [call |-> c, kind |-> kind]
           closes == c \in Relevant /\ Relevant \subseteq (Arrived \cup {c})
       IN /\ arrivals' = Append(arrivals, result)
          /\ answers' = answers \cup {result}
          /\ wakes' = IF closes THEN Append(wakes, c) ELSE wakes
    /\ UNCHANGED <<sent, partial>>
Settle == \E c \in Calls, k \in {"success", "failure", "unknown"} : Receive(c, k)
Next == DispatchGroup \/ Settle \/ \E c \in Calls : Output(c)
Spec == Init /\ [][Next]_vars /\ WF_vars(Settle)

OneFollowUpPerGroup == Len(wakes) <= 1 /\ (Len(wakes) > 0 => Relevant \subseteq Arrived)
NoPartialAsTerminal ==
    /\ answers = {arrivals[i] : i \in 1..Len(arrivals)}
    /\ \A i \in 1..Len(arrivals) : arrivals[i].kind \in {"success", "failure", "unknown"}
ProtocolPairs == IF sent THEN answers \cup {[call |-> c, kind |-> "outstanding"] : c \in Calls \ Paired}
                 ELSE {}
EveryCallPaired ==
    /\ [](sent => {r.call : r \in ProtocolPairs} = Calls /\ Cardinality(ProtocolPairs) = Cardinality(Calls))
    /\ (sent ~> Paired = Calls)
NoWakeForIrrelevant == \A i \in 1..Len(wakes) : wakes[i] \in Relevant
WakeAfterGroup == (sent /\ Relevant # {} /\ Relevant \subseteq Arrived) => Len(wakes) = 1
====
