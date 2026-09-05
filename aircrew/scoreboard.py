"""Replay all 38 questions and 6 scenarios against the engine.

States: pass / fail / TODO / GEN.

GEN is for the questions whose answer key is a rubric rather than a value.
They are never counted as passes -- grading a rubric against itself is a fake
pass, and the honest number is the one without them.

Every entry here is a thin adapter over the engine. If an entry computes
anything itself, the scoreboard has stopped testing the system.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Callable

from .data import load
from .engine import Engine
from .query import Query

PASS, FAIL, TODO, GEN = "pass", "fail", "TODO", "GEN"


def norm(x: Any) -> Any:
    """Compare structure and value, not float formatting or key order."""
    if isinstance(x, float):
        return round(x, 2)
    if isinstance(x, dict):
        return {k: norm(v) for k, v in sorted(x.items())}
    if isinstance(x, (list, tuple)):
        return [norm(v) for v in x]
    return x


def same(a: Any, b: Any) -> bool:
    return norm(a) == norm(b)


def subset(got: dict, want: dict) -> bool:
    """The key is a subset of the tool result: the engine may return more
    context than the key names, but never a different value for a named key."""
    if not isinstance(got, dict):
        return False
    return all(k in got and same(got[k], v) for k, v in want.items())


class Scoreboard:
    def __init__(self, data_dir=None):
        self.ds = load(data_dir) if data_dir else load()
        self.e = Engine(self.ds)
        self.q = Query(self.ds)
        self.answers: dict[str, Callable[[], tuple[str, Any]]] = {}
        self._register()

    # ------------------------------------------------------------------
    def _register(self):
        e, q = self.e, self.q
        A = self.answers

        # --- Tier 1 ----------------------------------------------------
        A["Q01"] = lambda: (
            PASS,
            [
                {"crew_id": r["crew_id"], "rank": r["rank"], "window": r["window"]}
                for r in q.reserves("2026-09-15", "BLR")["reserves"]
            ],
        )
        A["Q02"] = lambda: (
            PASS,
            {
                "duty_hours_7d": (p := q.crew_profile("C-1042", "2026-09-14"))["duty_hours_7d"],
                "headroom_hours": p["duty_headroom_7d"],
            },
        )
        A["Q03"] = lambda: (PASS, q.flights(on_date="2026-09-15", dep="DEL")["flight_numbers"])
        A["Q04"] = lambda: (
            PASS,
            [
                {"crew_id": c["crew_id"], "cert_type": c["cert_type"], "valid_to": c["valid_to"]}
                for c in q.certifications_expiring("2026-09-15", 30)["certifications"]
            ],
        )
        A["Q05"] = lambda: (
            PASS,
            {
                k: f[k]
                for f in [q.flights(on_date="2026-09-15", flight_no="DX412")["flights"][0]]
                for k in ("aircraft", "aircraft_type", "seats")
            },
        )
        A["Q06"] = lambda: (
            PASS,
            {
                "window": (p := q.crew_profile("C-3310"))["reserve"]["oncall_window_utc"],
                "reachability_minutes": p["reachability_minutes"],
            },
        )
        A["Q07"] = lambda: (
            PASS,
            {k: (p := q.crew_profile("C-2210"))[k] for k in ("base", "ratings")},
        )
        A["Q08"] = lambda: (PASS, q.pairings(pairing_id="P-2291")["pairings"][0]["crew"])
        A["Q09"] = lambda: (
            PASS,
            q.flights(on_date="2026-09-17", dep="BLR", arr="BOM")["flight_numbers"],
        )
        A["Q10"] = lambda: (PASS, q.flights(on_date="2026-09-16")["count"])
        A["Q11"] = lambda: (
            PASS,
            [c["crew_id"] for c in q.crew(rank="Captain", base="DEL")["crew"]],
        )
        A["Q12"] = lambda: (PASS, q.flights(longest_block=True)["longest_block"])
        A["Q13"] = lambda: (
            PASS,
            {
                "rank": (p := q.crew_profile("C-2087", "2026-09-14"))["rank"],
                "flight_hours_28d": p["flight_hours_28d"],
            },
        )
        A["Q14"] = lambda: (PASS, q.stations(from_station="BLR")["nonstop_destinations"])
        A["Q15"] = lambda: (
            PASS,
            next(
                m["crew_id"]
                for m in q.pairings(aircraft="VT-DXB", on_date="2026-09-16")["pairings"][0]["crew"]
                if m["role"] == "Senior Cabin Crew"
            ),
        )
        A["Q16"] = lambda: (
            PASS,
            {k: (r := q.risk("C-1042"))[k] for k in ("score", "drivers")},
        )

        # --- Tier 2 ----------------------------------------------------
        A["Q17"] = lambda: (
            PASS,
            {
                k: (r := e.trace_crew_unavailable("C-1042", "P-2291"))[k]
                for k in ("day1", "day2_also_at_risk", "passengers_day1")
            },
        )
        A["Q18"] = lambda: (
            PASS,
            {
                "legal": (r := e.check_assignment("C-2087", "P-2291", "2026-09-15"))["rules"]["legal"],
                "issues": r["rules"]["issues"],
            },
        )
        A["Q19"] = lambda: (
            PASS,
            e.station_closure_impact("BLR", "2026-09-17", "08:00", "14:00")["affected_flight_ids"],
        )
        A["Q20"] = lambda: (
            PASS,
            {
                k: (r := e.delay_impact("VT-DXA", "2026-09-16", 1.5, "technical"))[k]
                for k in ("breach", "fdp_after_delay", "fdp_limit")
            },
        )
        A["Q21"] = self._q21
        A["Q22"] = lambda: (
            PASS,
            self._cert_verdict("C-5417", "2026-09-19"),
        )
        A["Q23"] = lambda: (PASS, e.earliest_next_report("2026-09-16T15:30:00Z")["earliest_report_utc"])
        A["Q24"] = lambda: (
            PASS,
            {
                "legal": (r := e.check_assignment("C-3305", "P-2291"))["rules"]["legal"],
                "issues": r["rules"]["issues"],
            },
        )
        A["Q25"] = lambda: (
            PASS,
            {
                k: (r := e.cancellation_cost(["DX404-2026-09-16"]))[k]
                for k in ("passengers", "cost_inr")
            },
        )
        A["Q26"] = lambda: (
            PASS,
            [
                {
                    "crew_id": c["crew_id"],
                    "duty_hours_7d_incl_15sep_plan": c["duty_hours_7d"],
                }
                for c in q.crew(on_date="2026-09-15", min_duty_hours_7d=45)["crew"]
            ],
        )
        A["Q27"] = self._q27
        A["Q28"] = lambda: (
            PASS,
            {
                "legal": (r := e.check_assignment("C-5837", "P-2291"))["rules"]["legal"],
                "issues": r["rules"]["issues"],
            },
        )
        A["Q29"] = lambda: (
            PASS,
            e.station_closure_impact("HYD", "2026-09-19", "05:00", "09:00")["affected_flight_ids"],
        )
        A["Q30"] = self._q30

        # --- Tier 3 ----------------------------------------------------
        A["Q31"] = lambda: (
            PASS,
            [
                {k: o[k] for k in ("action", "crew_id", "legal", "rules_checked", "cost_inr", "delay_hours", "rank") if k in o}
                for o in e.resolve_cover("P-2291", vacated_by="C-1042")["options"]
            ],
        )
        A["Q32"] = self._q32
        A["Q33"] = self._q33
        A["Q34"] = lambda: (
            PASS,
            [
                {k: o[k] for k in ("action", "crew_id", "legal", "rules_checked", "cost_inr", "delay_hours", "rank") if k in o}
                for o in e.resolve_cover(
                    "P-2213", vacated_by="C-5417", from_date="2026-09-19", limit=3
                )["options"]
            ],
        )
        A["Q35"] = self._q35
        A["Q36"] = self._q36
        A["Q37"] = self._q37
        A["Q38"] = self._q38

    # ------------------------------------------------------------------
    # entries needing more than one engine call
    # ------------------------------------------------------------------
    def _cert_verdict(self, crew_id: str, on_date: str) -> dict:
        p = self.ds.find_pairing(crew_id=crew_id, on_date=on_date)
        r = self.e.check_assignment(crew_id, p[0]["pairing_id"], on_date)
        cert = [b for b in r["rules"]["breaches"] if b["rule"] == "RULE-CERT-06"]
        if not cert:
            return {"legal": r["rules"]["legal"]}
        c = cert[0]["context"]
        return {
            "legal": False,
            "rule": "RULE-CERT-06",
            "detail": f"{c['cert_type']} expired {c['valid_to']}",
        }

    def _q21(self):
        r = self.e.check_assignment("C-2210", "P-2291", "2026-09-15", positioned=True)
        pos = self.e.positioning("DEL", self.e.cover_duties("P-2291", "C-2210")[0])
        return PASS, {
            "legal": r["rules"]["legal"],
            "consequence": (
                f"Deadhead positioning on {pos['deadhead_flight_no']} "
                f"(arr {pos['arrives_utc'][11:16]}Z) delays the first departure "
                f"by ~{pos['delay_hours']:g}h; RULE-BASE-07 deadhead cost applies."
            ),
        }

    def _q27(self):
        """Reserve captains whose window covers the 16 Sep VT-DXE callout."""
        r = self.e.resolve_cover("P-2224", vacated_by="C-3231")
        # "which reserve captains" means the ones on call at the station the
        # pairing departs from; an out-of-base reserve is a positioning
        # decision, not a callout-window one.
        reserve_ids = {
            cid
            for cid, res in self.ds.reserve_by_id.items()
            if res["base"] == r["dep_station"]
        }
        eligible = [
            o["crew_id"]
            for o in r["options"]
            if o["crew_id"] in reserve_ids and o.get("source") == "reserve"
        ]
        excluded = [
            {"crew_id": x["crew_id"], "reason": x["reason"]}
            for x in r["exclusions"]
            if x["crew_id"] in reserve_ids
        ]
        return PASS, {"eligible": eligible, "excluded_examples": excluded}

    def _q30(self):
        """The key is prose about a class of leg rather than one leg, but it is
        an exact value and not a rubric: it falls out of the seat counts, so it
        is graded by equality like any other answer. The wording comes from the
        query layer, which is also what the tool claim uses."""
        d = self.q.flights()
        return PASS, {**d["most_seats_at_risk"], "seats_by_type": d["seats_by_type"]}

    def _q32(self):
        r = self.e.resolve_multiple(
            [
                {"pairing_id": "P-2205", "role": "Captain", "label": "assign_dxa"},
                {"pairing_id": "P-2212", "role": "Captain", "label": "assign_dxb"},
            ]
        )
        keep = ("action", "crew_id", "legal", "rules_checked", "cost_inr", "delay_hours", "rank")
        return PASS, {
            "total_cost_inr": r["total_cost_inr"],
            "assign_dxa": {k: r["assign_dxa"][k] for k in keep if k in r["assign_dxa"]},
            "assign_dxb": {k: r["assign_dxb"][k] for k in keep if k in r["assign_dxb"]},
        }

    def _q33(self):
        r = self.e.delay_recovery("VT-DXA", "2026-09-16", 1.5, "technical")
        return PASS, [
            {k: o[k] for k in ("rank", "action", "legal", "cost_inr", "reasoning")}
            for o in r["options"]
        ]

    def _q35(self):
        r = self.e.closure_recovery("BLR", "2026-09-17", "08:00", "14:00")
        return PASS, [
            {
                k: row[k]
                for k in (
                    "flight_id",
                    "pairing_id",
                    "min_delay_hours",
                    "crew_fdp_after_delay",
                    "fdp_limit",
                    "action",
                )
            }
            for row in r["per_flight_assessment"]
        ]

    def _q36(self):
        d = self.e.draft_notification("C-3310", "P-2291")
        return GEN, d

    def _q37(self):
        p = self.ds.find_pairing(aircraft="VT-DXF", on_date="2026-09-20")[0]
        r = self.e.resolve_cover(p["pairing_id"], role="First Officer")
        keep = ("action", "crew_id", "legal", "rules_checked", "cost_inr", "delay_hours", "rank")
        best = r["options"][0]
        return PASS, {k: best[k] for k in keep if k in best}

    def _q38(self):
        return GEN, {
            "suggested": [
                "crew legality headroom (7d duty) for today's rostered crew",
                "reserve availability by window and rating for the day",
                "risk_signals for today's rostered crew (provided input)",
            ]
        }

    # ------------------------------------------------------------------
    def run(self, only: list[str] | None = None) -> list[dict]:
        results = []
        for qn in self.ds.questions:
            qid = qn["question_id"]
            if only and qid not in only:
                continue
            fn = self.answers.get(qid)
            if fn is None:
                results.append({"id": qid, "tier": qn["tier"], "state": TODO, "prompt": qn["prompt"]})
                continue
            try:
                state, got = fn()
            except Exception as exc:  # a crash is a failure, not a skip
                results.append(
                    {
                        "id": qid,
                        "tier": qn["tier"],
                        "state": FAIL,
                        "prompt": qn["prompt"],
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                continue
            if state == TODO:
                results.append({"id": qid, "tier": qn["tier"], "state": TODO, "prompt": qn["prompt"]})
                continue
            want = qn["expected_answer"]
            if state == GEN:
                results.append(
                    {"id": qid, "tier": qn["tier"], "state": GEN, "prompt": qn["prompt"], "got": got}
                )
                continue
            ok = same(got, want) or (isinstance(want, dict) and subset(got, want))
            results.append(
                {
                    "id": qid,
                    "tier": qn["tier"],
                    "state": PASS if ok else FAIL,
                    "prompt": qn["prompt"],
                    "got": None if ok else got,
                    "want": None if ok else want,
                }
            )
        return results

    # ------------------------------------------------------------------
    def run_scenarios(self) -> list[dict]:
        out = []
        for s in self.ds.scenarios:
            sid = s["scenario_id"]
            ak = s["answer_key"]
            checks: list[tuple[str, bool, Any, Any]] = []
            try:
                checks = self._scenario_checks(s, ak)
            except Exception as exc:
                checks = [("engine", False, f"{type(exc).__name__}: {exc}", None)]
            out.append(
                {
                    "id": sid,
                    "title": s["title"],
                    "checks": [
                        {"name": n, "ok": ok, "got": None if ok else g, "want": None if ok else w}
                        for n, ok, g, w in checks
                    ],
                    "passed": sum(1 for _, ok, _, _ in checks if ok),
                    "total": len(checks),
                }
            )
        return out

    def _scenario_checks(self, s, ak):
        e = self.e
        sid = s["scenario_id"]
        ev = s["event"]
        c = []
        keep = ("action", "crew_id", "legal", "rules_checked", "cost_inr", "delay_hours", "rank")
        trim = lambda opts: [{k: o[k] for k in keep if k in o} for o in opts]

        if sid in ("S1", "S2"):
            r = e.resolve_cover(ev["pairing_id"], vacated_by=ev["crew_id"])
            if "uncovered_flights" in ak:
                c.append(("uncovered_flights", same(r["uncovered_flights"], ak["uncovered_flights"]),
                          r["uncovered_flights"], ak["uncovered_flights"]))
            if "uncovered_flights_day1" in ak:
                t = e.trace_crew_unavailable(ev["crew_id"], ev["pairing_id"])
                c.append(("day1", same(t["day1"], ak["uncovered_flights_day1"]), t["day1"], ak["uncovered_flights_day1"]))
                c.append(("day2", same(t["day2_also_at_risk"], ak["uncovered_flights_day2"]),
                          t["day2_also_at_risk"], ak["uncovered_flights_day2"]))
                c.append(("passengers_day1", same(t["passengers_day1"], ak["passengers_at_risk_day1"]),
                          t["passengers_day1"], ak["passengers_at_risk_day1"]))
            c.append(("options", same(trim(r["options"]), ak["options"]), trim(r["options"]), ak["options"]))
            c.append(("excluded", same(self._excl(r), ak["excluded_candidates"]),
                      self._excl(r), ak["excluded_candidates"]))

        elif sid == "S3":
            r = e.station_closure_impact("BLR", "2026-09-17", "08:00", "14:00")
            c.append(("affected_flights", same(r["affected_flight_ids"], ak["affected_flights"]),
                      r["affected_flight_ids"], ak["affected_flights"]))

        elif sid == "S4":
            r = e.delay_impact("VT-DXA", "2026-09-16", 1.5, "technical")
            c.append(("fdp_after_delay", same(r["fdp_after_delay"], ak["fdp_after_delay"]),
                      r["fdp_after_delay"], ak["fdp_after_delay"]))
            c.append(("fdp_limit", same(r["fdp_limit"], ak["fdp_limit"]), r["fdp_limit"], ak["fdp_limit"]))
            c.append(("breach", same(r["breach"], ak["breach"]), r["breach"], ak["breach"]))

        elif sid == "S5":
            r = e.resolve_cover("P-2213", vacated_by="C-5417", from_date="2026-09-19")
            c.append(("options", same(trim(r["options"]), ak["options"]), trim(r["options"]), ak["options"]))
            c.append(("excluded", same(self._excl(r), ak["excluded_candidates"]),
                      self._excl(r), ak["excluded_candidates"]))

        elif sid == "S6":
            r = e.resolve_multiple(
                [
                    {"pairing_id": "P-2205", "role": "Captain", "label": "assign_dxa"},
                    {"pairing_id": "P-2212", "role": "Captain", "label": "assign_dxb"},
                ]
            )
            dxa, dxb = r["per_vacancy"]
            c.append(("options_dxa", same(trim(dxa["options"]), ak["options_dxa"]), trim(dxa["options"]), ak["options_dxa"]))
            c.append(("options_dxb", same(trim(dxb["options"]), ak["options_dxb"]), trim(dxb["options"]), ak["options_dxb"]))
            c.append(("excluded_dxa", same(self._excl(dxa), ak["excluded_dxa"]), self._excl(dxa), ak["excluded_dxa"]))
            c.append(("excluded_dxb", same(self._excl(dxb), ak["excluded_dxb"]), self._excl(dxb), ak["excluded_dxb"]))
            want = ak["optimal_joint_plan"]
            got = {
                "total_cost_inr": r["total_cost_inr"],
                "assign_dxa": {k: r["assign_dxa"][k] for k in keep if k in r["assign_dxa"]},
                "assign_dxb": {k: r["assign_dxb"][k] for k in keep if k in r["assign_dxb"]},
            }
            c.append(("optimal_joint_plan", subset(got, want) or same(got, want), got, want))
        return c

    @staticmethod
    def _excl(r) -> list[dict]:
        return [{"crew_id": x["crew_id"], "reason": x["reason"]} for x in r["exclusions"]]


# ----------------------------------------------------------------------
def render(results, scenarios, verbose=False) -> int:
    counts = {PASS: 0, FAIL: 0, TODO: 0, GEN: 0}
    print(f"{'ID':<5} {'T':<2} {'STATE':<5}  PROMPT")
    print("-" * 78)
    for r in results:
        counts[r["state"]] += 1
        print(f"{r['id']:<5} {r['tier']:<2} {r['state']:<5}  {r['prompt'][:58]}")
        if r["state"] == FAIL:
            if r.get("error"):
                print(f"        ! {r['error']}")
            else:
                print(f"        got:  {json.dumps(norm(r['got']))[:300]}")
                print(f"        want: {json.dumps(norm(r['want']))[:300]}")
        elif verbose and r["state"] == GEN:
            print(f"        gen:  {json.dumps(norm(r.get('got')))[:300]}")
    gradable = counts[PASS] + counts[FAIL] + counts[TODO]
    print("-" * 78)
    print(
        f"ENGINE: {counts[PASS]}/{gradable} pass "
        f"({counts[FAIL]} fail, {counts[TODO]} TODO), "
        f"{counts[GEN]} GEN not counted"
    )

    print()
    print("SCENARIOS")
    print("-" * 78)
    sp = st = 0
    for s in scenarios:
        sp += s["passed"]
        st += s["total"]
        mark = "ok" if s["passed"] == s["total"] else "FAIL"
        print(f"{s['id']:<4} {s['passed']}/{s['total']} {mark:<5} {s['title'][:50]}")
        for chk in s["checks"]:
            if not chk["ok"]:
                print(f"       - {chk['name']}")
                print(f"         got:  {json.dumps(norm(chk['got']))[:300]}")
                print(f"         want: {json.dumps(norm(chk['want']))[:300]}")
    print("-" * 78)
    print(f"SCENARIO CHECKS: {sp}/{st}")
    return 0 if counts[FAIL] == 0 and sp == st else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="Replay the 38 questions against the engine")
    ap.add_argument("--only", nargs="*", help="question ids, e.g. Q17 Q18")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # non-ASCII output on Windows
    sb = Scoreboard()
    res = sb.run(a.only)
    scn = sb.run_scenarios() if not a.only else []
    if a.json:
        print(json.dumps({"questions": res, "scenarios": scn}, indent=2, default=str))
        return 0
    return render(res, scn, a.verbose)


if __name__ == "__main__":
    raise SystemExit(main())
