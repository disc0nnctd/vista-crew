# The tool surface, against the data

Ten tools. This is what each one is for, which of the eleven data files it
actually reads, and a real call with its real result.

The file lists were **measured**, not asserted: every accessor on `Dataset` was
instrumented and one call of each tool was recorded. If a tool reads a table,
it is listed. Reproduce with the script in
[Appendix: how the file lists were produced](#appendix-how-the-file-lists-were-produced).

Every tool returns the same envelope:

```
{summary, claims, missing, data}
```

- `data` is the engine's structured result and the only thing the workspace draws.
- `claims` are the figures and verdicts this result establishes, each with an id.
  The model may only state a figure by citing one; `grounding.py` substitutes the
  validated text.
- `missing` says what the result does **not** establish, because an impact result
  looks answer-shaped and a model asked "what should I do?" will fill the gap.

**No tool accepts a cost, a count, a duration or a verdict as an argument.** That
is checked by a test, not by a convention, so there is nowhere for a remembered
figure to re-enter the system.

## The dataset in one table

| File | Rows | What it holds |
| --- | --- | --- |
| `flights.json` | 147 | legs: times, block hours, tail, type, seats |
| `crew.json` | 150 | rank, base, ratings, seniority, reachability, status |
| `rosters.json` | 39 pairings | who is assigned to what, plus `flagged_exceptions` |
| `duty_clocks.json` | 150 | 28 days of daily duty/flight history, plus published 7d/28d totals |
| `certifications.json` | 600 | 150 crew x 4 cert types, with validity windows |
| `reserve_pool.json` | 16 | reserve dates and on-call windows |
| `rules.json` | 7 rules | the limits and their parameters |
| `costs.json` | 9 figures | callout, deadhead, delay per hour, cancellation, hotel |
| `risk_signals.json` | 150 | disruption risk score and drivers |
| `scenarios.json` | 6 | worked disruptions with answer keys (grading only) |
| `questions.json` | 38 | the graded questions (grading only) |

`scenarios.json` and `questions.json` are **never** read by a tool. They are the
harness's, not the product's: `scoreboard.py` reads them to grade. A tool that
could see the answer key would not be measuring anything.

`costs.json` does not appear in the per-tool lists below because `Engine.__init__`
loads it once at construction (`engine.py:60`), before any tool runs. Every rupee
in the system comes from there and from nowhere else.

---

## Tier 1: lookup and retrieval

### `lookup(entity, ...)`

One entity at a time, with filters. This is every Tier-1 question, collapsed
into one tool so the model has fewer things to route between.

`entity` is one of `flights | crew | reserves | certifications | pairings | risk | stations`.

| entity | reads | answers |
| --- | --- | --- |
| `flights` | `flights.json` | which flights depart DEL on the 15th; longest block; most seats at risk |
| `crew` | `crew.json`, `duty_clocks.json`, `rosters.json`, `flights.json` | which captains are A320-rated; who has 45h+ duty in 7 days |
| `reserves` | `reserve_pool.json`, `crew.json`, `flights.json` | who is on reserve at BLR on the 15th, and their windows |
| `certifications` | `certifications.json`, `crew.json`, `flights.json` | which certificates expire within 30 days |
| `pairings` | `rosters.json`, `flights.json` | which pairings fly on the 18th, by tail **or** by fleet type |
| `risk` | `risk_signals.json` | the disruption risk score for C-1042 and what drives it |

```jsonc
// "Who is on reserve at BLR on 2026-09-15, and what are their on-call windows?"
{"entity": "reserves", "on_date": "2026-09-15", "base": "BLR"}
```
```
summary: 12 reserves on 2026-09-15 at BLR. On-call window is eligibility to be called, not legality.
```

Two things worth knowing about this tool:

- `crew_id` on `entity="crew"` delegates to `crew_profile`. It used to be accepted
  and ignored, so "what is C-2210's base?" returned all 142 active crew under the
  summary "142 crew match".
- `aircraft` accepts a tail (`VT-DXA`) **or** a fleet type (`A320`). Matching only
  tails meant `aircraft="A320"` returned zero pairings with a confident count,
  which is the most dangerous shape a wrong answer can take. A zero count now
  names the tails, the types and the schedule window rather than reading like an
  answer.

### `crew_profile(crew_id, on_date)`

**Reads:** `crew.json`, `duty_clocks.json`, `certifications.json`,
`reserve_pool.json`, `risk_signals.json`, `rosters.json`, `flights.json`

Everything a controller asks about one person, so "what is their base, rating and
headroom" is one call rather than four.

```jsonc
{"crew_id": "C-1042", "on_date": "2026-09-15"}
```
```
Captain C-1042, based BLR, rated A320. 30.43h duty in the 7 days to 2026-09-15
(29.57h headroom under the 60h limit).
```

The 7-day figure is `daily_history` **plus** the roster, on every day including
the snapshot day. That reproduces the published `duty_hours_7d` for 150/150 crew;
anything that de-duplicates does not.

### `earliest_next_report(release_utc)`

**Reads:** `rules.json`

The one pure-arithmetic tool: release time plus the minimum rest.

```jsonc
{"release_utc": "2026-09-16T15:30:00Z"}
```
```
Released 2026-09-16T15:30:00Z, earliest next report 2026-09-17T03:30:00Z after 12h rest.
claim: earliest next report is 2026-09-17T03:30:00Z
```

---

## Tier 2: consequence and legality

### `trace_disruption(crew_id, pairing_id, from_date)`

**Reads:** `rosters.json`, `flights.json`, `crew.json`

What breaks when someone drops out: the flights, per day, with passenger counts.
Deliberately separate from `resolve_cover`, so "which flights are uncrewed?"
cannot trigger a 150-candidate ranking.

```jsonc
{"crew_id": "C-1042", "pairing_id": "P-2291"}
```
```
Captain C-1042 off P-2291. Day-1 passenger count is per day, not the pairing total.
claims: 3 flights uncovered on day 1: DX412, DX413, DX588
        486 passengers on day 1
        3 further flights at risk on day 2: DX589, DX590, DX591
```

`missing` carries "no candidates ranked and no costs computed here", because this
result looks like an answer to "what should I do?" and is not one.

### `check_assignment(crew_id, pairing_id, from_date, positioned)`

**Reads:** `rosters.json`, `rules.json`, `crew.json`, `duty_clocks.json`,
`certifications.json`, `reserve_pool.json`, `flights.json`

Can this person cover this pairing? Returns **two separate verdicts** that are
routinely confused:

- `rules` — legality under all seven rules
- `callable` — whether the reserve on-call window covers the required report

```jsonc
{"crew_id": "C-2087", "pairing_id": "P-2291", "from_date": "2026-09-15"}
```
```
ILLEGAL under the seven rules; callable. These are two separate questions
-- report the one that was asked.
claim: C-2087 on P-2291 is illegal: RULE-DUTY-02: would exceed 60h/7d by
       1h20m on 2026-09-15 (total 61.33h) ...
```

That split is load-bearing. The same person can be excluded from one question for
"outside the on-call window" and from another for a genuine rule breach, and the
answer keys distinguish them.

### `duty_timeline(crew_id, pairing_id, from_date)`

**Reads:** `rosters.json`, `flights.json`, `duty_clocks.json`, `crew.json`,
`certifications.json`

The crew member's week with the proposed cover inserted, each rest gap measured
and any breach marked. This is what makes "qualified, based, free and still
illegal" visible rather than merely asserted.

```jsonc
{"crew_id": "C-3310", "pairing_id": "P-2291"}
```
```
2 duty days. This is what makes 'qualified, based, free and still illegal' visible.
```

### `simulate_disruption(kind, ...)`

**Reads:** `flights.json`, `rosters.json` (and `costs.json` via the engine)

Three what-ifs behind one name, because they are the same engine primitive
dressed differently: `kind` is `delay | closure | cancellation`.

```jsonc
// a 90-minute technical delay
{"kind": "delay", "aircraft": "VT-DXA", "on_date": "2026-09-16", "delay_hours": 1.5}

// a station closure
{"kind": "closure", "station": "BLR", "on_date": "2026-09-17",
 "start_utc": "08:00", "end_utc": "14:00"}
```

Any date outside 2026-09-14..20 is **refused**, not answered. A hallucinated 2025
date used to return zero flights, and the model then reported "0 flights
affected" — wrong, but grounded, so the claim gate could not catch it.

### `validate(claim_kind, ...)`

**Reads:** whatever the claim needs; for `assignment_legal`, the same seven files
as `check_assignment`

For checking a statement before making it: `assignment_legal | crew_qualified |
cheapest_option`.

```jsonc
{"claim_kind": "assignment_legal", "crew_id": "C-2087",
 "pairing_id": "P-2291", "from_date": "2026-09-15"}
```
```
Claim 'C-2087 can legally cover P-2291' is REFUTED.
```

---

## Tier 3: recommendation

### `resolve_cover(pairing_id | vacancies, role, vacated_by, from_date, exclude_crew, limit)`

**Reads:** `crew.json`, `rosters.json`, `duty_clocks.json`, `certifications.json`,
`reserve_pool.json`, `rules.json`, `flights.json` (and `costs.json` via the engine)

The whole decision. Enumerates every candidate, runs all seven rules on each,
prices the legal ones, ranks by cost then crew id, and keeps the rejections with
the rule that stopped each.

```jsonc
// one vacancy
{"pairing_id": "P-2291", "vacated_by": "C-1042"}

// a follow-up: that person is also unavailable, re-rank from scratch
{"pairing_id": "P-2291", "vacated_by": "C-1042", "exclude_crew": ["C-3310"]}

// two vacancies solved together, not one after the other
{"vacancies": [{"pairing_id": "P-2205", "role": "Captain"},
               {"pairing_id": "P-2212", "role": "Captain"}]}
```
```
5 legal candidates, 19 excluded (9 rest, 8 aircraft rating, 1 duty hours,
1 base / on-call window). Ranked by cost, then crew id; cancellation is always last.

claims: 5 candidates are legal
        19 candidates are excluded (9 rest, 8 aircraft rating, ...)
        the cheapest legal option is Assign Captain C-3310 (reserve callout) at INR 18,500
        Assign Captain C-3310 (reserve callout) costs INR 18,500
        3 legal options tie at INR 24,000, so cost does not separate them
```

`exclude_crew` **re-simulates**; it does not read the next row down. That is
tested, because "the next-cheapest" and "the cheapest without X" are different
answers whenever the excluded person changed anyone else's rest.

The joint form searches combinations rather than solving each vacancy
independently, because the cheapest option for each separately can be the same
person.

### `draft_notification(crew_id, pairing_id, from_date)`

**Reads:** `rosters.json`, `crew.json`, `flights.json`

The callout message. Every time, place and flight number is taken from the
roster, so there is nothing for the model to misremember.

```jsonc
{"crew_id": "C-3310", "pairing_id": "P-2291"}
```
```
Callout draft for C-3310 on P-2291. Every time, place and flight number comes
from the roster.
```

---

## Which tool answers which question

| Controller asks | Tool | Tier |
| --- | --- | --- |
| "Who is on reserve at BLR on the 15th?" | `lookup(entity=reserves)` | 1 |
| "What is C-2210's base and rating?" | `crew_profile` | 1 |
| "When can they report next?" | `earliest_next_report` | 1 |
| "Which flights go uncrewed if C-1042 drops out?" | `trace_disruption` | 2 |
| "Can C-2087 cover P-2291?" | `check_assignment` | 2 |
| "Why is that illegal? Show me the week." | `duty_timeline` | 2 |
| "BLR closes 08:00-14:00Z, what breaks?" | `simulate_disruption(kind=closure)` | 2 |
| "Is that actually the cheapest?" | `validate` | 2 |
| "C-1042 is out. What should I do?" | `resolve_cover` | 3 |
| "Both A320 captains are sick. Plan both." | `resolve_cover(vacancies=[...])` | 3 |
| "Draft the callout." | `draft_notification` | 3 |

---

## Appendix: how the file lists were produced

Every accessor on `Dataset` (the raw tables and the derived indexes) was wrapped
to record access, then one call of each tool was run against a fresh `Dataset` so
no cache could hide a read:

```python
import functools
from aircrew import data as D, tools as T

seen = set()
for name in ("flights", "flight_by_id", "crew", "crew_by_id", "pairings",
             "pairing_by_id", "duties_for", "duty_clocks", "clock_by_id",
             "reserve_pool", "reserve_by_id", "certifications", "rules",
             "rule_params", "rule_text", "costs", "risk_signals", "risk_by_id"):
    attr = getattr(D.Dataset, name, None)
    if isinstance(attr, functools.cached_property):
        f = attr.func
        p = functools.cached_property(
            lambda self, n=name, f=f: (seen.add(n), f(self))[1])
        p.__set_name__(D.Dataset, name)
        setattr(D.Dataset, name, p)
    elif isinstance(attr, property):
        setattr(D.Dataset, name, property(
            lambda self, n=name, o=attr: (seen.add(n), o.fget(self))[1]))

t = T.Tools(D.Dataset(D.load().dir))
seen.clear()
T.dispatch(t, "resolve_cover", {"pairing_id": "P-2291", "vacated_by": "C-1042"})
print(sorted(seen))
```

`costs.json` will not appear this way for any tool: `Engine.__init__` reads it at
construction, before the recording starts.
