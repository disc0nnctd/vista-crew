# Architecture: where the language model stops

The brief's real question is which work belongs to the model and which to
deterministic code. This document is the answer, and the boundary it draws is
enforced by a mechanism rather than by a sentence in a prompt.

## The one-line version

> **Deterministic Python computes every figure. The model chooses which question
> to ask, resolves what the controller meant, and explains the result. It may
> propose anything; it may state only what a validator returned.**

## The diagram

```
     CONTROLLER
         │  "Captain C-1042 is sick for P-2291 — what should I do?"
         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ LANGUAGE MODEL  (gpt-5.6-luna, OpenAI-compatible)                       │
│                                                                         │
│  Owns:  which tool to call · what "tomorrow" meant · which of two       │
│         verdicts was asked for · naming a tie · phrasing                │
│  Never: arithmetic · legality · cost · ranking · any figure at all      │
└─────────────────────────────────────────────────────────────────────────┘
         │ tool call                                    ▲ tool result
         │ (ids, dates, stations —                      │ {summary, claims,
         │  never a cost, count,                        │  missing, data}
         │  duration or verdict)                        │
         ▼                                              │
┌─────────────────────────────────────────────────────────────────────────┐
│ TOOL LAYER  aircrew/tools.py — 9 tools, one envelope                    │
│                                                                         │
│  lookup · crew_profile · trace_disruption · check_assignment ·          │
│  duty_timeline · simulate_disruption · resolve_cover ·                  │
│  draft_notification · validate                                          │
│                                                                         │
│  Every result carries:                                                  │
│    claims[]  each figure/verdict, with an id, validated text and basis  │
│    missing[] what this result does NOT establish, inside the result     │
└─────────────────────────────────────────────────────────────────────────┘
         │                                              ▲
         ▼                                              │
┌─────────────────────────────────────────────────────────────────────────┐
│ DETERMINISTIC ENGINE                                                    │
│                                                                         │
│   rules.py    seven rules from rules.json → Finding{rule, limit,        │
│               actual, excess, context}; rendering kept separate         │
│   engine.py   impact tracing · candidate enumeration · legality         │
│               simulation per candidate · costing · ranking ·            │
│               exhaustive joint search · recovery planning               │
│   query.py    the typed lookups                                         │
│   data.py     loaders, indices, Duty                                    │
│                                                                         │
│   Proven by:  scoreboard.py — 36/36 gradable questions, 19/19           │
│               scenario checks, with no model attached                   │
└─────────────────────────────────────────────────────────────────────────┘
         │
         ▼ problem_statement/data/*.json   (read-only)
```

## The gate: the boundary made mechanical

The model's reply passes through `grounding.py` before anyone sees it.

```
model reply ─┬─ "{{claim:c7}}"  ──► substituted with the claim's validated text
             │                      (the model never typed the figure)
             │
             └─ a figure typed directly ──► checked against every value the
                                            tools returned this turn
                                                │
                            ┌───────────────────┴────────────────────┐
                            ▼                                        ▼
                        accounted for                       not accounted for
                            │                                        │
                            ▼                                        ▼
                        shown to the                    one corrective turn; if
                        controller                      still ungrounded, the
                                                        answer is WITHHELD
```

The gate does not silently rewrite the model's prose. A crew controller needs
to know the system disagreed with itself; a quiet correction hides exactly the
failure the product exists to prevent.

## Why the model is allowed to propose

The strict reading — the model is never in a position where it *could*
calculate — costs something real. A controller asking "could we position
someone from Delhi?" wants the idea considered, and a system that can only
answer questions it was asked in tool-shaped form is worse at the job.

So the split here is by *authority*, not by *topic*:

| | model | engine |
|---|---|---|
| may raise a possibility | yes | — |
| may state a figure | only via a claim id | yes |
| may state a legality verdict | only after `check_assignment` / `validate` | yes |
| may rank options | never | yes |
| decides which question is asked | yes | — |
| decides what the answer is | never | yes |

"C-2210 might work if we position them from DEL" is a useful sentence. It
becomes an answer only after `check_assignment(..., positioned=true)` returns,
and what reaches the controller is the tool's verdict — including when it
refutes the suggestion.

## The corollaries this forces

- **No tool takes a cost, a duration, a count or a verdict.** There is nowhere
  for a remembered figure to enter. `tests/test_agent_loop.py` asserts this
  against the tool schemas, so it cannot rot.
- **No tool can skip a rule.** `check_assignment(positioned=true)` supplies
  RULE-BASE-07's precondition (a deadhead has been arranged); it does not
  disable the check. Inventing a capability is as serious as inventing a number.
- **A follow-up that changes the pool is an engine parameter.** "What if the
  reserve is sick too?" is `exclude_crew=[...]`, which re-enumerates, re-checks
  legality and re-prices. Reading the next row off the previous ranking is a
  different question with a different answer.
- **`missing` lives in the result, not in the prose.** An impact result looks
  answer-shaped, and a model asked "what should I do?" will otherwise fill the
  gap with a plausible cost.
- **Two verdicts, not a flag.** `check_assignment` returns `callable` and
  `rules` separately. The dataset grades both, and they disagree: Q24 asks
  whether reserve C-3305 can cover P-2291 and keys on a duty-hours breach, while
  scenario S2 excludes the same person on the on-call window. One boolean
  parameter would have made one of those two answers unreachable.

## The workspace boundary

The same rule, applied to pixels.

- Panels render **only** from a tool result's `data`. Nothing is parsed out of
  the model's prose, so the chat and the workspace cannot disagree about a
  figure.
- Layout is deterministic: the tool that ran picks the panel. The model has no
  say, so the same question type always draws the same screen.
- Drill-downs (`why` on a candidate row) call `/api/tool` directly. The
  workspace therefore works with the model switched off — which is the demo's
  safety net and, on a crew desk, the difference between degraded and dead.
- A turn that draws no panel leaves the workspace alone.

---

Build notes, dataset measurements and the reverse-engineering log are in
[NOTES.md](NOTES.md); worked transcripts in [SAMPLES.md](SAMPLES.md).
