---- MODULE GroupDelivery ----
EXTENDS Naturals, Sequences, FiniteSets
CONSTANTS Members, First, SendIds, MaxRounds, MaxReplies, MaxOrigin, MaxCrashes
VARIABLES log, issued, queue, copies, attention, down, crashes, replyLimit
vars == <<log, issued, queue, copies, attention, down, crashes, replyLimit>>
Ids == {log[i].id : i \in 1..Len(log)}
At(id) == log[CHOOSE i \in 1..Len(log) : log[i].id = id]
Epoch == log[Len(log)].epoch
Calls(m) == {<<p, m.id>> : p \in m.targets}
Plan == UNION {Calls(log[i]) : i \in {j \in 1..Len(log) : log[j].epoch = Epoch}}
Consumed == UNION {log[i].used : i \in 1..Len(log)}
Replies(root) == {i \in 1..Len(log) : log[i].root = root /\ log[i].used # {}}
OriginUse == Cardinality({i \in 1..Len(log) : log[i].id \in SendIds})
CopiesFor(id) == {<<p, id>> : p \in Members}
Flushed == CopiesFor(log[Len(log)].id) \subseteq copies
Human(id) == [id |-> id, epoch |-> id, root |-> id, round |-> 1,
              body |-> id, used |-> {}, targets |-> {First}]

Init == /\ log = <<Human("opening")>> /\ queue = Calls(log[1]) /\ issued = {}
        /\ copies = {} /\ attention = {} /\ down = {} /\ crashes = 0 /\ replyLimit \in 1..MaxReplies

Deliver(p, id) ==
    /\ "host" \notin down /\ p \notin down /\ id \in Ids
    /\ copies' = copies \cup {<<p, id>>}
    /\ UNCHANGED <<log, issued, queue, attention, down, crashes>>
Issue(call) ==
    /\ "host" \notin down /\ Flushed /\ call \in queue
    /\ issued' = issued \cup {call} /\ queue' = queue \ {call}
    /\ UNCHANGED <<log, copies, attention, down, crashes>>
DeliverRequest(call) ==
    /\ "host" \notin down /\ call[1] \notin down /\ call \in (issued \cap Plan)
    /\ attention' = attention \cup {call}
    /\ UNCHANGED <<log, issued, queue, copies, down, crashes>>

Send(id, call) ==
    /\ "host" \notin down /\ call[1] \notin down /\ call \in attention /\ Flushed
    /\ id \notin Ids
    /\ OriginUse < MaxOrigin
    /\ LET answers == call \in (issued \cap Plan) /\ call \notin Consumed
           root == IF answers THEN At(call[2]).root ELSE id
           round == IF answers THEN At(call[2]).round + 1 ELSE 1
           msg == [id |-> id, epoch |-> Epoch, root |-> root, round |-> round, body |-> id,
                   used |-> IF answers THEN {call} ELSE {},
                   targets |-> IF round <= MaxRounds THEN Members \ {call[1]} ELSE {}]
       IN /\ answers => Cardinality(Replies(root)) < replyLimit
          /\ log' = Append(log, msg) /\ queue' = queue \cup Calls(msg)
    /\ UNCHANGED <<issued, copies, attention, down, crashes>>
Retry(p, id, body) ==
    /\ id \in Ids /\ body = At(id).body /\ Deliver(p, id)
Interject ==
    /\ "host" \notin down /\ Epoch = "opening" /\ Flushed
    /\ log' = Append(log, Human("new")) /\ queue' = Calls(Human("new"))
    /\ UNCHANGED <<issued, copies, attention, down, crashes>>

Crash(p) ==
    /\ p \notin down /\ crashes < MaxCrashes /\ crashes' = crashes + 1 /\ down' = down \cup {p}
    /\ queue' = IF p = "host" THEN {} ELSE queue
    /\ UNCHANGED <<log, issued, copies, attention>>
Recover(p) ==
    /\ p \in down /\ down' = down \ {p}
    /\ queue' = IF p = "host" THEN Plan \ issued ELSE queue
    /\ UNCHANGED <<log, issued, copies, attention, crashes>>

Protocol ==
        \/ Interject
        \/ \E p \in Members, id \in Ids : Deliver(p, id) \/ Retry(p, id, "different") \/ Retry(p, id, id)
        \/ \E call \in queue : Issue(call)
        \/ \E call \in issued : DeliverRequest(call)
        \/ \E id \in SendIds, call \in attention : Send(id, call)
        \/ \E p \in Members \cup {"host"} : Crash(p) \/ Recover(p)
Next == Protocol /\ UNCHANGED replyLimit
Spec == Init /\ [][Next]_vars

AtMostOnceConsume == \A i,j \in 1..Len(log) : i # j => log[i].used \cap log[j].used = {}
IdempotentSend == Cardinality(Ids) = Len(log)
BudgetNeverExceeded ==
    /\ OriginUse <= MaxOrigin
    /\ \A id \in Ids : Cardinality(Replies(id)) <= replyLimit
    /\ \A c \in issued : At(c[2]).round <= MaxRounds
RecoveryFromGroupLog == "host" \notin down => queue = Plan \ issued
InterjectionDoesNotRevoke == [][copies \subseteq copies' /\ attention \subseteq attention']_vars
====
