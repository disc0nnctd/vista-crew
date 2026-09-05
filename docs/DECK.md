# Crew Ops Advisor — deck

Ten slides. Speaker notes in italics.

---

## 1 — The desk

06:00. A captain calls in sick for a two-day pairing. The controller has minutes
to decide, seven rules to satisfy, 150 crew to search, and a spreadsheet.

*The system exists for one moment: the one where somebody acts on a number.*

---

## 2 — The real question is architectural

Not "can a model do crew control". It cannot, and should not.

**What should the model do, and what should deterministic code do?**

---

## 3 — Our answer

> Deterministic Python computes every figure. The model chooses which question
> to ask and explains the result.
>
> It may propose anything. It may state only what a validator returned.

*The second sentence is the interesting one, and it is the one we enforce.*

---

## 4 — Why "propose" is allowed

A system that only answers questions already in tool-shaped form is worse at the
job. "Could we position someone from Delhi?" deserves to be considered.

So the split is by **authority**, not by topic:

| | model | engine |
|---|---|---|
| raise a possibility | yes | — |
| state a figure | only via a claim id | yes |
| state a legality verdict | only after a check | yes |
| rank options | never | yes |
| decide what the answer is | never | yes |

---

## 5 — The mechanism

Every tool returns the same envelope:

```
{ summary, claims[], missing[], data }
```

- `claims[]` — each figure and verdict, with an id and validated text
- `missing[]` — what this result does **not** establish, inside the result

The model writes `{{claim:c7}}`; the renderer substitutes the validated text. A
figure written that way cannot be wrong, because the model never typed it.

Anything it does type is checked against the turn's tool results. Unbacked
figure → one corrective turn → still unbacked → **the answer is withheld.**

*A confident wrong number is the failure this product exists to prevent. No
answer is recoverable.*

---

## 6 — What that buys, concretely

```
model:  "Three flights uncovered; cancelling costs INR 1,250,000."
gate:   not in any tool result  →  not sent
model:  "Three uncovered on day 1. I don't have a cancellation cost."
```

Impact results look answer-shaped. That is precisely where a model asked "what
should I do?" invents a figure — so the tool says *in its own result* that it
has no costs in it.

---

## 7 — Nine tools, not seventeen

`lookup · crew_profile · trace_disruption · check_assignment · duty_timeline ·
simulate_disruption · resolve_cover · draft_notification · validate`

Two that earn their separation:

- **`check_assignment` returns two verdicts.** "Does this breach a rule?" and
  "can we call them out?" have different answers, and the dataset grades both —
  Q24 keys on a duty breach where S2 excludes the same person on the on-call
  window. One boolean would make one answer unreachable.
- **`trace_disruption` stays out of `resolve_cover`.** "Which flights are
  uncrewed?" must not trigger a 150-candidate ranking nobody asked for.

No tool takes a cost, a count, a duration or a verdict. Nowhere for a remembered
figure to enter. Asserted in the test suite against the schemas.

---

## 8 — Where the numbers come from

The generator is absent; the rules were reverse-engineered from the keys and
then verified.

- Accrual is `daily_history + roster`, **added** — reproduces the published
  fields for **150/150** crew
- Deadhead: available from the next whole hour after arrival, report − 60 min →
  the 3.0h, 7.0h, 6.5h and 6.0h delays in four different scenarios, from one rule
- Exclusions stop at the first failing rule and report all of its findings
- Rest is named after the duty that follows the gap; a clash is never shown to a
  human as negative rest

**36/36 gradable questions. 19/19 scenario checks. No model attached.**

Cross-checked against an independent implementation of the same brief: the
recommendation agrees on **156/156** vacancies.

---

## 9 — The screen

Two panes. Conversation left, evidence right.

- Panels render **only** from tool `data` — chat and workspace cannot disagree
- The tool picks the panel, not the model — the same question always draws the
  same screen
- Drill-downs call the tool endpoint directly — **the workspace works with the
  model switched off**
- Exclusions are visible and grouped by rule, not collapsed at the bottom: they
  are what a controller most wants to challenge

*Dark only, deliberately. This desk is staffed overnight.*

---

## 10 — What we would tell you before you trusted it

- **The agent number is not measured.** 36/36 is the engine. The harness that
  replays the 38 questions *through the model* is written and unrun — no API key
  in the build environment. They are different numbers.
- **The gate matches figures, not meaning.** It catches invention. It would not
  catch a real figure attached to the wrong label.
- **Where the reference data is wrong, we are wrong with it.** Two crew already
  working a pairing are offered as paid callouts for it, because the answer key
  offers them. Documented, reproducible, and the fix costs two scenario checks.

*Overstating capability is the one thing this product cannot afford, in a deck
or in a reply.*
