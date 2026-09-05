# Crew Ops Advisor — what it is, and what it covers

## Short description

A crew controller loses a captain two hours before report time and has minutes
to decide. Crew Ops Advisor takes that question in plain English and answers it
the way a duty manager needs it answered: who can legally cover, what each
option costs, and why everyone else was ruled out.

The agent is given deterministic tools that read and simulate the operation. It
chooses which tool the question calls for, resolves what the controller actually
meant, and reports what came back — including when the result refutes what it
was about to say. Alongside the answer it stages the matching view in the
workspace, so the proposal the controller passes on to the crew is already on
screen: the ranked options with costs, the duty timeline, and the rule that
stopped each excluded candidate.

## The line the product is built on

> Deterministic Python computes every figure, every legality verdict and every
> cost. The language model chooses which question to ask it, resolves ambiguity,
> and explains the result. It never calculates.

This is enforced, not requested. Every tool returns a claim envelope —
`{summary, claims, missing, data}` — and the model may only state a figure by
citing a claim id. The gate substitutes the engine's own text for the citation
before the reply is sent, so a real number can never end up attached to the
wrong thing, which is the failure a spot-check would miss. `missing` names what
each result does *not* establish, so an impact answer cannot be read as a
recommendation. A reply citing a figure no tool computed is regenerated once,
and withheld if it happens again.

## How a question moves through it

1. **The controller asks.** "Captain C-1042 is out for P-2291 on 15 Sep. What
   should I do?"
2. **The model resolves the ask.** Which pairing, which date, and which of two
   verdicts is wanted — "does this breach a rule" and "can we call this person
   out" have different answers, and the controller asked one of them.
3. **The tools compute.** Ten of them, in three tiers: read the record, trace
   the consequence, produce the recommendation.
4. **The gate checks.** Claim substitution, then five checks: leftover
   placeholders, ungrounded figures, figures under the wrong label, unknown
   claim ids, and figures spelled as words.
5. **The workspace stages the view.** The panel drawn is the most decisive
   result of the turn, not the last call made. Every panel drills down, and
   every drill-down comes back.

## The tools

| Tier | Tool | What it settles |
|---|---|---|
| 1 — read | `lookup` | Flights, crew, reserves, certifications, pairings, risk, stations |
| 1 — read | `crew_profile` | One crew member: rank, base, ratings, reserve window, 7d duty and headroom, 28d block, certificates |
| 1 — read | `earliest_next_report` | The minimum-rest answer from a release time |
| 2 — consequence | `trace_disruption` | Which flights lose a crew member, per day, with passenger counts |
| 2 — consequence | `check_assignment` | Two separate verdicts: legality under the seven rules, and callability under the on-call window |
| 2 — consequence | `duty_timeline` | The week with the proposed cover inserted, rest gaps, and any breach |
| 2 — consequence | `simulate_disruption` | A delay, a station closure or a cancellation, with the recovery assessment |
| 3 — recommend | `resolve_cover` | Every candidate enumerated, legality simulated, costed and ranked |
| 3 — recommend | `draft_notification` | The callout message, from roster records |
| — | `validate` | Check a statement before making it |

Tools query several of the nine JSON files at once and return more than a single
fact. That is deliberate: the model asks one question instead of five, and it
never joins the files itself — joining is where a wrong answer would come from.

## How it covers the problem statement

The dataset is dCortex Air: 147 legs, 150 crew, 39 pairings, 16 reserves, seven
rules, and a fixed snapshot of 2026-09-14T18:00Z. The published question set is
38 questions across three tiers, and the build answers all of them through the
same loop — there is no per-question handling anywhere.

**Tier 1 — retrieval (16 questions).** Reserve rosters and on-call windows,
duty-hour accrual against the snapshot, departures by station, certificate
expiries, aircraft and seat counts, crew complements by role, base and ratings.
Answered by `lookup` and `crew_profile`. The engine reproduces the published
`duty_hours_7d` for all 150 crew from `daily_history` plus the roster, so these
figures are recomputed rather than read back.

**Tier 2 — consequence (14 questions).** A sick call and the flights it
uncrews; whether a specific cover breaches a rule and by how much; a station
closure and the legs inside the window; a 90-minute delay against the FDP limit
for that sector count; minimum rest from a release time; whether a reserve can
cover a *full* two-day pairing or only its first day; cancellation cost and
passengers; crew above a duty threshold; which reserve windows actually cover a
callout; and the single leg with the most seats at risk. Answered by
`trace_disruption`, `check_assignment`, `duty_timeline` and
`simulate_disruption`.

Two distinctions in this tier decide whether an answer is usable, and both are
carried explicitly rather than left implied:

- **Illegal is not the same as uncallable.** A reserve outside their on-call
  window has broken no rule. `check_assignment` returns the two verdicts
  separately, and the exclusion list marks the difference.
- **Day 1 is not the pairing.** C-3305 is legal for the first day of P-2291 in
  isolation and breaches the 7-day duty limit on the second. The engine
  simulates the whole pairing, so the answer does not change when the controller
  reads further.

**Tier 3 — recommendation (8 questions).** Ranked resolution options with costs
and reasoning; the optimal joint plan when two captains are lost at once; what
to do about an FDP breach after a delay; resolving a lapsed certificate; a
recovery plan across the pairings a closure touches; the callout draft; the
cheapest legal cover; and what a standing morning briefing should surface.
Answered by `resolve_cover`, `simulate_disruption` and `draft_notification`.

`resolve_cover` enumerates every candidate, simulates legality for each against
all seven rules, costs them from `costs.json`, and ranks them with cancellation
always last. Where options tie on cost, the answer says so — cost has stopped
deciding, and the choice is the controller's, usually on reachability.
Follow-ups that change who is available re-rank from scratch rather than reading
the next row off the previous list, because that is a different question with a
different answer.

**The engineered cases the problem statement calls out** are reproduced from the
data rather than special-cased: C-2087 breaching the 60-hour limit by 1h20m,
C-3310 covering cleanly at ₹18,500, C-2210 legal via deadhead at ₹41,200 with
DX412 delayed about three hours, C-2091 excluded as ATR-only under RULE-QUAL-05,
and the single flagged roster exception where a certificate lapses two days
before the duty it was rostered against.

**Risk signals are consumed, not predicted.** `risk_signals.json` is a provided
input; the build reads it and never models it, which is what the brief asks.

## What the controller sees

A horizontal header, then two panes. Left is the conversation. Right is the
workspace, and everything in it came from a tool result: the ranked options with
the rule that cleared each one, the duty timeline with rest gaps, the exclusion
list with one row per person and the rule that stopped them, and the claim
check. Excess detail is folded by default and one click from open. Rule ids
carry their plain-English constraint on hover.

Two side buttons open the boundary diagram and the flow chart — what the engine
decides against what the model decides — because the claim that the model does
not calculate is worth showing rather than asserting. A dev checkbox reveals the
briefing panel and the tool tiers for a technical audience.

The workspace runs with the model switched off. Every panel draws from
`/api/tool`, so the engine half of the product is demonstrable without an API
key, which is also the demo's safety net.
