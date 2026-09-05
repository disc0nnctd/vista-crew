"""Offline review regressions. Expected failures document unfixed defects.

Run: python -m unittest tests.test_review_astra -v
Remove each expectedFailure decorator when its production fix lands.
"""

import json
import threading
import unittest
import urllib.request
from datetime import timedelta
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from aircrew import grounding, server
from aircrew.data import parse_utc
from aircrew.tools import Tools, dispatch, renumber
from tests.test_agent_loop import _agent, _Call, _Msg


class ReviewAstra(unittest.TestCase):
    def setUp(self):
        self.tools = Tools()

    def cover(self):
        result = dispatch(self.tools, "resolve_cover", {
            "pairing_id": "P-2291", "vacated_by": "C-1042",
        })
        renumber([result])
        return result

    def test_cost_claim_cannot_be_attached_to_another_person(self):
        result = self.cover()
        cost = next(c for c in result["claims"] if c["value"] == 18500)
        reply = "Assign C-2210 at {{claim:" + cost["id"] + "}}."
        checked = grounding.check(reply, [result])
        self.assertTrue(checked.blocking, checked.rendered)

    def test_a_failed_assignment_does_not_authorize_a_positive_verdict(self):
        result = dispatch(self.tools, "check_assignment", {
            "crew_id": "C-2087", "pairing_id": "P-2291",
        })
        self.assertFalse(result["data"]["rules"]["legal"])
        checked = grounding.check("C-2087 is legal and cheapest.", [result])
        self.assertTrue(checked.blocking, checked.rendered)

    def test_small_money_amount_requires_evidence(self):
        checked = grounding.check("Cancellation costs INR 7.", [])
        self.assertTrue(checked.blocking, checked.rendered)

    def test_operational_times_and_word_numbers_require_evidence(self):
        checked = grounding.check("Report at 23:59Z. Rest is ten hours.", [])
        self.assertTrue(checked.blocking, checked.rendered)

    def test_unknown_claim_is_withheld_after_the_corrective_round(self):
        agent = _agent([
            _Msg(tool_calls=[_Call("resolve_cover", {
                "pairing_id": "P-2291", "vacated_by": "C-1042",
            })]),
            _Msg("{{claim:c999}}"),
            _Msg("{{claim:c999}}"),
        ], self.tools)
        turn = agent.ask("Who should cover?")
        self.assertTrue(turn.grounding.blocking, turn.reply)
        self.assertNotIn("[unknown claim", turn.reply)

    def test_an_honest_refusal_is_not_an_unbacked_legality_claim(self):
        reply = "I cannot confirm that C-2087 is legal without checking."
        checked = grounding.check(reply, [])
        self.assertTrue(checked.ok, checked)

    @unittest.expectedFailure
    def test_positioned_cover_is_checked_on_its_delayed_timetable(self):
        result = self.cover()["data"]
        candidate = next(o for o in result["options"] if o["crew_id"] == "C-2210")
        duties = self.tools.e.cover_duties("P-2291", "C-2210")
        duties[0] = duties[0].shifted(candidate["delay_hours"], hold_report=False)
        findings = self.tools.e.rules.check_duties("C-2210", duties, positioned=True)
        self.assertEqual(candidate["legal"], not findings,
                         [f.render() for f in findings])

    @unittest.expectedFailure
    def test_positioned_assignment_and_timeline_agree(self):
        result = self.tools.e.check_assignment("C-2210", "P-2291", positioned=True)
        self.assertEqual(result["rules"]["legal"], result["timeline"]["legal"])

    @unittest.expectedFailure
    def test_delay_recovery_checks_the_reserve_people_it_clears(self):
        rules = self.tools.e.rules
        with patch.object(rules, "check_duties", wraps=rules.check_duties) as check:
            result = self.tools.e.delay_recovery("VT-DXA", "2026-09-16", 1.5)
        self.assertTrue(result["recommended"]["legal"])
        self.assertTrue(check.called, result["recommended"])

    @unittest.expectedFailure
    def test_closure_does_not_clear_a_tail_on_an_impossible_rotation(self):
        result = self.tools.e.closure_recovery("BLR", "2026-09-17", "08:00", "14:00")
        rows = {r["flight_id"]: r for r in result["per_flight_assessment"]}
        outbound = self.tools.ds.flight_by_id["DX453-2026-09-17"]
        inbound = self.tools.ds.flight_by_id["DX454-2026-09-17"]
        first_arrival = parse_utc(outbound["arr_utc"]) + timedelta(
            hours=rows[outbound["flight_id"]]["min_delay_hours"])
        tail = rows[inbound["flight_id"]]
        # Even allowing zero turnaround, this aircraft cannot depart before arrival.
        earliest_departure = max(first_arrival, parse_utc(inbound["dep_utc"]) +
                                 timedelta(hours=tail["min_delay_hours"]))
        earliest_release = earliest_departure + timedelta(hours=inbound["block_hours"], minutes=30)
        day = self.tools.ds.pairing_by_id[tail["pairing_id"]]["days"][0]
        minimum_fdp = (earliest_release - parse_utc(day["report_utc"])).total_seconds() / 3600
        self.assertGreater(minimum_fdp, tail["fdp_limit"])
        self.assertNotEqual(tail["action"], "delay (crew legal)")

    def test_impact_only_closure_does_not_claim_zero_fdp_breaches(self):
        args = {"kind": "closure", "station": "BLR", "on_date": "2026-09-17",
                "start_utc": "08:00", "end_utc": "14:00"}
        full = dispatch(self.tools, "simulate_disruption", args)
        impact = dispatch(self.tools, "simulate_disruption", {**args, "with_recovery": False})
        self.assertEqual(len(full["data"]["flights_needing_recrew"]), 10)
        self.assertNotIn("0 would push their crew past FDP", impact["summary"])

    def test_presentation_limit_does_not_change_the_number_of_ties(self):
        args = {"pairing_id": "P-2201", "role": "Captain"}
        full = dispatch(self.tools, "resolve_cover", {**args, "limit": 100})
        small = dispatch(self.tools, "resolve_cover", {**args, "limit": 2})
        tie = lambda result: next(c["value"] for c in result["claims"]
                                  if "legal options tie" in c["text"])
        self.assertEqual(tie(full), tie(small))

    def test_two_browser_sessions_do_not_share_conversation_history(self):
        agent = _agent([_Msg("Ready."), _Msg("Ready.")], self.tools)
        with patch.object(server, "_agent", agent), patch.object(server.Handler, "log_message"):
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                for session, question in [("desk-a", "C-3310 is also sick."),
                                          ("desk-b", "Which captain should cover?")]:
                    req = urllib.request.Request(
                        f"http://127.0.0.1:{httpd.server_port}/api/chat",
                        data=json.dumps({"message": question}).encode(),
                        headers={"Content-Type": "application/json", "Cookie": f"session={session}"},
                    )
                    with urllib.request.urlopen(req) as response:
                        self.assertEqual(response.status, 200)
                questions = [m["content"] for m in agent.messages if m["role"] == "user"]
                self.assertNotIn("C-3310 is also sick.", questions)
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join()


if __name__ == "__main__":
    unittest.main()
