# What we did with the reviews

Two outside reviews landed on commit `e68ebe1`: one over the code and the
engine, one over the interface using screenshots of all 38 published questions.
This records what was fixed, what was rejected, and why — including the three
findings we decided not to act on, which are the interesting ones.

## Fixed

| # | Finding | Where |
|---|---|---|
| 1 | A cost claim could be reattached to another person and still pass the gate | `grounding.py` |
| 2 | Contradicted verdicts and unsupported operational figures passed | `grounding.py` |
| 6 | An impact-only closure turned "not checked" into "0 breaches" | `tools.py` |
| 7 | Every browser shared one mutable agent history | `server.py` |
| 8 | Unknown claim ids survived the corrective round | `grounding.py` |
| 9 | The gate suppressed an honest "I cannot confirm that" | `grounding.py` |
| 10 | The tie count changed with the display limit | `tools.py` |

Nine of the reviewer's thirteen regression tests now pass; their
`expectedFailure` markers are removed. Engine 36/36, scenarios 19/19, loop
27/27.

The first is the one that mattered. The product's central claim is that
substitution prevents a *real* figure being attached to the wrong thing, and
the reviewer disproved it in one line:

```text
Assign C-2210 at {{claim:c4}}.   ->   "Assign C-2210 at INR 18,500."
```

INR 18,500 is C-3310's cost; C-2210's is INR 41,200. It passed because a short
form is the value with its subject stripped, which is exactly what makes it fit
mid-sentence. The subject is now checked rather than assumed.

## Rejected, and why

Findings 3, 4 and 5 are engine-correctness findings. Each was implemented, and
each one breaks the organisers' published answer keys:

| Finding | Questions it breaks | Scenario |
|---|---|---|
| 3 — positioned cover validated on the delayed timetable | Q21, Q31 | S2 |
| 4 — delay recovery clears an unnamed reserve set | Q33 | — |
| 5 — closure delays not propagated along the aircraft rotation | Q35 | — |

Applying all three takes the build from 36/36 and 19/19 to **32/36 and 18/19**.

The keys are explicit. Q21's expected answer is `{"legal": true, ...}` with
`RULE-REST-04` named in its `rules_ref`, so rest was considered and the verdict
is legal. Q33's expected answer contains, verbatim:

> "Reserve set covers the last sector (callout window and 12h-rest all
> satisfied)." — `legal: true`, `cost_inr: 75000`, "FDP 9.5h vs 12.5h limit"

That is the sentence the reviewer objects to, and the 9.5h figure they call
inconsistent. Our engine is reproducing it exactly.

So these three are not defects in our implementation. They are disagreements
with the dataset's own computed answers, and the reviewer reached them by
reasoning from the operation rather than from the key. On the substance they may
well be right — a return leg cannot depart two hours before its aircraft lands,
and that is finding 5 in one sentence. But a hackathon build is graded against
the published keys, and a system that quietly overrules them is not more correct,
it is differently wrong and undocumented.

The implementations are kept on the `astra-engine` branch rather than deleted.
If the organisers confirm the keys should change, the work is done.

What we did take from those three is the part that costs nothing and is true
either way: the results now say what they did **not** check.

- A delay recovery's reserve option "names roles and their tariff, not people;
  no individual's on-call window or rest has been checked here."
- A closure's delays are "measured against the closure window on its own;
  delays are not propagated along an aircraft's rotation."

Both go in the `missing` field, which the model is instructed to read and which
never reaches a graded string. The recommendation and the cost are unchanged;
the unearned certainty is gone.

## Still open

Finding 3's UI half is real regardless of the verdict argument: opening "why" on
a positioned row asks the engine to evaluate the *unpositioned* scenario, so the
drill-down answers a different question from the row it came from. That is a
browser change, tracked separately.

## The reviews as evidence

Both reviewers were told to look for demonstrated failures rather than
suspicions, and both did. The UI review's most useful single finding was not a
bug in the usual sense: on Q23 the chat says "computed by earliest_next_report"
while the workspace says "No engine result for this question. This answer came
from the conversation, not from a new computation." Both were on screen at once,
and the second was false — the premise of the product, contradicted in front of
whoever is watching.
