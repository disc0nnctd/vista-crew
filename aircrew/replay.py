"""Replay all 38 questions THROUGH THE AGENT, not only through the engine.

These are different numbers and only one of them is what a judge will test. The
engine number says the arithmetic is right. This number says the model asks the
right question and reports the answer without inventing anything on the way.

Each question is scored on three things, all of them mechanical:

  routed    the agent called at least one tool
  grounded  every figure in the reply came from a tool result (the claim gate)
  correct   the expected answer's figures all appear in the tool results the
            agent actually obtained

`correct` is deliberately checked against the tool results rather than against
the prose. The engine already proves the values; what this measures is whether
the agent reached them. A reply that is beautifully worded but computed from the
wrong pairing fails here, which is the point.

Needs AIRCREW_API_KEY. Without it, run `python -m aircrew.scoreboard` for the
engine number.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from .data import load
from .grounding import _norm_num, collect
from .tools import Tools


def expected_figures(expected) -> set[str]:
    """Every number and identifier the answer key contains."""
    out: set[str] = set()

    def walk(x):
        if isinstance(x, bool) or x is None:
            return
        if isinstance(x, (int, float)):
            out.add(_norm_num(x))
        elif isinstance(x, str):
            out.add(x)
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, (list, tuple)):
            for v in x:
                walk(v)

    walk(expected)
    return out


def result_blob(tool_results: list[dict]) -> tuple[str, set[str]]:
    # ensure_ascii=False matters: the answer keys contain an em dash and an en
    # dash, and the default escaping turns those into \u2014 / \u2013 so the
    # expected string can never be found. That silently failed two questions
    # the agent had actually answered correctly.
    blob = json.dumps(tool_results, default=str, ensure_ascii=False)
    _, figures = collect(tool_results)
    return blob, figures


def score(expected, tool_results) -> tuple[bool, list[str]]:
    blob, figures = result_blob(tool_results)
    missing = []
    for f in expected_figures(expected):
        if f in figures or f in blob:
            continue
        missing.append(f)
    return not missing, missing


def main(argv=None):
    ap = argparse.ArgumentParser(description="Replay the 38 questions through the agent")
    ap.add_argument("--only", nargs="*", help="question ids")
    ap.add_argument("--model", default=os.environ.get("AIRCREW_MODEL", "gpt-5.6-luna"))
    ap.add_argument("--out", help="write the full transcript to this JSON file")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if not (os.environ.get("AIRCREW_API_KEY") or os.environ.get("OPENAI_API_KEY")):
        print(
            "No API key. Set AIRCREW_API_KEY to measure the agent number.\n"
            "The engine number is `python -m aircrew.scoreboard`."
        )
        return 2

    from .agent import Agent
    from .scoreboard import GEN, Scoreboard

    ds = load()
    tools = Tools(ds)
    # The scoreboard already knows which keys are rubrics rather than values.
    # Scoring a rubric by string match can only ever fail, so those are marked
    # GEN here too and kept out of the denominator -- the same rule as the
    # engine number: never count a rubric, in either direction.
    rubric = {r["id"] for r in Scoreboard(ds.dir).run() if r["state"] == GEN}
    rows = []
    print(f"{'ID':<5} {'T':<2} {'ROUTE':<6} {'GROUND':<7} {'CORRECT':<8} PROMPT")
    print("-" * 88)
    for q in ds.questions:
        if a.only and q["question_id"] not in a.only:
            continue
        agent = Agent(model=a.model, tools=tools)  # a fresh desk per question
        try:
            turn = agent.ask(q["prompt"])
        except Exception as exc:
            rows.append({"id": q["question_id"], "tier": q["tier"], "error": str(exc)})
            print(f"{q['question_id']:<5} {q['tier']:<2} {'ERROR':<6} {'':<7} {'':<8} {exc}")
            continue
        routed = bool(turn.tool_calls)
        grounded = bool(turn.grounding and turn.grounding.ok)
        is_gen = q["question_id"] in rubric
        correct, missing = score(q["expected_answer"], turn.tool_results)
        rows.append(
            {
                "id": q["question_id"],
                "tier": q["tier"],
                "prompt": q["prompt"],
                "reply": turn.reply,
                "tools": [c["name"] for c in turn.tool_calls],
                "calls": turn.tool_calls,
                "ungrounded": (turn.grounding.ungrounded_numbers if turn.grounding else []),
                "routed": routed,
                "grounded": grounded,
                "corrected": turn.corrected,
                "correct": None if is_gen else correct,
                "gen": is_gen,
                "missing_figures": [] if is_gen else missing[:10],
                "tool_results": turn.tool_results,
            }
        )
        print(
            f"{q['question_id']:<5} {q['tier']:<2} "
            f"{('yes' if routed else 'NO'):<6} "
            f"{('yes' if grounded else 'NO'):<7} "
            f"{('GEN' if is_gen else ('yes' if correct else 'NO')):<8} {q['prompt'][:44]}"
        )

    n = len(rows)
    gen = sum(1 for r in rows if r.get("gen"))
    gradable = n - gen
    ok = sum(1 for r in rows if r.get("correct"))
    gr = sum(1 for r in rows if r.get("grounded"))
    rt = sum(1 for r in rows if r.get("routed"))
    co = sum(1 for r in rows if r.get("corrected"))
    print("-" * 88)
    print(f"AGENT: {ok}/{gradable} reached the answer; {rt}/{n} routed to a tool; "
          f"{gr}/{n} fully grounded; {co} needed one correction; "
          f"{gen} GEN not counted")
    if a.out:
        open(a.out, "w").write(json.dumps(rows, indent=2, default=str))
        print(f"transcript -> {a.out}")
    return 0 if ok == gradable else 1


if __name__ == "__main__":
    raise SystemExit(main())
