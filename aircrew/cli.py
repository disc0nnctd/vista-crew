"""Drive the engine from a terminal, with no model attached.

This is the demo's safety net: every recovery the product can recommend can be
produced here, so a network failure costs the presentation its chat pane and
nothing else.
"""

from __future__ import annotations

import argparse
import json
import sys

from .tools import Tools, dispatch


def show(env: dict, as_json: bool):
    if as_json:
        print(json.dumps(env, indent=2, default=str))
        return
    print(env["summary"])
    if env["claims"]:
        print("\nEstablished:")
        for c in env["claims"]:
            print(f"  [{c['id']}] {c['text']}")
    if env["missing"]:
        print("\nNot established by this result:")
        for m in env["missing"]:
            print(f"  - {m}")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Crew Ops Advisor engine, without the model",
        epilog="Example: python -m aircrew.cli resolve --pairing P-2291 --vacated-by C-1042",
    )
    ap.add_argument("--json", action="store_true", help="print the full tool envelope")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("resolve", help="ranked cover options for a vacancy")
    p.add_argument("--pairing", required=True)
    p.add_argument("--vacated-by")
    p.add_argument("--role")
    p.add_argument("--from-date")
    p.add_argument("--exclude", nargs="*", default=None)
    p.add_argument("--limit", type=int, default=8)

    p = sub.add_parser("check", help="can this person cover this pairing")
    p.add_argument("--crew", required=True)
    p.add_argument("--pairing", required=True)
    p.add_argument("--from-date")
    p.add_argument("--positioned", action="store_true")

    p = sub.add_parser("trace", help="which flights lose a crew member")
    p.add_argument("--crew", required=True)
    p.add_argument("--pairing")

    p = sub.add_parser("timeline", help="merged week with a proposed cover")
    p.add_argument("--crew", required=True)
    p.add_argument("--pairing")

    p = sub.add_parser("delay", help="simulate a delay and its recovery")
    p.add_argument("--aircraft", required=True)
    p.add_argument("--date", required=True)
    p.add_argument("--hours", type=float, required=True)
    p.add_argument("--mode", choices=["technical", "positioning"], required=True)

    p = sub.add_parser("closure", help="simulate a station closure and its recovery")
    p.add_argument("--station", required=True)
    p.add_argument("--date", required=True)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)

    p = sub.add_parser("profile", help="one crew member")
    p.add_argument("--crew", required=True)
    p.add_argument("--on-date")

    p = sub.add_parser("notify", help="draft a callout")
    p.add_argument("--crew", required=True)
    p.add_argument("--pairing", required=True)

    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    t = Tools()

    call = {
        "resolve": ("resolve_cover", lambda: {
            "pairing_id": a.pairing, "vacated_by": a.vacated_by, "role": a.role,
            "from_date": a.from_date, "exclude_crew": a.exclude, "limit": a.limit}),
        "check": ("check_assignment", lambda: {
            "crew_id": a.crew, "pairing_id": a.pairing,
            "from_date": a.from_date, "positioned": a.positioned}),
        "trace": ("trace_disruption", lambda: {"crew_id": a.crew, "pairing_id": a.pairing}),
        "timeline": ("duty_timeline", lambda: {"crew_id": a.crew, "pairing_id": a.pairing}),
        "delay": ("simulate_disruption", lambda: {
            "kind": "delay", "aircraft": a.aircraft, "on_date": a.date,
            "delay_hours": a.hours, "mode": a.mode}),
        "closure": ("simulate_disruption", lambda: {
            "kind": "closure", "station": a.station, "on_date": a.date,
            "start_utc": a.start, "end_utc": a.end}),
        "profile": ("crew_profile", lambda: {"crew_id": a.crew, "on_date": a.on_date}),
        "notify": ("draft_notification", lambda: {"crew_id": a.crew, "pairing_id": a.pairing}),
    }[a.cmd]

    show(dispatch(t, call[0], call[1]()), a.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
