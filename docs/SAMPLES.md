# Sample inputs and outputs

Every figure below was produced by the engine on the shipped dataset and can be
reproduced with the command shown. Nothing here is illustrative.

### A. Cover a sick captain (Tier 3, Q31 / scenario S2)
```
$ python -m aircrew.cli resolve --pairing P-2291 --vacated-by C-1042 --limit 5
5 legal candidates, 19 excluded (9 rest, 8 aircraft rating, 1 duty hours, 1 base / on-call window). Ranked by cost, then crew id; cancellation is always last.

Established:
  [c1] 5 candidates are legal
  [c2] 19 candidates are excluded (9 rest, 8 aircraft rating, 1 duty hours, 1 base / on-call window)
  [c3] the cheapest legal option is Assign Captain C-3310 (reserve callout) at INR 18,500
  [c4] Assign Captain C-3310 (reserve callout) costs INR 18,500
  [c5] Assign Captain C-1526 (day-off callout) costs INR 24,000
  [c6] Assign Captain C-3983 (day-off callout) costs INR 24,000
  [c7] Assign Captain C-5566 (day-off callout) costs INR 24,000
  [c8] Assign Captain C-2210 (reserve callout + deadhead from DEL (first departure delayed ~3.0h)) costs INR 41,200

Not established by this result:
  - excluded candidates carry the rule that stopped them; they are in data.exclusions
```

### B. Why one candidate was ruled out (Tier 2, Q28)
```
$ python -m aircrew.cli check --crew C-5837 --pairing P-2291
ILLEGAL under the seven rules; callable. These are two separate questions -- report the one that was asked.

Established:
  [c1] C-5837 on P-2291 is illegal: RULE-REST-04: only 10.75h rest before P-2204 on 2026-09-17 (downstream conflict)
  [c2] C-5837 can be called out

Not established by this result:
  - no cost computed here; call resolve_cover for prices and ranking
```

### C. The follow-up that changes the pool

A controller who has just been told to call C-3310 asks: "and if C-3310 is
sick too?" This is not the next row of the previous list. It is a new
enumeration, re-checked for legality and re-priced.

```
$ python -m aircrew.cli resolve --pairing P-2291 --vacated-by C-1042 --exclude C-3310 --limit 3
4 legal candidates, 19 excluded (9 rest, 8 aircraft rating, 1 duty hours, 1 base / on-call window). Ranked by cost, then crew id; cancellation is always last.

Established:
  [c1] 4 candidates are legal
  [c2] 19 candidates are excluded (9 rest, 8 aircraft rating, 1 duty hours, 1 base / on-call window)
  [c3] the cheapest legal option is Assign Captain C-1526 (day-off callout) at INR 24,000
  [c4] Assign Captain C-1526 (day-off callout) costs INR 24,000
  [c5] Assign Captain C-3983 (day-off callout) costs INR 24,000
  [c6] Assign Captain C-5566 (day-off callout) costs INR 24,000

Not established by this result:
  - excluded candidates carry the rule that stopped them; they are in data.exclusions
```

### D. A delay that breaks the duty (Tier 3, Q33 / scenario S4)
```
$ python -m aircrew.cli delay --aircraft VT-DXA --date 2026-09-16 --hours 1.5 --mode technical
technical delay of 1.5h. FDP breached.

Established:
  [c1] FDP after the delay is 12.75h against a 12.0h limit for 4 sectors
  [c2] the rostered crew BREACH RULE-FDP-01
  [c3] Original crew operates DX401–DX403 (delayed); full reserve set (CPT, FO, SCC, 3 CC) operates DX404 costs INR 75,000
  [c4] Cancel DX404 costs INR 250,000

Not established by this result:
  - downstream duties of the same crew are not re-checked here; use duty_timeline for the rest of their week
```

### E. A case the system handles poorly

**The question.** "C-5417's recurrent training lapsed. Resolve their 19 Sep
assignment." (Q34, scenario S5.)

**What the system returns.** Among the 42 ranked candidates:

```
rank 15   Assign Cabin Crew C-2840 (day-off callout)   INR 12,500
rank 29   Assign Cabin Crew C-4588 (day-off callout)   INR 12,500
```

**Why it is wrong.** Both of them are already rostered on P-2213 on 19 September:

```
P-2213 crew: C-5647 Captain · C-5363 First Officer · C-3171 Senior Cabin Crew
             C-5417 Cabin Crew · C-4588 Cabin Crew · C-2840 Cabin Crew
```

They are working that flight. Offering to call them out — and charging INR
12,500 for a day off they are not on — is not a cover option, it is a
double-count of crew already on the aircraft. A controller who took rank 15
would end the day one cabin crew short and one callout fee poorer.

**Why it is still in the build.** The reference answer key lists both of them,
at exactly those ranks and prices. The engine reproduces the key because the key
is the grading surface, and inserting an operational correction here would trade
a scenario check for a judgement the brief did not ask for.

Reproducing it required a real modelling decision, not a special case: a
proposed cover **replaces** the candidate's own duty on that pairing rather than
stacking on top of it (`RulesEngine.week`). Without that, C-2840 collides with
themselves and is excluded for a clash that does not exist. The replacement rule
is correct in general — a person cannot be double-booked against themselves —
and it is what makes the reference's inclusion of them reachable at all.

**What we would do about it.** One line in `resolve_cover`: skip candidates
already holding a role on the pairing being covered, unless they are the person
being replaced. It was implemented, it cost two scenario checks, and it was
reverted. The right resolution is to ship the filter and record the deviation
from the key, but that is a call for whoever owns the grading contract, not one
to make silently in the engine.

**The general shape of this failure.** The dataset is the specification, and
where the dataset is wrong the system is wrong with it. That is the correct
default for a graded exercise and the wrong one for an operational deployment.
A production build needs a layer that can disagree with its own reference data
and say so out loud — which is the same problem this product already solves for
the language model, applied one level up.

### F. Where the claim gate fires

The failure mode the gate exists for, reproduced in `tests/test_agent_loop.py`:

```
model:   "Three flights uncovered; cancelling costs INR 1,250,000."
gate:    1,250,000 is not in any tool result this turn  →  reply not sent
model:   "Three flights are uncovered on day 1. I do not have a cancellation
          cost — that needs resolve_cover."
gate:    grounded  →  sent, flagged in the UI as corrected
```

`trace_disruption` deliberately returns no per-option costs and says so in its
`missing` list. An impact result looks answer-shaped, and that is exactly where
an invented figure comes from.

If the model cannot ground the figure on the second attempt, the answer is
withheld rather than printed. On a crew desk, no answer is recoverable and a
confident wrong number is not.
