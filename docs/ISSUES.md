# Open issues and handover

State as of the last commit on `rebuild`. Written for whoever picks this up
next. Everything here is measured or reproducible; nothing is a guess, and
where something is unverified it says so.

## Where the numbers stand

| Measurement | Result | Verified |
| --- | --- | --- |
| 38 questions through the **engine** | **36/36** gradable, 2 GEN | yes, `python -m aircrew.scoreboard` |
| 6 scenarios | **19/19** checks | yes, same command |
| Agent loop + claim gate unit tests | **13/13** | yes, `python -m tests.test_agent_loop` |
| 38 questions through the **agent** (luna), run 1 | 21/38 | yes, measured |
| 38 questions through the **agent** (luna), run 2 | 32/38 as reported | yes, measured |
| Run 2 **re-scored** after fixing the scorer | **35/36** gradable | yes, offline replay of the recorded calls |
| Run 3 (all fixes applied) | **not run** | **no** |

Read the last two rows carefully. Run 2 was scored by a broken scorer (below),
and re-scoring its recorded tool calls against the current code gives 35/36 with
Q27 the only genuine failure. That re-score is real — it replays the exact
arguments the model chose — but it is **not** the same as a fresh run, because
three fixes landed after it and none has been exercised live.

**First thing to do: run it.**

```bash
export AIRCREW_API_KEY=$(grep -m1 '^OPENAI_API_KEY=' ~/Keys/.env.keys | cut -d= -f2-)
export AIRCREW_BASE_URL=$AIRCREW_BASE_URL     # codex-lb, tailnet
export AIRCREW_MODEL=gpt-5.6-luna
python -m aircrew.replay --out /tmp/replay3.json
```

Takes roughly 15–20 minutes and spends real tokens on the user's gateway. Ask
first.

---

## Open issues

### 1. Q27 routes to `validate` instead of `resolve_cover` — the only real failure left

**Question:** "The VT-DXE captain is sick on 16 Sep (called 01:30Z). Which
reserve captains' on-call windows cover the callout, and are they qualified?"

**What it does:** calls `validate(claim_kind="crew_qualified", …)` once per
reserve captain, which answers the *rating* half and never touches the on-call
window. It then reports C-3305 as eligible. The correct answer is C-3315, and
the key wants the exclusion reason `reserve on-call window 06:00-18:00Z does not
cover required report 03:00Z`.

**Why:** `resolve_cover` computes exactly this — its exclusion list carries the
window reason — but nothing steers the model there for an "are they eligible"
phrasing. `validate` looks like the right tool because the question contains the
word "qualified".

**Options, cheapest first:**
- A routing line in the system prompt: *eligibility across a set of candidates
  is `resolve_cover`; `validate` is for one specific statement about one person.*
- Narrow `validate`'s `crew_qualified` schema — it currently accepts
  `pairing_id`, `role`, `vacated_by`, `from_date`, none of which it uses. The
  unused parameters make it look more capable than it is.
- Consider deleting `validate` entirely if the replay shows it is never the
  right choice. It duplicates no logic; it may earn no keep.

### 2. The claim gate fired on 13 turns for the wrong reason — fix applied, unverified

14 of 38 turns needed a correction round in run 2. **13 of them had an empty
`ungrounded_numbers` list**, so no figure was invented at all: the gate rejected
them for citing an unknown claim id.

Cause: ids came from a process-global counter, so by question 20 they read
`c200+` while the system prompt's example says `{{claim:c7}}` — and the model
copied the example.

Fixed by `tools.renumber()`, called per turn in `agent.py`, so ids are always
`c1, c2, c3 …` within a turn. **Not verified live.** Expect the correction count
to drop sharply; if it does not, that is the first thing to investigate.

### 3. Three questions are unverified after their fixes

`Q21` (positioning consequence), `Q30` (seats at risk) and the claim renumbering
all landed after the last full run. Q21 and Q30 are confirmed correct by offline
replay of the recorded calls; the renumbering is not confirmed at all.

### 4. Test coverage is thin next to `main`

10 tests here against 267 on `main`, which has a dedicated file per rule. The
scoreboard plus scenario checks cover the engine end to end, but there is
nothing equivalent to `test_rule_rest.py` exercising one rule in isolation. This
is the clearest structural gap in the branch.

### 5. The claim gate matches figures, not meaning

A number passes if it appears anywhere in the turn's tool results. "The delay
costs 486" would pass when 486 is that turn's passenger count. It catches
invention, which is the dangerous failure, but not a real figure attached to the
wrong label. Tightening means demanding a claim id for every figure, and a gate
that fires on correct answers gets switched off.

### 6. Known reference defect, deliberately reproduced

S5 ranks two crew already rostered on P-2213 as paid callouts to cover it. The
answer key does that, so the engine does. Full analysis in
[SAMPLES.md §E](SAMPLES.md#e-a-case-the-system-handles-poorly). The one-line
filter costs two scenario checks; shipping it is a grading-contract decision.

---

## Bugs found and fixed, with the evidence

All of these came out of running the agent, and none of them was visible from
the engine number.

| Bug | Symptom | Fix |
| --- | --- | --- |
| Model fills every optional parameter with `""`/`0` | `lookup` hit its own "needs a date" error and the agent asked the controller for a date it already had (Q07, Q11) | `tools.clean_args()` drops empty values before dispatch |
| No `crew_id` filter on `lookup(entity="crew")` | "What is C-2210's base and rating?" had nowhere to land | delegates to `crew_profile` |
| Year hallucinated as 2025 | closure returned 0 flights; the model reported "0 flights affected" — **wrong but grounded** (Q19, Q29) | schedule window stated in the system prompt; `Tools._bad_date` refuses an out-of-window date instead of returning an empty result |
| `earliest_next_report` had no tool | Q23 unanswerable; collapsing 17→9 tools dropped it | added back on evidence; the surface is now ten |
| Nothing exposed seats-by-type | Q30 forced the model to compute, which the gate blocks | `most_seats_at_risk` in the query layer, shared by the tool claim and the scoreboard |
| `check_assignment(positioned=True)` returned no deadhead detail | Q21 could not state the consequence | positioning info and its consequence sentence now on the result |
| **The replay scorer escaped non-ASCII** | `json.dumps` turned the answer keys' em dash into `—`, so Q33 and Q35 could never match — two correct answers scored as failures | `ensure_ascii=False` |
| The replay scorer graded rubrics | Q36 and Q38 have `must_include` / "open-ended" keys and can only ever fail a string match | marked GEN and excluded from the denominator, same rule as the engine scoreboard |
| **The chat pane needed `openai`, and died silently without it** | a missing package raised `SystemExit`, a `BaseException`, so `server.py`'s `except Exception` missed it: the handler thread died and the browser saw a closed socket. `/api/health` said `"model": true` at the same time, because `get_agent()` defers the import | the loop now posts to `/chat/completions` over stdlib `urllib`, with retry. No dependency, so the health answer is honest again |
| **Two tools raised through `dispatch`, killing the turn** | `dispatch` caught only `TypeError`, and the loop calls it unguarded, so `validate` missing `aircraft_type` (or `pairing_id`) and `duty_timeline` with an unknown pairing became a 502 for the whole turn instead of a result the model could correct | `dispatch` returns an envelope for `KeyError` and for anything else, naming the missing field |

The scorer bugs are worth dwelling on: **the measurement was wrong in the
direction that made the system look worse**, and two of the six "failures" in
run 2 were the harness, not the agent. A number that is not itself tested is not
evidence.

---

## Running it

```bash
# engine only — no key, no dependencies
python -m aircrew.scoreboard
python -m aircrew.cli resolve --pairing P-2291 --vacated-by C-1042

# workspace, with luna, over the tailnet
export AIRCREW_API_KEY=... AIRCREW_BASE_URL=$AIRCREW_BASE_URL
export AIRCREW_MODEL=gpt-5.6-luna
python -m aircrew.server --host 0.0.0.0 --port 8765
# http://dcmini-1.tail1e3236.ts.net:8765
```

`--host 0.0.0.0` also exposes it on the LAN; use `--host <your tailnet address>` for
tailnet only. Do **not** stop it with `pkill -f aircrew.server` — the pattern
matches your own shell's command line and kills the terminal.

## Orientation

- [ARCHITECTURE.md](ARCHITECTURE.md) — the LLM/deterministic boundary and the claim gate
- [NOTES.md](NOTES.md) — how each rule was recovered from the keys, which rules
  actually bind, and the comparison against `main`
- [SAMPLES.md](SAMPLES.md) — worked transcripts including the case handled poorly
- `aircrew/scoreboard.py` is the forcing function; run it after every change
