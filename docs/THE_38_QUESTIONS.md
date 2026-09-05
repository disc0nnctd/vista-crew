# The 38 questions, in plain English

What each graded question is really asking, what the right answer is, and which
part of the engine produces it. Every row was **measured**: the scoreboard was
run with `Query` and `Engine` instrumented, so the "answered by" column is the
call that actually ran, not a guess.

```bash
python -m aircrew.scoreboard      # prints the same 38, pass by pass
```

**Score: 36/36 gradable pass. Two are rubrics and are never counted.**

The three tiers are the dataset's own, from `questions.json`:

| Tier | Count | What it demands |
| --- | --- | --- |
| 1 | 16 | Look something up. One fact, one file. |
| 2 | 14 | Work out a consequence. Something happened: what breaks, who breaches? |
| 3 | 8 | Recommend. Rank real options with real costs, and justify them. |

---

## Tier 1: sixteen lookups

No reasoning, no ranking. You either indexed the data correctly or you did not.

| # | The question, plainly | The answer | Answered by |
| --- | --- | --- | --- |
| Q01 | Who is on reserve at BLR that day, and when are they callable? | 12 reserves with their on-call windows | `query.reserves` |
| Q02 | How many duty hours has C-1042 used this week, and how much is left? | 20.93h used, 39.07h headroom | `query.crew_profile` |
| Q03 | Which flights leave DEL on the 15th? | one: DX402 | `query.flights` |
| Q04 | Which certificates expire in the next 30 days? | a list, including the two that expire inside the flying week | `query.certifications_expiring` |
| Q05 | Which aircraft flies DX412, and how many seats? | VT-DXC, A320, 162 seats | `query.flights` |
| Q06 | When is C-3310 callable, and how fast can they get in? | 06:00-18:00Z, 45 minutes | `query.crew_profile` |
| Q07 | Where is C-2210 based and what can they fly? | DEL, A320 | `query.crew_profile` |
| Q08 | Who is rostered on P-2291, in which seats? | six crew with roles | `query.pairings` |
| Q09 | Which flights go BLR to BOM on the 17th? | DX431, DX412 | `query.flights` |
| Q10 | How many flights on the 16th? | 21 | `query.flights` |
| Q11 | How many captains at DEL, and who? | one: C-2210 | `query.crew` |
| Q12 | What is the longest leg in the schedule? | 2.75h, four flights share it | `query.flights` |
| Q13 | C-2087's rank and 28-day flight hours? | Captain, 23.5h | `query.crew_profile` |
| Q14 | Where can you fly nonstop from BLR? | seven stations | `query.stations` |
| Q15 | Who is the senior cabin crew on VT-DXB on the 16th? | C-3171 | `query.pairings` |
| Q16 | How risky is C-1042, and why? | 0.78, short-rest pattern and two fatigue reports | `query.risk` |

**The one that bites: Q02.** It asks for the hours *and* the headroom, so it
quietly tests whether your accrual reproduces the dataset's own published
figure. Get the accrual wrong and every later cost and legality answer is wrong
too, because they all depend on it.

**The near-miss: Q07.** "What is C-2210's base and rating?" used to return all
142 active crew, because `lookup(entity="crew")` accepted `crew_id` and ignored
it. Confidently wrong, which is worse than empty.

---

## Tier 2: fourteen consequences

Something has happened. What breaks, and does anyone breach a rule? These stop
at a verdict or a list; nothing is ranked or priced.

| # | The question, plainly | The answer | Rule tested | Answered by |
| --- | --- | --- | --- | --- |
| Q17 | C-1042 calls in sick. Which flights have no crew now? | 3 flights day 1, 3 more at risk day 2, 486 passengers | QUAL-05 | `engine.trace_crew_unavailable` |
| Q18 | Can C-2087 cover P-2291? | No: 61.33h against the 60h weekly cap | DUTY-02 | `engine.check_assignment` |
| Q19 | BLR closes 08:00-14:00Z. What is hit? | a list of affected flights | | `engine.station_closure_impact` |
| Q20 | VT-DXA is 90 minutes late. Does the crew bust a limit? | Yes: FDP 12.75h against a 12h limit | FDP-01 | `engine.delay_impact` |
| Q21 | Can the DEL captain cover if we fly them in? | Legal, but the deadhead delays departure ~3h and costs extra | BASE-07, REST-04 | `engine.check_assignment` + positioning |
| Q22 | Can C-5417 work their rostered duty on the 19th? | No: recurrent training expired on the 17th | CERT-06 | `engine.check_assignment` |
| Q23 | Released 15:30Z. When can they report next? | 03:30Z next morning, after 12h rest | REST-04 | `engine.earliest_next_report` |
| Q24 | Can reserve C-3305 cover **both** days of P-2291? | No: 68.25h against the 60h cap | DUTY-02 | `engine.check_assignment` |
| Q25 | DX404 is cancelled. How many passengers, what cost? | 162 passengers, INR 2,50,000 | | `engine.cancellation_cost` |
| Q26 | Who is at 45h+ duty this week, counting today's plan? | C-2087 at 51.83h, C-3305 at 50.0h | DUTY-02 | `query.crew` |
| Q27 | The ATR captain is sick at 01:30Z. Which reserves are both awake and rated? | only C-3315 | QUAL-05, BASE-07 | `engine.resolve_cover` |
| Q28 | C-5837 is free that day. Can they cover? | No: only 10.75h rest before their next pairing | REST-04 | `engine.check_assignment` |
| Q29 | HYD closes 05:00-09:00Z. What is hit? | DX461, DX462 | | `engine.station_closure_impact` |
| Q30 | Which single leg strands the most passengers? | any A320 leg, 162 seats against 72 on the ATR | | `query.flights` |

**Why Q24 is the clever one.** C-3305 can cover day 1 quite happily. It is day 2
that pushes them over 60 hours. Anyone checking the first day and stopping gets
the wrong answer, which is exactly the mistake a controller makes at 3 a.m.

**Why Q28 is the subtle one.** C-5837 is free on the 15th and 16th. The breach
is on the **17th** — taking this pairing leaves too little rest before the
pairing they already have. The conflict is downstream, and invisible unless you
merge the proposed duty into their whole week.

**Why Q27 splits two ideas.** A reserve outside their on-call window is not
breaking a rule; they simply cannot be called. That is a different sentence from
"this would be illegal", and the answer key distinguishes them.

---

## Tier 3: eight recommendations

Now you have to decide, price it, rank it, and say why everyone else was ruled
out.

| # | The question, plainly | The answer | Answered by |
| --- | --- | --- | --- |
| Q31 | C-1042 is out for a two-day pairing. What do we do? | ranked options; C-3310 at INR 18,500 is cheapest legal | `engine.resolve_cover` |
| Q32 | Two A320 captains sick at once. Plan both. | C-3305 on P-2205, C-1017 on P-2212, INR 42,500 total | `engine.resolve_multiple` |
| Q33 | The delay busts FDP. Now what? | ranked recovery; a fresh reserve set takes the last leg | `engine.delay_recovery` |
| Q34 | C-5417's training lapsed. Fix the 19th. | ranked cover, C-4809 by reserve callout | `engine.resolve_cover` |
| Q35 | BLR closes for six hours. Plan the recovery. | per-flight delays, FDP consequences and actions across pairings | `engine.closure_recovery` |
| Q36 | Write the callout message to C-3310. | **rubric**: must contain report time, both days' flights, the hotel, an acknowledgement deadline | `engine.draft_notification` |
| Q37 | Cheapest legal cover for the VT-DXF first officer? | C-3316, reserve callout | `engine.resolve_cover` |
| Q38 | What three things should the morning briefing show? | **rubric**: open-ended, judged on operational reasoning | none |

**Q32 is why "solve them together" matters.** With two vacancies you cannot
resolve each one separately and add up: the cheapest person for the first is
often also the cheapest for the second, and one human cannot fly two aeroplanes.
The engine searches 157 legal combinations. Twenty of them tie at INR 42,500, so
cost does not pick a winner and the controller decides on reachability.

---

## The two that are not scored, and why

**Q36** (draft the callout) and **Q38** (the morning briefing) have rubrics for
answer keys, not values. Q38's key says so outright:

> "Open-ended; judged on operational reasoning, not exact match."

Q36's key is a `must_include` checklist, "judged on completeness... not template
wording."

You cannot grade those by comparison. A checker either always passes them, or
passes when the wording happens to overlap with the key's own phrasing, which
measures string similarity rather than reasoning. Either way the number goes up
while the capability does not.

**So the honest denominator is 36, not 38.** They are marked `GEN` and excluded.
Reporting 38/38 would be two free points, and a judge who opens Q38 sees
immediately that nothing could have failed it.

---

## What the score does and does not mean

**36/36 measures the engine.** The scoreboard calls `Query` and `Engine`
directly, never through the tool layer or the model. That is deliberate: it
tests whether the rules were recovered from the data correctly, with no language
model in the way.

**It is not the same as asking the assistant.** The agent has to pick the right
tool from a plain-English question first, and that is a different number,
measured separately by `python -m aircrew.replay`. The last recorded run
re-scored at 35/36 with one genuine routing failure. Anyone quoting 36/36 as
"the assistant answers 36 of 38" is overstating it.

**And two of the seven rules never eliminate anybody** in this dataset. The
heaviest 28-day block is 79.24h against a 100h limit, so `RULE-FLT-03` never
fires and `RULE-FDP-01` essentially only fires under a delay. The rules doing
the real work are rest, qualification, certification and base.
