"""The agent loop and the claim gate, exercised without a network call.

The engine's correctness is covered by `python -m aircrew.scoreboard`, which
replays the 38 questions and 6 scenarios against the answer keys. What is left
to test is the boundary itself: that a figure the model invents does not reach
the controller.

Run with: python -m tests.test_agent_loop   (or pytest)
"""

from __future__ import annotations

import json

from aircrew import grounding
from aircrew.agent import Agent
from aircrew.tools import Tools, dispatch, renumber


# --- a stub model endpoint ------------------------------------------------
# The seam is `_post`, the one HTTP call the agent makes. Faking at that level
# means the test still exercises the real response parsing, the real history
# handling and the real tool dispatch, and only the network is pretend.
def _Msg(content=None, tool_calls=None):
    """One assistant message, in the wire shape a gateway returns."""
    msg: dict = {"role": "assistant"}
    if content is not None:
        msg["content"] = content
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def _Call(name, args, call_id="call_1"):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def _agent(script, tools):
    a = Agent(tools=tools, api_key="test-key")
    replies = [{"choices": [{"message": m}]} for m in script]

    def _post(path, payload):
        assert path == "/chat/completions", path
        assert payload["messages"][0]["role"] == "system"
        return replies.pop(0)

    a._post = _post
    return a


# --- the gate itself ------------------------------------------------------
def test_placeholder_is_substituted_with_validated_text():
    t = Tools()
    env = dispatch(t, "resolve_cover", {"pairing_id": "P-2291", "vacated_by": "C-1042", "limit": 3})
    cid = env["claims"][2]["id"]
    g = grounding.check(f"Call out {{{{claim:{cid}}}}}.", [env])
    assert g.ok
    assert "C-3310" in g.rendered and "18,500" in g.rendered


def test_invented_figure_is_caught():
    t = Tools()
    env = dispatch(t, "trace_disruption", {"crew_id": "C-1042", "pairing_id": "P-2291"})
    g = grounding.check("Cancelling would cost INR 1,250,000.", [env])
    assert not g.ok
    assert "1,250,000" in g.ungrounded_numbers


def test_figure_present_in_tool_data_is_allowed():
    """The model may quote any field the tool returned, not only the claims."""
    t = Tools()
    env = dispatch(t, "trace_disruption", {"crew_id": "C-1042", "pairing_id": "P-2291"})
    g = grounding.check("486 passengers are booked on day 1.", [env])
    assert g.ok


def test_rule_limits_are_not_flagged():
    g = grounding.check("RULE-DUTY-02 caps duty at 60 hours in any 7 days.", [])
    assert g.ok


def test_a_tool_name_never_reaches_the_controller():
    """The `missing` field says "call resolve_cover", which is the right steer
    for the model and the wrong words for a crew controller: it reads as a
    debug log leaking into an operational answer. The steer stays technical,
    the reply has to be plain."""
    t = Tools()
    env = dispatch(t, "trace_disruption", {"crew_id": "C-1042", "pairing_id": "P-2291"})
    renumber([env])

    leaked = grounding.check(
        "Three flights are uncovered. Call resolve_cover for prices.", [env])
    assert not leaked.ok
    assert leaked.leaked_tool_names == ["resolve_cover"]

    plain = grounding.check(
        "Three flights are uncovered on day 1: {{claim:c1}}. I have not priced "
        "the options yet. Shall I rank them?", [env])
    assert plain.ok
    assert "resolve_cover" not in plain.rendered

    # Ordinary English must survive. "lookup" and "validate" are real words,
    # and rejecting "let me validate that before you act" was exactly the
    # false positive this file keeps warning about. Only the unambiguous
    # underscore-shaped names are matched.
    for english in ("The lookups are done.",
                    "Let me validate that before you act.",
                    "A quick lookup shows three flights."):
        assert grounding.check(english, [env]).ok, english

    # A leaked name is worth one rewrite and never worth withholding a true
    # answer: it is a style fault, not an untruth.
    assert not leaked.blocking
    assert grounding.check("Cancelling would cost INR 1,250,000.", [env]).blocking


def test_a_date_is_not_treated_as_a_figure():
    """Found by an A/B on the live model: it wrote "three flights on 15 Sep and
    three on 16 Sep", which is entirely true, and the gate failed it for the
    ungrounded figures 15 and 16. Controllers say dates like that constantly,
    and a gate that fires on correct answers gets switched off."""
    t = Tools()
    env = dispatch(t, "trace_disruption", {"crew_id": "C-1042", "pairing_id": "P-2291"})
    renumber([env])

    for dated in ("Three flights on 15 Sep and three on 16 Sep.",
                  "Cover is needed from 15 September.",
                  "Report at 06:00Z on 15 Sep 2026.",
                  "Sep 15 has three flights."):
        assert grounding.check(dated, [env]).ok, dated

    # the exemption is for dates only; invented figures beside one still fail
    g = grounding.check("It affects 999 passengers on 15 Sep.", [env])
    assert not g.ok and g.ungrounded_numbers == ["999"]


def test_a_figure_written_as_words_does_not_slip_past():
    """Digits were checked; prose was not. "Cancelling would cost one million
    two hundred and fifty thousand rupees" passed the whole gate while
    "INR 1,250,000" was caught, which made the guarantee a formatting
    preference. Only magnitude words are matched, never "three" or "both", so
    ordinary counting language is untouched."""
    t = Tools()
    env = dispatch(t, "trace_disruption", {"crew_id": "C-1042", "pairing_id": "P-2291"})
    renumber([env])

    for prose in ("Cancelling would cost one million two hundred and fifty thousand rupees.",
                  "Roughly twelve lakh fifty thousand rupees.",
                  "It costs a few hundred rupees."):
        g = grounding.check(prose, [env])
        assert not g.ok, prose
        assert g.spelled_figures, prose

    # counting words a correct reply actually uses must not trip it
    for fine in ("Three legal options tie at the same cost.",
                 "Both days of the pairing are affected.",
                 "486 passengers are booked on day 1.",
                 "3 flights uncovered on day 1."):
        assert not grounding.check(fine, [env]).spelled_figures, fine

    # nothing the engine writes may ever trip it
    for text in [env["summary"]] + [c["text"] for c in env["claims"]] + env["missing"]:
        assert not grounding.MAGNITUDE_RE.search(text), text


def test_a_real_figure_under_the_wrong_label_is_caught():
    """The gate's known blind spot, closed. "The delay costs 486" passes every
    other check because 486 really is in this turn's results -- as a passenger
    count. The engine knows what each field holds, the sentence says what it
    thinks it is quoting, and a conflict between the two is a mislabel.

    Deliberately conservative: it fires only when the figure lives in fields of
    exactly one kind and the sentence clearly asserts a different one. A gate
    that fires on correct answers gets switched off."""
    t = Tools()
    trace = dispatch(t, "trace_disruption", {"crew_id": "C-1042", "pairing_id": "P-2291"})
    cover = dispatch(t, "resolve_cover", {"pairing_id": "P-2291", "vacated_by": "C-1042"})
    renumber([trace, cover])

    for wrong in ("The delay costs 486.",
                  "Cancelling strands 15,00,000 passengers.",
                  "There are 18,500 passengers at risk."):
        g = grounding.check(wrong, [trace, cover])
        assert not g.ok, wrong
        assert g.mislabelled_figures, wrong
        assert "written as" in g.corrective_prompt() or "but you wrote it as" in g.corrective_prompt()

    # the same figures, correctly labelled, must pass
    for right in ("486 passengers are booked on day 1.",
                  "Cancelling all six flights costs INR 15,00,000.",
                  "Assign C-3310 at INR 18,500.",
                  "5 candidates are legal and 19 were excluded."):
        assert grounding.check(right, [trace, cover]).ok, right

    # and no claim the engine wrote may ever trip it
    for env in (trace, cover):
        for c in env["claims"]:
            assert not grounding.mislabelled(c["text"], [trace, cover]), c["text"]


def test_a_single_brace_placeholder_is_still_substituted():
    """The documented form is {{claim:c3}}. luna writes {claim:c3} often enough
    that raw tokens reached the screen in a live run, which looks broken and
    hides the figure. Both forms substitute; anything still placeholder-shaped
    afterwards fails the turn rather than being printed."""
    t = Tools()
    env = dispatch(t, "resolve_cover", {"pairing_id": "P-2291", "vacated_by": "C-1042"})
    renumber([env])
    for form in ("{{claim:c1}}", "{claim:c1}", "{{ claim: c1 }}"):
        # mid-sentence, so the bare figure is substituted: the model already
        # wrote the label ("There are ...")
        g = grounding.check(f"There are {form} legal candidates.", [env])
        assert g.ok, form
        assert "There are 5 legal candidates." == g.rendered, (form, g.rendered)
        assert "claim:" not in g.rendered, form

        # standing alone, the claim supplies its own label
        g = grounding.check(f"{form}", [env])
        assert g.ok, form
        assert g.rendered == "5 candidates are legal", (form, g.rendered)

    # an id this turn does not have is never printed as a token
    bad = grounding.check("Assign {claim:c99}.", [env])
    assert not bad.ok


def test_the_short_form_is_the_bare_value():
    """Inside a sentence the model is building, only the value is substituted,
    and the model writes every surrounding word including the unit.

    An earlier version kept the unit in the short form and needed a
    de-duplicator to stop "5 legal legal candidates". That then failed on
    "at **INR {{claim}}**", because the markdown hid the join and the reply
    said "INR INR 18,500". One rule beats three: substitute the value, let the
    model own the words."""
    t = Tools()
    env = dispatch(t, "trace_disruption", {"crew_id": "C-1042", "pairing_id": "P-2291"})
    cover = dispatch(t, "resolve_cover", {"pairing_id": "P-2291", "vacated_by": "C-1042"})
    renumber([env, cover])

    assert grounding.check("affecting **{{claim:c2}} passengers**.", [env]).rendered ==         "affecting **486 passengers**."
    assert grounding.check("There are **{{claim:c4}}** legal candidates.", [env, cover]).rendered ==         "There are **5** legal candidates."
    # standing alone it still supplies its own label
    assert grounding.check("{{claim:c2}}.", [env]).rendered == "486 passengers on day 1."


def test_a_claim_does_not_repeat_what_the_model_already_wrote():
    """Claims are whole sentences so they stand up alone, but the model writes
    around them, so the placeholder lands mid-sentence: "the cheapest option is
    the cheapest legal option is Assign...". Only words already on the page are
    dropped, so no figure can be lost this way."""
    from aircrew.grounding import _fit

    assert _fit("the cheapest joint plan costs INR 42,500",
                "The cheapest joint plan costs ") == "INR 42,500"
    assert _fit("C-2210 is rated on A320",
                "C-2210 is based in DEL and is rated on ") == "A320"
    # nothing typed yet: the claim stands whole
    assert _fit("5 candidates are legal", "") == "5 candidates are legal"
    # an unrelated lead-in must not eat the claim
    assert _fit("5 candidates are legal", "Here is the answer. ") == "5 candidates are legal"
    # and the figure survives every path
    for before in ("The cheapest joint plan costs ", "", "Cost: "):
        assert "42,500" in _fit("the cheapest joint plan costs INR 42,500", before)


# --- the loop -------------------------------------------------------------
def test_loop_grounds_a_good_answer():
    t = Tools()
    env = dispatch(t, "resolve_cover", {"pairing_id": "P-2291", "vacated_by": "C-1042", "limit": 3})
    cid = env["claims"][2]["id"]
    # the ids the live run mints will differ; ask for the third claim by shape
    a = _agent(
        [
            _Msg(tool_calls=[_Call("resolve_cover",
                                   {"pairing_id": "P-2291", "vacated_by": "C-1042", "limit": 3})]),
            _Msg(content="Call out C-3310, the cheapest legal cover."),
        ],
        t,
    )
    turn = a.ask("C-1042 is sick for P-2291, what should I do?")
    assert turn.grounding.ok
    assert turn.tool_calls[0]["name"] == "resolve_cover"


def test_loop_sends_one_corrective_turn_and_recovers():
    a = _agent(
        [
            _Msg(tool_calls=[_Call("trace_disruption",
                                   {"crew_id": "C-1042", "pairing_id": "P-2291"})]),
            _Msg(content="Three flights uncovered; cancelling costs INR 1,250,000."),
            # plain words: naming the tool would now fail its own check
            _Msg(content="Three flights are uncovered on day 1. I do not have a "
                         "cancellation cost yet. Shall I price the options?"),
        ],
        Tools(),
    )
    turn = a.ask("What breaks if C-1042 goes sick?")
    assert turn.corrected
    assert turn.grounding.ok
    assert "1,250,000" not in turn.reply


def test_loop_refuses_rather_than_printing_an_ungrounded_figure():
    """If the model cannot ground the figure on the second try, the answer is
    withheld. Printing it anyway is the failure the product exists to prevent."""
    a = _agent(
        [
            _Msg(tool_calls=[_Call("trace_disruption",
                                   {"crew_id": "C-1042", "pairing_id": "P-2291"})]),
            _Msg(content="Cancelling costs INR 1,250,000."),
            _Msg(content="It is still INR 1,250,000."),
        ],
        Tools(),
    )
    turn = a.ask("What does cancelling cost?")
    assert "1,250,000" not in turn.reply
    assert "could not ground" in turn.reply.lower()


def test_the_loop_runs_without_a_third_party_package():
    """The engine, the CLI and the workspace have no dependencies, and the
    chat pane must not quietly add one. A missing package used to raise
    SystemExit, which is a BaseException, so the server's `except Exception`
    let it kill the request thread and the browser saw a closed socket."""
    import sys

    t = Tools()
    a = _agent([_Msg(content="RULE-DUTY-02 caps duty at 60 hours in any 7 days.")], t)
    a.ask("what is the duty limit?")
    assert "openai" not in sys.modules


def test_a_tool_that_raises_returns_a_result_the_model_can_act_on():
    """`dispatch` is called unguarded inside the loop, so anything it raises
    kills the whole turn. A missing field has to come back as a readable
    envelope instead, or one blank argument costs the controller the answer."""
    t = Tools()
    for name, args in (
        ("validate", {"claim_kind": "crew_qualified", "crew_id": "C-3305"}),
        ("validate", {"claim_kind": "assignment_legal", "crew_id": "C-3305"}),
        ("validate", {"claim_kind": "cheapest_option"}),
        ("duty_timeline", {"crew_id": "C-3310", "pairing_id": "P-9999"}),
    ):
        env = dispatch(t, name, args)
        assert "error" in env["data"], f"{name} {args} should report, not raise"
        assert name in env["summary"]
        assert not env.get("claims"), "a failed call must state nothing"


def test_a_blank_optional_argument_does_not_become_a_missing_one():
    """The model fills optionals with "", `clean_args` strips them, and the
    tool then indexes a key that is no longer there. Both halves must hold."""
    t = Tools()
    blank = dispatch(t, "validate", {"claim_kind": "crew_qualified",
                                     "crew_id": "C-3305", "aircraft_type": ""})
    assert "error" in blank["data"]
    good = dispatch(t, "validate", {"claim_kind": "crew_qualified",
                                    "crew_id": "C-3305", "aircraft_type": "A320"})
    assert "CONFIRMED" in good["summary"]


def test_a_tool_result_carries_only_what_is_read():
    """A tool result is context the model pays for on every turn of the
    conversation, so it holds what something consumes and nothing else.

    resolve_cover is the heaviest call in the product. Its exclusion list used
    to carry `all_breaches`, a structured copy of the same findings already in
    `reason`: 13,232 of 17,445 characters, read by no panel, no test and no
    answer key. Dropping it took the whole result from ~5,800 tokens to
    ~2,500."""
    t = Tools()
    env = dispatch(t, "resolve_cover", {"pairing_id": "P-2291", "vacated_by": "C-1042"})
    for x in env["data"]["exclusions"]:
        assert set(x) == {"crew_id", "rule", "rule_text", "reason"}, sorted(x)
        assert x["reason"], "the graded wording must survive"

    size = len(json.dumps(env, default=str, ensure_ascii=False))
    assert size < 13000, f"resolve_cover grew to {size} chars; it was 10,096"


# --- the tools ------------------------------------------------------------
def test_no_tool_accepts_a_cost_or_a_verdict():
    """There is nowhere for a remembered figure to enter the engine."""
    from aircrew.tools import SCHEMAS

    banned = {"cost", "cost_inr", "price", "total", "legal", "illegal", "verdict",
              "breach", "passengers", "count"}
    for s in SCHEMAS:
        for name in s["parameters"]["properties"]:
            assert name not in banned, f"{s['name']} takes {name}"


def test_no_tool_can_skip_a_rule():
    from aircrew.tools import SCHEMAS

    for s in SCHEMAS:
        for name in s["parameters"]["properties"]:
            assert "skip" not in name and "ignore" not in name and "only" not in name


def test_exclude_crew_re_ranks_rather_than_reading_the_next_row():
    t = Tools()
    first = dispatch(t, "resolve_cover", {"pairing_id": "P-2291", "vacated_by": "C-1042"})
    top = first["data"]["recommended"]["crew_id"]
    again = dispatch(t, "resolve_cover",
                     {"pairing_id": "P-2291", "vacated_by": "C-1042", "exclude_crew": [top]})
    assert again["data"]["recommended"]["crew_id"] != top
    # and it is a real re-simulation: every option was legality-checked again
    assert all(o["rules_checked"] for o in again["data"]["options"] if o["crew_id"])


def test_a_short_form_keeps_its_noun_but_never_says_it_twice():
    # The model writes "{{claim}} operate BLR-BOM", so a bare "2" leaves the
    # sentence without a subject; it also writes "{{claim}} certifications
    # expire", where the noun would then appear twice.
    c = {"text": "6 certifications expiring", "short": "6 certifications"}
    assert grounding._pick(c, "There are ", " expire within 30 days.") == "6 certifications"
    assert grounding._pick(c, "There are ", " certifications expire in 30 days.") == "6"
    assert grounding._pick(c, "", "") == "6 certifications expiring"


def test_every_figure_claim_can_be_written_mid_sentence():
    # A claim with no short substitutes its whole standalone sentence wherever
    # the model puts it, which is where the stutters came from.
    t = Tools()
    calls = [
        ("crew_profile", {"crew_id": "C-1042"}),
        ("trace_disruption", {"crew_id": "C-1042", "pairing_id": "P-2291"}),
        ("check_assignment", {"crew_id": "C-3310", "pairing_id": "P-2291"}),
        ("resolve_cover", {"pairing_id": "P-2291", "vacated_by": "C-1042"}),
        ("lookup", {"entity": "flights", "on_date": "2026-09-15"}),
        ("lookup", {"entity": "certifications", "on_date": "2026-09-15"}),
    ]
    bare = []
    for name, args in calls:
        for c in dispatch(t, name, args)["claims"]:
            if c["kind"] in ("number", "list") and c["short"] == c["text"]:
                bare.append((name, c["text"]))
    assert not bare, bare


def test_a_single_flight_lookup_makes_no_comparison():
    # "the most seats at risk is any A320 leg (162 seats), against " -- a
    # comparison with nothing on the other side, which reached an answer.
    t = Tools()
    d = dispatch(t, "lookup",
                 {"entity": "flights", "on_date": "2026-09-15", "flight_no": "DX412"})
    assert "most_seats_at_risk" not in d["data"]
    assert any(c["short"] == "162 seats" for c in d["claims"])
    assert d["claims"][0]["text"] == "1 flight matched"


def test_the_fdp_figure_and_its_limit_are_separate_claims():
    # As one claim the model could only cite it twice, and wrote "the duty
    # becomes 12.75h, against a 12.75h limit". Both figures grounded, sentence
    # false.
    t = Tools()
    r = dispatch(t, "simulate_disruption", {
        "kind": "delay", "aircraft": "VT-DXA", "on_date": "2026-09-16",
        "delay_hours": 1.5, "mode": "technical",
    })
    shorts = [c["short"] for c in r["claims"] if c["kind"] == "number"]
    assert len({s for s in shorts if s.endswith("h")}) > 1, shorts


def test_a_verdict_claim_does_not_repeat_the_label_the_model_wrote():
    t = Tools()
    r = dispatch(t, "check_assignment", {"crew_id": "C-3305", "pairing_id": "P-2291"})
    renumber([r])
    cid = r["claims"][0]["id"]
    draft = ("C-3305 is illegal for P-2291 under RULE-DUTY-02 (maximum 60 duty "
             "hours in any 7 consecutive days): {{claim:%s}}." % cid)
    g = grounding.check(draft, [r])
    assert g.ok, g
    assert "illegal: RULE-DUTY-02" not in g.rendered, g.rendered
    assert "would exceed 60h/7d" in g.rendered


def test_the_corrective_turn_may_take_more_than_one_tool_round():
    # A tier-3 question needs several calls to answer. Forcing text after one
    # round produced "I cannot run the required computation in this turn" on a
    # question the engine answers completely.
    t = Tools()
    a = _agent([
        _Msg(tool_calls=[_Call("trace_disruption",
                               {"crew_id": "C-1042", "pairing_id": "P-2291"})]),
        _Msg("Cover costs INR 91,234."),                      # ungrounded
        _Msg(tool_calls=[_Call("resolve_cover",
                               {"pairing_id": "P-2291", "vacated_by": "C-1042"})]),
        _Msg(tool_calls=[_Call("check_assignment",
                               {"crew_id": "C-3310", "pairing_id": "P-2291"})]),
        _Msg("C-3310 can cover it."),
    ], t)
    turn = a.ask("What should I do?")
    assert turn.reply == "C-3310 can cover it.", turn.reply
    assert len(turn.tool_calls) == 3, turn.tool_calls


def test_the_engine_runs_with_no_filesystem():
    # The deployed Worker has no problem_statement/data to read, so the tables
    # arrive as a bundled module. If that path can drift from the file path,
    # the deployed engine is a second engine -- so it is checked, not assumed.
    import json
    from pathlib import Path

    from aircrew import data as ds

    tables = {n: json.loads((Path(ds.__file__).resolve().parent.parent
                             / "problem_statement" / "data" / f"{n}.json")
                            .read_text(encoding="utf-8"))
              for n in ds.REQUIRED}
    bundled = Tools(ds.Dataset(records=tables))
    onfile = Tools()
    for name, args in [
        ("resolve_cover", {"pairing_id": "P-2291", "vacated_by": "C-1042"}),
        ("check_assignment", {"crew_id": "C-2087", "pairing_id": "P-2291"}),
        ("crew_profile", {"crew_id": "C-1042"}),
    ]:
        assert (dispatch(bundled, name, args)["data"]
                == dispatch(onfile, name, args)["data"]), name


if __name__ == "__main__":
    import sys

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"ok   {fn.__name__}")
        except Exception as exc:
            failed += 1
            print(f"FAIL {fn.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
