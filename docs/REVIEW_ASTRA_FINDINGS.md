**Astra review: branch `rebuild`, commit `e68ebe1`**

The separation between deterministic computation and model explanation is worth keeping. The current boundary does not enforce the stated guarantee: wrong assignments pass the gate, and some engine results assert legality without evaluating the scenario they describe. I would fix those before presenting this as an answer a controller can act on without checking.

This was an offline code and executable review against the unchanged published dataset. I ran the 38-question engine scoreboard, the scenario checks, the existing agent tests, and the UI DOM suite. `screenshots/` is empty in this checkout, so I could not inspect the claimed 38 browser transcripts. I did not run a paid live-model replay. The question assessment below concerns reproduced engine/tool/UI behavior, not unseen model answers.

**1. [P1] A cost claim can be reassigned to another person, even when the model uses the required placeholder.**

Source: [grounding.py:375](C:/Users/gamin/Projects/dCortex/aircrew/grounding.py:375), [grounding.py:452](C:/Users/gamin/Projects/dCortex/aircrew/grounding.py:452).

Reproduce by calling `resolve_cover(pairing_id="P-2291", vacated_by="C-1042")` and renumbering its claims. Claim `c4` is C-3310's INR 18,500 cost. Submit this draft to `grounding.check`:

```text
Assign C-2210 at {{claim:c4}}.
```

The result is `ok=True`, `blocking=False`, and **“Assign C-2210 at INR 18,500.”** The engine actually prices C-2210 at INR 41,200. A second draft, “The cancellation affects {{claim:c4}} passengers,” also passes, rendering “INR 18,500 passengers.”

`_pick()` drops the claim's subject when it chooses `short`. The later label check examines `typed`, from which the placeholder has been removed. Neither the subject nor even the unit of the inserted value is checked. Substitution prevents invention of the digits; it does not prevent invention of their meaning. Checking rendered units alone would still miss the first example, where both values are money.

Smallest safe containment: render operational claims as complete, isolated engine statements, including subject, scope and units; stop inserting bare operational values into model-authored assertions. The stronger fix is a structured answer whose assignment/cost/rule fields reference compatible claims and whose factual sentences are rendered deterministically. Merely making placeholders mandatory does not fix this defect.

Regression: `test_cost_claim_cannot_be_attached_to_another_person`.

**2. [P1] The gate permits contradicted verdicts and unsupported operational figures.**

Source: [grounding.py:443](C:/Users/gamin/Projects/dCortex/aircrew/grounding.py:443), [grounding.py:97](C:/Users/gamin/Projects/dCortex/aircrew/grounding.py:97).

Call `check_assignment(crew_id="C-2087", pairing_id="P-2291")`. Its result says illegal, with the weekly-duty breach. The draft **“C-2087 is legal and cheapest.”** nevertheless passes. `unbacked` only asks whether *any* tool result exists; it does not inspect that result's verdict, its subject, its capability, or its `missing` boundary. An error envelope can similarly satisfy this existence check.

With no tool results at all, both **“Cancellation costs INR 7.”** and **“Report at 23:59Z. Rest is ten hours.”** pass. `ALWAYS_OK` exempts numbers by value rather than grammatical use. Clock times are excluded by the number regex, and ordinary number words are outside `MAGNITUDE_RE`.

Smallest fix for verdicts: require an explicit verdict claim with matching subject, scope and polarity; an arbitrary tool result is insufficient. For operational figures, remove the global value allowlist and use structurally identified list markers/rule references instead. Bind report times, dates, durations and costs to typed engine fields. Extending a few regexes cannot support the general guarantee about free prose.

Regressions: `test_a_failed_assignment_does_not_authorize_a_positive_verdict`, `test_small_money_amount_requires_evidence`, `test_operational_times_and_word_numbers_require_evidence`.

**3. [P1] Positioning is priced, but the delayed duty is not used for legality or the timeline.**

Source: [engine.py:496](C:/Users/gamin/Projects/dCortex/aircrew/engine.py:496), [engine.py:529](C:/Users/gamin/Projects/dCortex/aircrew/engine.py:529), [engine.py:358](C:/Users/gamin/Projects/dCortex/aircrew/engine.py:358).

The P-2291 resolver offers C-2210 as legal, at INR 41,200, with a three-hour positioning delay. Its duty objects still use the original report and release times. On 15 September, shifting both edges by those computed three hours moves release from 15:30Z to 18:30Z. The next report remains 04:00Z on 16 September. Rest is therefore **9.5 hours**, not the displayed 12.5 hours.

Reproduce without changing any records: take `cover_duties("P-2291", "C-2210")`, replace its first duty with `duties[0].shifted(3, hold_report=False)`, and call `rules.check_duties(..., positioned=True)`. The existing rules engine reports `RULE-REST-04: only 9.5h rest before COVER on 2026-09-16`.

There are two related UI contradictions. `check_assignment(..., positioned=True)` returns `rules.legal=True` but `timeline.legal=False`, because `duty_timeline()` drops the positioning precondition when rechecking. Clicking “why” on the positioned ranked option also omits `positioned` from the new request, producing a base breach for a different scenario. See [engine.py:328](C:/Users/gamin/Projects/dCortex/aircrew/engine.py:328) and [web/index.html:1481](C:/Users/gamin/Projects/dCortex/web/index.html:1481).

Smallest fix: construct the actual positioned schedule before validation; propagate any required later changes, then price and render that same schedule. Carry the scenario through the timeline and drill-down, preferably by an evaluated scenario identifier. Passing `positioned=True` through the UI alone fixes the base contradiction but leaves the rest error.

Regressions: `test_positioned_cover_is_checked_on_its_delayed_timetable`, `test_positioned_assignment_and_timeline_agree`, and the positioned drill test in `review_astra_ui.js`.

**4. [P1] Delay recovery declares an unselected reserve crew legal.**

Source: [engine.py:697](C:/Users/gamin/Projects/dCortex/aircrew/engine.py:697), [engine.py:778](C:/Users/gamin/Projects/dCortex/aircrew/engine.py:778).

Call `delay_recovery("VT-DXA", "2026-09-16", 1.5)`. The recommended INR 75,000 option says a full reserve set operates DX404, sets `legal=True`, and says “callout window and 12h-rest all satisfied.” Its `reserve_set` contains only role names and tariff amounts. No crew members are selected, and no reserve member's legality or callability is checked.

The reproduction wraps `RulesEngine.check_duties` and observes zero calls. Independently, making `check_duties` and `callable_now` raise still produces the same legal recommendation: that code path never reaches either check. The UI prints this reasoning directly. This establishes an unsupported verdict, even if an executable reserve combination might exist.

The same result quotes 9.5h FDP for the retained legs while its technical-delay scenario holds report and computes 11.0h for those legs. Both are below the limit here, but the quoted explanation belongs to a different report-time assumption.

Smallest safe fix: label this as a tariff-based proposal with reserve feasibility unverified, remove `legal=True` and the claim that checks passed, and expose the missing checks. To make it actionable, select distinct named crew for the tail, evaluate each actual duty and callout, and derive cost from those assignments. Quote the FDP for the report-time convention actually applied.

Regression: `test_delay_recovery_checks_the_reserve_people_it_clears`.

**5. [P1] Closure recovery clears a flight using an impossible aircraft rotation.**

Source: [engine.py:834](C:/Users/gamin/Projects/dCortex/aircrew/engine.py:834).

Call `closure_recovery("BLR", "2026-09-17", "08:00", "14:00")`. DX453 and DX454 use the same aircraft, VT-DXE. The output independently assigns DX453 a 6.5h delay and DX454 a 3.75h delay. Those figures imply:

| Flight | Delayed departure | Delayed arrival |
| --- | --- | --- |
| DX453, BLR to MAA | 14:30Z | 15:30Z |
| DX454, MAA to BLR | 13:30Z | 14:30Z |

The return flight departs two hours before its aircraft arrives. Nevertheless its row says **“delay (crew legal)”**, based on 12h FDP. Even allowing zero turnaround, the earliest possible return departs at 15:30Z, arrives at 16:30Z, and releases at 17:00Z. From the recorded 03:00Z report that is at least **14h FDP**, above the 12h limit.

These individual waits are useful lower bounds. They are not an executable recovery schedule or a basis for clearing the rostered crew on a connected leg.

Smallest fix: propagate delays through the aircraft rotation before constructing affected crew duties and checking FDP. Until that exists, expose the rows as independent lower bounds and withhold “crew legal” conclusions that assume they can be executed together.

Regression: `test_closure_does_not_clear_a_tail_on_an_impossible_rotation`.

**6. [P1] An impact-only closure silently turns “not checked” into zero breaches.**

Source: [tools.py:525](C:/Users/gamin/Projects/dCortex/aircrew/tools.py:525), [web/index.html:1227](C:/Users/gamin/Projects/dCortex/web/index.html:1227).

Call `simulate_disruption(kind="closure", station="BLR", on_date="2026-09-17", start_utc="08:00", end_utc="14:00", with_recovery=False)`.

The summary says **“13 flights touch BLR ...; 0 would push their crew past FDP.”** The UI displays **“Need re-crew 0.”** No recovery assessment ran. The same call with recovery enabled produces ten rows flagged for re-crewing under the engine's current assessment.

Both layers default a missing `flights_needing_recrew` field to an empty list. This directly violates the intended separation between impact and recommendation: even perfect model routing and quotation would transmit the false zero.

Smallest fix: represent assessment status explicitly. Omit the FDP conclusion from an impact-only summary and display “Not assessed” in the UI. Preserve the difference between an assessed empty list and an absent result.

Regressions: `test_impact_only_closure_does_not_claim_zero_fdp_breaches` and the closure UI test.

**7. [P2] Every browser shares one mutable agent history, without serialization.**

Source: [server.py:28](C:/Users/gamin/Projects/dCortex/aircrew/server.py:28), [server.py:141](C:/Users/gamin/Projects/dCortex/aircrew/server.py:141), [server.py:160](C:/Users/gamin/Projects/dCortex/aircrew/server.py:160).

Open two desk sessions against the same server. Send “C-3310 is also sick” from the first, then ask the second which captain should cover. Both use the module-global `_agent`, so the second model request contains the first desk's availability constraint. Clear in either desk resets both. The offline HTTP test reproduces the shared history using two independently labelled client requests.

Concurrent calls are worse: `ThreadingHTTPServer` can interleave `ask()` and `reset()` mutations on the same `messages` list while each call maintains a separate local set of tool results and claims. The UI disables the send button but does not prevent every programmatic submit path.

Smallest fix: associate an agent with a browser session and serialize `ask`/`reset` for that session. If this is deliberately a single-desk service, enforce one active owner and reject overlapping operations explicitly. A lock alone does not isolate different conversations.

Regression: `test_two_browser_sessions_do_not_share_conversation_history`.

**8. [P2] Unknown claim IDs survive the corrective round, and the UI claims rejected replies passed.**

Source: [grounding.py:204](C:/Users/gamin/Projects/dCortex/aircrew/grounding.py:204), [agent.py:300](C:/Users/gamin/Projects/dCortex/aircrew/agent.py:300), [web/index.html:1549](C:/Users/gamin/Projects/dCortex/web/index.html:1549).

After one real tool call, have the scripted model answer `{{claim:c999}}` both initially and during correction. The controller receives **`[unknown claim c999]`**. The gate has `ok=False` but `blocking=False`.

`unknown_claim` is local to `check()`. Substitution has already changed the token to square-bracket text, so the `blocking` property's leftover regex no longer recognizes it. The agent treats this as the same nonblocking class as a leaked tool name.

Separately, give the UI `corrected:true, grounded:false` with the agent's withheld-answer message. Developer view still says **“the reply above passed”**. It also always attributes correction to an invented figure, even when the issue was wording or a claim ID.

Smallest fix: store unknown/malformed claim failures explicitly in `Grounding`, include them in `blocking` and the corrective prompt, and expose a final disposition such as accepted/accepted-with-style-warning/withheld. Render the UI message from that disposition rather than `corrected` alone.

Regressions: `test_unknown_claim_is_withheld_after_the_corrective_round` and the rejected-correction UI test.

**9. [P2] The gate suppresses an honest statement that legality has not been checked.**

Source: [grounding.py:443](C:/Users/gamin/Projects/dCortex/aircrew/grounding.py:443).

With no tool results, submit **“I cannot confirm that C-2087 is legal without checking.”** The gate sets `unbacked_verdicts=True` and `blocking=True`. In a two-response scripted agent run, repeating this honest statement causes it to be replaced with the generic “I could not ground every figure” fallback, although it states neither a figure nor a positive legality verdict.

This is the false-suppression counterpart of finding 2: the regex treats a word as an assertion without recognizing its use in uncertainty or negation.

Smallest robust fix: provide a distinct, deterministic “not checked / cannot establish” response form outside factual verdict claims. Have the model select that state when appropriate. Avoid a broad negation exception in the current regex, which would introduce new ways to hide assertions.

Regression: `test_an_honest_refusal_is_not_an_unbacked_legality_claim`.

**10. [P2] The reported tie count depends on how many rows are displayed.**

Source: [tools.py:646](C:/Users/gamin/Projects/dCortex/aircrew/tools.py:646).

Call `resolve_cover(pairing_id="P-2201", role="Captain", limit=2)`. Its claim says **“2 legal options tie at INR 24,000.”** The complete ranking contains nine options at that price. With `limit=1`, the tie disclosure disappears altogether.

`legal_candidate_count` is computed before truncation, but the tool layer counts ties from the truncated `data.options`. The recommendation prompt asks the model to tell the controller when cost stops deciding; the presentation limit changes that operational statement.

Smallest fix: compute `tie_count` over the full candidate list in the engine before slicing, and generate the claim from that field. This also gives the UI a reliable way to indicate undisplayed equal-cost candidates.

Regression: `test_presentation_limit_does_not_change_the_number_of_ties`.

**What the 38-question result establishes**

The unchanged baseline passes **36/36 gradable questions**, with Q36 and Q38 excluded as rubrics, and **19/19 scenario checks**. That is useful evidence of agreement with the published expected outputs. It does not independently validate the scenario transformations above.

| Questions | What I would challenge as a controller |
| --- | --- |
| Q21; C-2210 alternative in Q31 | The positioned two-day assignment is cleared on the original timetable. The computed delay invalidates the displayed next-day rest. The C-3310 recommendation itself is not invalidated by this finding. |
| Q33 | The named roles and INR 75,000 tariff do not identify an available, legal reserve crew. “All satisfied” has no member-level validation behind it. |
| Q35 | Independent closure waits yield an impossible outbound/return rotation and incorrectly clear the return crew. |
| Q19/Q29 if routed with `with_recovery=False` | The impact tool introduces an uncomputed zero-breach conclusion. The affected-flight lookup itself is not the failure. |
| Q27 | The historical routing failure is documented in `docs/ISSUES.md`, but there is no transcript here to verify a fresh answer. Rating-only validation still cannot establish callability, and finding 2 allows that distinction to be lost in prose. |
| Remaining questions | No demonstrated rejection from this review. The passing engine adapters do not establish the correctness of unseen browser replies. Q36/Q38 still require rubric assessment. |

There is also an important limit on the replay score: `replay.score` checks whether expected strings/numbers occur anywhere in tool results, not whether the delivered reply says the right thing. It ignores booleans. For example, `score({"legal": False}, [{"data": {"legal": True}}])` returns `(True, [])`. Keep this metric labelled as retrieval/routing coverage; add subject/polarity and delivered-answer checks before treating it as answer correctness. The existing documentation sometimes distinguishes these measures well; preserve that distinction consistently.

**What is right, and what the tool context actually costs**

Keep the joined deterministic tools. Having the model assemble duty histories, certifications, reserve windows and costs would move the difficult work into the least checkable component. Keep impact separate from ranked recovery, the explicit `missing` contract, the distinction between legal and callable, the merged-week rest checks, per-candidate exclusion reasons, and fresh ranking after exclusions. The bugs above arise where these principles are incompletely carried through.

The UI also has useful safeguards: model HTML is escaped, evidence can be reopened for an earlier turn, empty evidence clears the previous workspace, and cancellation is distinguished from a crew candidate. These behaviors passed the DOM checks.

The context argument in `TOOL_DESIGN.md` is directionally right but its “heaviest realistic turn is under 5,000 tokens of tool results” claim does not cover joint resolution. The ordinary Q32 two-captain call produces **37,569 characters** after renumbering, about **9,392 tokens using the document's own chars/4 estimate**, before the prompt or history. This is a size estimate, not tokenizer measurement. Its response duplicates assignments across the optimal plan, named assignments, tied plans and per-vacancy results. Preserve the deterministic join; consider returning compact plan IDs and tie counts to the model while retaining detailed alternatives for UI retrieval. A wholesale split into raw-file tools would solve the wrong problem.

**Verification and review artifacts**

| Check | Observed result |
| --- | --- |
| `python -m tests.test_agent_loop` | 24/24 pass |
| `python -m aircrew.scoreboard` | 36/36 gradable; 19/19 scenario checks |
| `node tests/ui_check.js` | 68 DOM checks pass |
| `python -m unittest tests.test_review_astra -v` | 13 expected failures, each documenting an unfixed defect |
| `node tests/review_astra_ui.js` | Three reproduced known failures |

The new tests are [test_review_astra.py](C:/Users/gamin/Projects/dCortex/tests/test_review_astra.py) and [review_astra_ui.js](C:/Users/gamin/Projects/dCortex/tests/review_astra_ui.js). Their expected-failure status is intentional and is not a correctness pass; remove the marker as each production fix lands. The JS harness likewise labels current defects as known failures and flags unexpected fixes for review. No production code or dataset records were changed.

`jsdom` was initially missing; the existing harness silently skipped. I installed `jsdom@26` under `%TEMP%\dcortex-review-deps`, outside the repository, and ran the JS checks with `NODE_PATH` pointing at its `node_modules`. These are DOM checks, not a visual/browser-layout certification. The test script's catch-all dependency handling also reported “not installed” for an incompatible installed version, so a printed SKIP should not be treated as a passing UI run.

**The three things I would do with one day**

1. Make factual answer rendering enforce subject, scope, units and verdict polarity; close the unknown-ID path and align the UI with accepted versus withheld outcomes. Use the adversarial gate cases above as acceptance tests.
2. Make positioned and closure recovery operate on the actual propagated schedule, and downgrade unnamed reserve-set clearance to an unverified proposal until named people have been checked. Make every drill-down use the same evaluated scenario.
3. Isolate and serialize desk sessions, then rerun all 38 questions through the agent and browser with saved replies, tool arguments and screenshots. Score the delivered statements and operational feasibility separately from expected-output matching.
