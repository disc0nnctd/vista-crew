# Why a tool joins several files, and what that costs

The tools here are not one-per-file accessors. `resolve_cover` alone reads seven
of the eleven data files in a single call. That is a deliberate choice with a
real cost, so this is the argument for it and the accounting against it.

Every number below is measured. Reproduce with the snippets at the end.

## The alternative, priced

The obvious design is a thin tool per file: `get_crew`, `get_roster`,
`get_duty_clocks`, `get_certifications`, `get_reserves`, `get_rules`,
`get_costs`. The model then joins them itself.

Take the flagship question: *"Captain C-1042 is out for P-2291. What should I
do?"* Under thin tools the model would have to:

1. read the roster to find the pairing, its days and its flights
2. read the crew list to find every captain
3. filter to the A320-rated ones
4. read every candidate's duty clock and sum 28 days of history plus rostered duty
5. read every candidate's certificates and check validity on both duty dates
6. read the reserve pool for windows and bases
7. read the rules for the limits
8. for each of 24 candidates, check seven rules against a merged week
9. read costs and price each survivor
10. sort, and keep the reasons for the 19 rejected

Steps 4, 8 and 9 are arithmetic. **The moment the model performs them, the
product's central claim is gone** — the figures on screen would be model output,
not computed results, and the claim gate would have nothing to check them
against.

The context cost is also worse, not better. The raw files are:

| File | Size | ~tokens |
| --- | --- | --- |
| `duty_clocks.json` | 384 KB | ~95,900 |
| `certifications.json` | 72 KB | ~17,900 |
| `flights.json` | 44 KB | ~11,000 |
| `crew.json` | 29 KB | ~7,400 |
| `rosters.json` | 28 KB | ~7,000 |
| everything | 660 KB | **~165,000** |

`duty_clocks.json` alone is ~96k tokens: 150 crew x 28 dated rows. To check
seven rules against 24 candidates you need most of it. A thin-tool design either
ships that into the context or invents pagination and spends twenty round-trips
assembling it.

**The joined call that answers the whole question costs ~2,500 tokens.**

## What the joined calls actually cost

Measured, one call each, the full envelope as the model receives it:

| Tool | chars | ~tokens |
| --- | --- | --- |
| `earliest_next_report` | 561 | 140 |
| `duty_timeline` | 1,283 | 320 |
| `draft_notification` | 1,577 | 394 |
| `validate` | 1,467 | 366 |
| `trace_disruption` | 1,589 | 397 |
| `lookup(reserves)` | 2,556 | 639 |
| `crew_profile` | 2,688 | 672 |
| `lookup(crew, filtered)` | 3,122 | 780 |
| `check_assignment` | 3,514 | 878 |
| `simulate_disruption(delay)` | 3,596 | 899 |
| `lookup(flights, one day)` | 6,659 | 1,664 |
| `simulate_disruption(closure)` | 7,662 | 1,915 |
| **`resolve_cover`** | **10,096** | **2,524** |

A whole Tier-3 turn is typically two or three of these. The heaviest realistic
turn is under 5,000 tokens of tool results.

## Where it was genuinely wasteful, and what changed

The argument above does not excuse a bloated payload, so I measured the biggest
one instead of assuming it was fine. `resolve_cover` was **23,306 characters**,
and the breakdown was damning:

```
exclusions             17,445 chars   (85% of data)
  └ all_breaches       13,232 chars   (75% of exclusions)
  └ reason              1,940 chars
  └ rule_text           1,402 chars
options                 2,420 chars
recommended               382 chars
```

`all_breaches` was a structured copy of findings whose text was already in
`reason`. Searching the whole repository, it was **written in one place and read
in none**: not by the workspace, not by the scoreboard, not by the answer keys.

Removing it took `resolve_cover` from 23,306 to **10,096 characters**, a **57%
cut on the single heaviest call in the product**, with no change to any output:
36/36 engine, 19/19 scenarios, 16/16 loop, 67 UI checks.

A test now pins the shape and the budget, because this is exactly the kind of
thing that creeps back:

```python
for x in env["data"]["exclusions"]:
    assert set(x) == {"crew_id", "rule", "rule_text", "reason"}
assert len(json.dumps(env)) < 13000
```

## Two defects that look alike on a size chart and are not

A second bug in this codebase also showed up as an oversized payload, and
treating it as the same problem would miss the point.

`lookup(entity="crew")` advertised a `crew_id` parameter in its schema, accepted
it, and never passed it to the query. "What is C-2210's base and rating?"
therefore returned every active crew member:

```
lookup(entity="crew")                 15,099 chars  ~3,774 tokens
lookup(entity="crew", crew_id=…)       1,960 chars    ~490 tokens
                                                        8x
```

The right answer was in there — `C-2210 → {base: DEL, ratings: [A320]}`, in row
142 of 142. The data was not missing. It was buried, under a summary that read
**"142 crew match"**.

| | `all_breaches` | `crew_id` accepted and ignored |
| --- | --- | --- |
| Extra payload | 13,232 chars | ~3,300 tokens per call |
| Was the answer present? | not applicable, nothing read it | yes, in row 142 |
| What the summary said | accurate | a true sentence about a question nobody asked |
| Failure mode | cost only | cost, plus confident mis-direction |
| Found by | auditing sizes | running the thing |

`all_breaches` was **pure freight**: expensive and otherwise harmless. No output
changed when it was removed.

The `crew_id` bug was **mis-scoping**, and the bloat was a symptom rather than
the disease. Three things were wrong at once:

1. **The schema lied.** A parameter that is accepted and silently dropped is
   worse than one that does not exist, because the model believes it filtered
   and has no way to detect otherwise.
2. **The summary was true about the wrong question.** Summaries steer the model
   harder than data does, so it steers confidently in the wrong direction.
3. **It pushed the filtering back into the model.** Answering would mean
   scanning 142 rows and picking one. That is the same family of mistake as
   making it do arithmetic: work that belongs in Python, handed to the component
   whose output cannot be checked.

The rule that falls out of the pair: **waste costs tokens and is recoverable;
mis-scoping costs trust and is silent.** A size audit finds the first. Only
running the system finds the second — which is how both of these actually
surfaced, one from measuring the heaviest call and one from a live session
where a Tier-1 question had nowhere clean to land.

To be precise about what was observed: the tool returned 142 rows under that
summary. It was not caught producing a wrong answer to a controller. It was a
hazard that was fixed before it became an incident.

## The five rules the tool surface follows

**1. A tool joins files when the join is the answer.** "Can this person cover
this pairing?" is inherently a join across roster, crew, clocks, certificates,
reserves and rules. Splitting it does not remove the join; it moves it into the
model, where it becomes arithmetic nobody can check.

**2. A tool returns what a controller would ask next, not the whole table.**
`crew_profile` costs 672 tokens and covers rank, base, ratings, reserve window,
this week's pairings, 7-day duty, headroom, 28-day block, certificates and risk.
Four separate lookups would cost more in round-trips alone, and each round-trip
is another chance to route wrongly.

**3. Decision tools stay separate from impact tools.** `trace_disruption` (397
tokens) answers "which flights are uncrewed?" without triggering a 24-candidate
ranking. Folding it into `resolve_cover` would make the cheap question cost
2,524 tokens every time.

**4. Every field must have a reader.** A tool result is context the model pays
for on every subsequent turn of the conversation, not just once. `all_breaches`
is the cautionary tale.

**5. Every advertised parameter must do something.** If the schema names it, the
query must use it. A silently dropped filter is not a small bug: it returns a
confident, correct-looking result for a question nobody asked, and it hands the
filtering back to the model. `crew_id` on `entity="crew"` is that cautionary
tale, and it is a different one from `all_breaches`.

## The honest trade-offs

**`resolve_cover` is still the heaviest thing here** at ~2,500 tokens, and the
19 exclusions are 54% of its data even after the cut. They earn it: the answer
keys grade the rejections with their reasons, and "why not them?" is the
question a controller actually argues with. But it does mean a long conversation
carries several of these.

**`limit` trims options, not exclusions.** `limit=3` saves 1,739 of 10,096
characters, 17%, because exclusions dominate what is left. That is arguably the
wrong knob: if the tool needed slimming again, a `brief` mode returning
exclusion counts by rule rather than per-crew rows would save far more.

**`lookup(entity="flights")` for a whole day costs 1,664 tokens** and is close to
a raw table dump. It is only cheap relative to the 11,000-token file.

**The join hides its own cost from the model.** It cannot tell that one call read
seven files, so it cannot make a cost-aware choice about which to call. That is
fine here because the surface is ten tools and the routing is obvious; it would
not scale to a hundred.

## Reproducing the measurements

```python
import json
from aircrew.tools import Tools, dispatch, renumber

t = Tools()
env = dispatch(t, "resolve_cover", {"pairing_id": "P-2291", "vacated_by": "C-1042"})
renumber([env])
s = json.dumps(env, default=str, ensure_ascii=False)
print(len(s), "chars ~", len(s) // 4, "tokens")

for k, v in env["data"].items():
    print(f"  {k:24} {len(json.dumps(v, default=str))}")
```

Which files a tool reads is measured separately, by instrumenting every
`Dataset` accessor. See [TOOLS.md](TOOLS.md#appendix-how-the-file-lists-were-produced).
