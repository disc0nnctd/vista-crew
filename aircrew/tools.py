"""The tool surface, and the claim envelope that makes an unvalidated figure
structurally hard to state.

Ten tools, each named after the thing a controller asks for. Every one of them
returns the same envelope:

    {summary, claims, missing, data}

`data` is the engine's structured result -- the only thing the workspace draws
from. `claims` is the list of figures and verdicts this result establishes, each
with an id. `missing` says what the result does *not* establish, because an
impact result looks answer-shaped and a model asked "what should I do?" will
otherwise fill the gap.

The model may hypothesise freely in prose. It may not put a number or a verdict
on screen except through a claim id, which `grounding.py` substitutes with the
validated text. That is the whole boundary: the model chooses the question, the
engine owns every answer.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any, Callable

from .data import Dataset, load
from .engine import Engine
from .query import Query
from .rules import ALL_RULES

_ids = itertools.count(1)


@dataclass
class Claim:
    """One validated statement. `text` is what may be rendered; `value` is the
    machine-readable version; `basis` names the code and records behind it.

    `short` is the same fact with its label stripped: "DX412, DX413, DX588"
    beside "3 flights uncovered on day 1: DX412, DX413, DX588". A claim has to
    stand up alone, so `text` says what the figure is; but the model writes its
    own lead-in, and substituting the full sentence into "breaks three flights
    on day one: ..." says everything twice. Both forms are the engine's words,
    so choosing between them cannot introduce a figure the engine did not
    produce.
    """

    id: str
    kind: str  # "number" | "verdict" | "legality" | "list"
    text: str
    value: Any
    basis: list[str] = field(default_factory=list)
    short: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "text": self.text,
            "short": self.short or self.text,
            "value": self.value,
            "basis": self.basis,
        }


def claim(kind: str, text: str, value: Any, basis: list[str] | None = None,
          short: str | None = None) -> Claim:
    return Claim(f"c{next(_ids)}", kind, text, value, basis or [], short)


def renumber(results: list[dict]) -> list[dict]:
    """Renumber every claim in a turn to c1, c2, c3 ... in place.

    Ids are minted from a process-global counter, so by the twentieth question
    of a replay they read c200+ while the system prompt's example says
    {{claim:c7}} -- and the model copies the example. That referenced an id
    that did not exist in the turn, the gate rejected the reply as citing an
    unknown claim, and the turn cost a needless correction round. It fired on
    13 of 38 questions before this existed. Per-turn numbering makes the ids
    small, predictable, and impossible to collide with the prompt.
    """
    n = 0
    for r in results:
        for c in r.get("claims", []):
            n += 1
            c["id"] = f"c{n}"
    return results


def envelope(
    summary: str,
    data: dict,
    claims: list[Claim] | None = None,
    missing: list[str] | None = None,
) -> dict:
    return {
        "summary": summary,
        "claims": [c.to_dict() for c in (claims or [])],
        "missing": missing or [],
        "data": data,
    }


def inr(n: int | float) -> str:
    return f"INR {int(round(n)):,}"


def nos(flight_ids) -> str:
    """"DX412-2026-09-15" is a key, not a flight number.

    The date is already in the sentence, and three of these in a row is a wall
    of characters a controller has to parse to find three numbers.
    """
    return ", ".join(str(f).split("-")[0] for f in flight_ids)


def plural(n: int, word: str) -> str:
    """"1 flights match" reads like a bug, and the model copies it verbatim."""
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


class Tools:
    """Every tool takes what a controller would actually say -- a pairing id, or
    the crew member who dropped out, or a tail plus a date. None of them takes a
    cost, a duration, a count or a verdict, so there is nowhere for a remembered
    figure to enter."""

    def __init__(self, ds: Dataset | None = None):
        self.ds = ds or load()
        self.e = Engine(self.ds)
        self.q = Query(self.ds)

    def _bad_date(self, on_date: str | None) -> dict | None:
        """A date outside the schedule returns nothing, and nothing looks like
        an answer: "0 flights affected" is what a wrong year produces. Say so
        instead, and name the window."""
        if not on_date or self.ds.date_in_schedule(on_date):
            return None
        dates = self.ds.schedule_dates
        return envelope(
            f"{on_date} is outside the schedule, which runs {dates[0]} to "
            f"{dates[-1]}. No result was computed -- check the year.",
            {"error": "date outside schedule", "requested": on_date,
             "schedule_from": dates[0], "schedule_to": dates[-1]},
            # The refusal is the answer here, so give the model something to
            # cite. Without it the reply came back as "no schedule result was
            # computed", which reads as the system failing rather than the
            # system declining to invent a week it does not have.
            [claim("verdict",
                   f"{on_date} is outside the schedule, which covers "
                   f"{dates[0]} to {dates[-1]}, so nothing was computed",
                   {"requested": on_date, "from": dates[0], "to": dates[-1]},
                   ["flights.json"])],
            ["this is not an empty result; nothing was computed"],
        )

    # ==================================================================
    # 1. lookup -- every Tier-1 question, one entity at a time
    # ==================================================================
    def lookup(
        self,
        entity: str,
        on_date: str | None = None,
        dep: str | None = None,
        arr: str | None = None,
        flight_no: str | None = None,
        aircraft: str | None = None,
        crew_id: str | None = None,
        pairing_id: str | None = None,
        rank: str | None = None,
        base: str | None = None,
        rating: str | None = None,
        min_duty_hours_7d: float | None = None,
        within_days: int | None = None,
        longest_block: bool = False,
    ) -> dict:
        q = self.q
        bad = self._bad_date(on_date)
        if bad and entity in ("flights", "reserves", "pairings"):
            return bad
        if entity == "flights":
            d = q.flights(on_date, dep, arr, flight_no, aircraft, longest_block)
            cl = [claim("number", plural(d["count"], "flight") + " matched", d["count"], ["flights.json"],
                    short=plural(d["count"], "flight"))]
            if d["count"] == 1 and d.get("flights"):
                f0 = d["flights"][0]
                cl.append(claim("number",
                                f"{f0['flight_no']} on {f0['date']} is "
                                f"{'an' if f0['aircraft_type'][:1] in 'AEIOU' else 'a'} "
                                f"{f0['aircraft_type']} "
                                f"({f0['aircraft']}) with {f0['seats']} seats",
                                f0["seats"], ["flights.json"],
                                short=f"{f0['seats']} seats"))
            if d.get("most_seats_at_risk"):
                m = d["most_seats_at_risk"]
                cl.append(
                    claim(
                        "number",
                        f"the most seats at risk on one leg is {m['flights']}, "
                        f"against {m['vs']}",
                        m,
                        ["flights.json seats"],
                        short=m["flights"],
                    )
                )
            if longest_block and "longest_block" in d:
                lb = d["longest_block"]
                cl.append(
                    claim(
                        "number",
                        f"longest block time is {lb['block_hours']}h, on "
                        + ", ".join(lb["flights"]),
                        lb,
                        ["flights.json"],
                        short=f"{lb['block_hours']}h",
                    )
                )
            return envelope(
                f"{d['count']} flights. Schedule facts only.",
                d,
                cl,
                ["no crew, legality or cost information in this result"],
            )

        if entity == "crew":
            if crew_id:
                # "What is C-2210's base and rating?" is a profile question
                # however the model phrased the route to it.
                return self.crew_profile(crew_id, on_date)
            d = q.crew(rank, base, rating, on_date, min_duty_hours_7d)
            if "error" in d:
                return envelope(d["error"], d)
            note = (
                "7d duty shown per row"
                if on_date
                else "no dated totals: pass on_date for duty hours"
            )
            return envelope(
                f"{d['count']} crew match. {note}.",
                d,
                [claim("number", f"{d['count']} crew match", d["count"], ["crew.json"],
                       short=f"{d['count']} crew")],
                [] if on_date else ["duty hours and headroom need on_date"],
            )

        if entity == "reserves":
            if not on_date:
                return envelope("reserves needs on_date", {"error": "on_date required"})
            d = q.reserves(on_date, base)
            return envelope(
                f"{d['count']} reserves on {on_date}"
                + (f" at {base}" if base else "")
                + ". On-call window is eligibility to be called, not legality.",
                d,
                [claim("number", plural(d["count"], "reserve") + " on call", d["count"],
                       ["reserve_pool.json"], short=plural(d["count"], "reserve"))],
                ["being on call does not mean the assignment is legal; use check_assignment"],
            )

        if entity == "certifications":
            if not on_date:
                return envelope("certifications needs on_date", {"error": "on_date required"})
            d = q.certifications_expiring(on_date, within_days or 30)
            return envelope(
                f"{d['count']} certifications expire within {d['within_days']} days of {on_date}.",
                d,
                [claim("number", plural(d["count"], "certification") + " expiring", d["count"],
                       ["certifications.json"], short=plural(d["count"], "certification"))],
                ["validity is tested against valid_to only"],
            )

        if entity == "pairings":
            d = q.pairings(pairing_id, aircraft, on_date, crew_id)
            # A zero count is the dangerous result: it reads like an answer.
            # Say what would have to be true for it to be right.
            if not d["count"]:
                ds = self.q.ds
                return envelope(
                    "0 pairings match, which usually means a filter is wrong rather "
                    f"than the roster being empty. Tails are {', '.join(ds.tails)}; "
                    f"types are {', '.join(ds.aircraft_types)}; the schedule runs "
                    f"{ds.schedule_dates[0]} to {ds.schedule_dates[-1]}. Do not "
                    "report this as 'no pairings'.",
                    d,
                )
            return envelope(f"{d['count']} pairings match.", d)

        if entity == "risk":
            if not crew_id:
                return envelope("risk needs crew_id", {"error": "crew_id required"})
            d = q.risk(crew_id)
            if "error" in d:
                return envelope(d["error"], d)
            return envelope(
                f"Risk score for {crew_id} is {d['score']} (a provided input, not a rule).",
                d,
                [claim("number", f"{crew_id} disruption-risk score {d['score']}", d["score"],
                       ["risk_signals.json"], short=str(d["score"]))],
                ["risk score never makes an assignment illegal; it is context only"],
            )

        if entity == "stations":
            d = q.stations(dep)
            return envelope("Network stations.", d)

        return envelope(
            f"Unknown entity '{entity}'.",
            {"error": f"entity must be one of: flights, crew, reserves, "
                      f"certifications, pairings, risk, stations"},
        )

    # ==================================================================
    # 2. crew_profile
    # ==================================================================
    def crew_profile(self, crew_id: str, on_date: str | None = None) -> dict:
        d = self.q.crew_profile(crew_id, on_date)
        if "error" in d:
            return envelope(d["error"], d)
        return envelope(
            f"{d['rank']} {crew_id}, based {d['base']}, rated {', '.join(d['ratings'])}. "
            f"{d['duty_hours_7d']}h duty in the 7 days to {d['as_of']} "
            f"({d['duty_headroom_7d']}h headroom under the 60h limit).",
            d,
            [
                claim("number", f"{crew_id} has {d['duty_hours_7d']}h duty in 7d to {d['as_of']}",
                      d["duty_hours_7d"], ["RULE-DUTY-02", "duty_clocks.json", "rosters.json"],
                      short=f"{d['duty_hours_7d']}h"),
                claim("number", f"{crew_id} has {d['duty_headroom_7d']}h headroom under RULE-DUTY-02",
                      d["duty_headroom_7d"], ["RULE-DUTY-02"],
                      short=f"{d['duty_headroom_7d']}h"),
                claim("number", f"{crew_id} has {d['flight_hours_28d']}h block in 28d to {d['as_of']}",
                      d["flight_hours_28d"], ["RULE-FLT-03"],
                      short=f"{d['flight_hours_28d']}h"),
                claim("list", f"{crew_id} is rated on {', '.join(d['ratings'])}",
                      d["ratings"], ["crew.json"], short=", ".join(d["ratings"])),
            ],
            ["headroom is not a verdict; a specific assignment needs check_assignment"],
        )

    # ==================================================================
    # 3. trace_disruption -- impact only, never a ranking
    # ==================================================================
    def trace_disruption(
        self, crew_id: str, pairing_id: str | None = None, from_date: str | None = None
    ) -> dict:
        d = self.e.trace_crew_unavailable(crew_id, pairing_id, from_date)
        if "error" in d:
            return envelope(d["error"], d)
        cl = [
            claim("list", f"{len(d['day1'])} flights uncovered on day 1: " + nos(d["day1"]),
                  d["day1"], ["rosters.json"], short=nos(d["day1"])),
            claim("number", f"{d['passengers_day1']} passengers on day 1",
                  d["passengers_day1"], ["flights.json seats"],
                  short=str(d["passengers_day1"])),
        ]
        if d["day2_also_at_risk"]:
            cl.append(
                claim("list", f"{len(d['day2_also_at_risk'])} further flights at risk on day 2: "
                      + nos(d["day2_also_at_risk"]), d["day2_also_at_risk"],
                      ["rosters.json"], short=nos(d["day2_also_at_risk"]))
            )
        return envelope(
            f"{d['role_on_pairing']} {crew_id} off {d['pairing_id']}. "
            f"Day-1 passenger count is per day, not the pairing total.",
            d,
            cl,
            [
                "no candidates ranked and no costs computed here; call resolve_cover",
                "passenger figures are per day; cancellation cost is per leg",
            ],
        )

    # ==================================================================
    # 4. check_assignment -- two verdicts, not a flag
    # ==================================================================
    def check_assignment(
        self,
        crew_id: str,
        pairing_id: str,
        from_date: str | None = None,
        positioned: bool = False,
    ) -> dict:
        """The validator for any legality hypothesis. There is no parameter that
        skips a rule; `positioned` supplies RULE-BASE-07's precondition rather
        than disabling it."""
        d = self.e.check_assignment(crew_id, pairing_id, from_date, positioned)
        if "error" in d:
            return envelope(d["error"], d)
        legal, callable_ = d["rules"]["legal"], d["callable"]["callable"]
        cl = []
        if d.get("positioning"):
            cl.append(
                claim(
                    "verdict",
                    d["positioning"]["consequence"],
                    d["positioning"],
                    ["RULE-BASE-07", "flights.json", "costs.json"],
                )
            )
        cl += [
            claim(
                "legality",
                f"{crew_id} on {pairing_id} is "
                + ("legal under all seven rules" if legal else "illegal: " + "; ".join(d["rules"]["issues"])),
                {"legal": legal, "issues": d["rules"]["issues"]},
                ALL_RULES,
                short=("legal under all seven rules" if legal
                       else "; ".join(d["rules"]["issues"])),
            ),
            claim(
                "verdict",
                f"{crew_id} "
                + ("can be called out" if callable_ else f"cannot be called out: {d['callable']['reason']}"),
                {"callable": callable_, "reason": d["callable"]["reason"]},
                ["reserve_pool.json", "RULE-BASE-07"],
                short=("can be called out" if callable_
                       else d["callable"]["reason"]),
            ),
        ]
        return envelope(
            ("Legal" if legal else "ILLEGAL")
            + " under the seven rules; "
            + ("callable" if callable_ else "not callable")
            + ". These are two separate questions -- report the one that was asked.",
            d,
            cl,
            ["no cost computed here; call resolve_cover for prices and ranking"],
        )

    # ==================================================================
    # 5. duty_timeline
    # ==================================================================
    def duty_timeline(
        self, crew_id: str, pairing_id: str | None = None, from_date: str | None = None
    ) -> dict:
        proposed = (
            self.e.cover_duties(
                pairing_id, crew_id, __import__("aircrew.data", fromlist=["parse_date"]).parse_date(from_date) if from_date else None
            )
            if pairing_id
            else []
        )
        d = self.e.duty_timeline(crew_id, proposed)
        tight = [r for r in d["duties"] if r["rest_before_ok"] is False]
        return envelope(
            f"{len(d['duties'])} duty days"
            + (f", {len(tight)} with less than {d['min_rest_hours']}h rest before them" if tight else "")
            + ". This is what makes 'qualified, based, free and still illegal' visible.",
            d,
            [
                claim("legality", f"{crew_id}'s week with the proposed cover is "
                      + ("legal" if d["legal"] else "illegal"), d["legal"], ALL_RULES,
                      short=("legal" if d["legal"] else "illegal"))
            ]
            if pairing_id
            else [],
            ["a clash is shown as a duty overlap, never as negative rest"],
        )

    # ==================================================================
    # 6. simulate_disruption -- delay, closure, cancellation
    # ==================================================================
    def simulate_disruption(
        self,
        kind: str,
        aircraft: str | None = None,
        station: str | None = None,
        on_date: str | None = None,
        delay_hours: float | None = None,
        mode: str = "technical",
        start_utc: str | None = None,
        end_utc: str | None = None,
        flight_ids: list[str] | None = None,
        with_recovery: bool = True,
    ) -> dict:
        """`mode` is required for a delay and has no default that hides the
        choice: a technical delay holds report and grows the duty; positioning
        shifts both edges and leaves FDP unchanged."""
        bad = self._bad_date(on_date)
        if bad:
            return bad
        if kind == "delay":
            if not (aircraft and on_date and delay_hours is not None):
                return envelope("delay needs aircraft, on_date and delay_hours", {"error": "missing arguments"})
            d = (
                self.e.delay_recovery(aircraft, on_date, delay_hours, mode)
                if with_recovery
                else self.e.delay_impact(aircraft, on_date, delay_hours, mode)
            )
            if "error" in d:
                return envelope(d["error"], d)
            cl = [
                # Two figures, two claims. As one claim it could only offer
                # its short form for whichever figure the model wanted, and the
                # model wanted both: it cited this claim twice and wrote "the
                # duty becomes 12.75h, against a 12.75h limit". Both figures
                # were grounded and the sentence was false.
                claim("number", f"FDP after the delay is {d['fdp_after_delay']}h",
                      d["fdp_after_delay"], ["RULE-FDP-01"],
                      short=f"{d['fdp_after_delay']}h"),
                claim("number", f"the FDP limit is {d['fdp_limit']}h for "
                      f"{d['sectors']} sectors",
                      d["fdp_limit"], ["RULE-FDP-01"],
                      short=f"{d['fdp_limit']}h"),
                claim("legality", "the rostered crew "
                      + ("BREACH RULE-FDP-01" if d["breach"] else "stay inside RULE-FDP-01"),
                      d["breach"], ["RULE-FDP-01"],
                      short=("breach RULE-FDP-01" if d["breach"] else "stay inside RULE-FDP-01")),
            ]
            for o in d.get("options", []):
                cl.append(
                    claim("number", f"{o['action']} costs {inr(o['cost_inr'])}",
                          o["cost_inr"], ["costs.json"], short=inr(o["cost_inr"]))
                )
            return envelope(
                f"{mode} delay of {delay_hours}h. "
                + ("FDP breached." if d["breach"] else "No FDP breach."),
                d,
                cl,
                ["downstream duties of the same crew are not re-checked here; "
                 "use duty_timeline for the rest of their week",
                 # The reserve option is a role-and-tariff set, which is what
                 # the published answer key computes and what this reproduces.
                 # It names no individuals, so nobody's callout window or rest
                 # has been tested -- and a controller reading "all satisfied"
                 # would reasonably think otherwise.
                 "a reserve option names roles and their tariff, not people; "
                 "no individual's on-call window or rest has been checked here"],
            )

        if kind == "closure":
            if not (station and on_date and start_utc and end_utc):
                return envelope("closure needs station, on_date, start_utc, end_utc", {"error": "missing arguments"})
            d = (
                self.e.closure_recovery(station, on_date, start_utc, end_utc)
                if with_recovery
                else self.e.station_closure_impact(station, on_date, start_utc, end_utc)
            )
            # An impact call does not assess crew at all. Defaulting the
            # missing field to an empty list turned "not checked" into "zero
            # breaches" -- in the summary the model reads and on the screen the
            # controller reads -- while the same closure with recovery on
            # flags ten flights. An unasked question must not answer itself.
            assessed = "flights_needing_recrew" in d
            need = d.get("flights_needing_recrew") or []
            d = dict(d, recovery_assessed=assessed)
            return envelope(
                f"{len(d['affected_flight_ids'])} flights touch {station} while it is closed "
                f"({d['window_utc']}); "
                + (f"{len(need)} would push their crew past FDP."
                   if assessed else
                   "crew impact was not assessed in this result."),
                d,
                [
                    claim("list", f"{len(d['affected_flight_ids'])} flights affected: "
                          + nos(d["affected_flight_ids"]), d["affected_flight_ids"],
                          ["flights.json"], short=nos(d["affected_flight_ids"])),
                    claim("number", f"{d['passengers_at_risk']} passengers on affected flights",
                          d["passengers_at_risk"], ["flights.json seats"],
                          short=str(d["passengers_at_risk"])),
                ],
                ["arrivals count as well as departures; the window is half-open",
                 # Each delay is computed against the closure window alone, as
                 # the published answer key does. Two legs on one tail can
                 # therefore carry delays that could not both be flown.
                 "each flight's delay is measured against the closure window on "
                 "its own; delays are not propagated along an aircraft's rotation"]
                + ([] if assessed else
                   ["no crew were checked here, so this establishes nothing "
                    "about FDP, re-crewing or legality"]),
            )

        if kind == "cancellation":
            if not flight_ids:
                return envelope("cancellation needs flight_ids", {"error": "flight_ids required"})
            d = self.e.cancellation_cost(flight_ids)
            if "error" in d:
                return envelope(d["error"], d)
            return envelope(
                f"{d['leg_count']} legs, {d['passengers']} passengers, {inr(d['cost_inr'])}. "
                "Cancellation is priced per leg.",
                d,
                [
                    claim("number", f"cancelling {d['leg_count']} legs costs {inr(d['cost_inr'])}",
                          d["cost_inr"], ["costs.json cancellation_per_flight"],
                          short=inr(d["cost_inr"])),
                    claim("number", f"{d['passengers']} passengers affected", d["passengers"],
                          ["flights.json seats"], short=str(d["passengers"])),
                ],
            )

        return envelope(
            f"Unknown kind '{kind}'.", {"error": "kind must be delay, closure or cancellation"}
        )

    # ==================================================================
    # 7. resolve_cover -- one vacancy or several
    # ==================================================================
    def resolve_cover(
        self,
        pairing_id: str | None = None,
        role: str | None = None,
        vacated_by: str | None = None,
        from_date: str | None = None,
        exclude_crew: list[str] | None = None,
        vacancies: list[dict] | None = None,
        limit: int | None = 8,
    ) -> dict:
        """`exclude_crew` is how "what if the reserve is sick too?" is answered:
        the pool changes and everything is re-ranked, re-priced and re-checked.
        Reading the next row off a previous ranking would give a different, and
        wrong, answer."""
        if vacancies:
            d = self.e.resolve_multiple(vacancies, exclude_crew)
            if "error" in d:
                return envelope(d["error"], d)
            cl = [
                claim("number", f"the cheapest joint plan costs {inr(d['total_cost_inr'])}",
                      d["total_cost_inr"], ["costs.json"],
                      short=inr(d["total_cost_inr"])),
                claim("number", f"{d['plan_count']} legal joint plans were enumerated",
                      d["plan_count"], ["exhaustive search"],
                      short=str(d["plan_count"])),
            ]
            for i, v in enumerate(vacancies):
                key = v.get("label") or f"assign_{i+1}"
                a = d[key]
                cl.append(
                    claim("verdict", f"{v['pairing_id']}: {a['action']} at {inr(a['cost_inr'])}",
                          a, ALL_RULES + ["costs.json"],
                          short=f"{a['action']} at {inr(a['cost_inr'])}")
                )
            tie = ""
            if d["tie_count"] > 1:
                cl.append(
                    claim("number", f"{d['tie_count']} plans tie at {inr(d['total_cost_inr'])}",
                          d["tie_count"], ["exhaustive search"],
                          short=str(d["tie_count"]))
                )
                tie = (
                    f" {d['tie_count']} plans tie at that cost, so cost does not "
                    "separate them and the choice is the controller's -- it turns "
                    "on reachability."
                )
            return envelope(
                f"{d['plan_count']} joint plans; cheapest is {inr(d['total_cost_inr'])}.{tie}",
                d,
                cl,
                ["one person cannot cover two pairings; plans that reuse a crew member are excluded"],
            )

        if not pairing_id:
            return envelope("resolve_cover needs pairing_id, or a vacancies list",
                            {"error": "pairing_id required"})
        # Ranked in full, then trimmed here. Asking the engine to trim made the
        # tie count a function of how many rows were on screen: with limit=2 the
        # claim said "2 legal options tie", with limit=1 the tie disappeared,
        # and nine candidates were actually at that price. A presentation limit
        # must not change an operational statement.
        d = self.e.resolve_cover(pairing_id, role, vacated_by, from_date,
                                 exclude_crew, limit=None)
        if "error" in d:
            return envelope(d["error"], d)
        rec = d["recommended"]
        ranked = d["options"]
        if limit is not None and limit < len(ranked):
            d = dict(d, options=ranked[:limit], options_truncated=True)
        cl = [
            claim("number", f"{d['legal_candidate_count']} candidates are legal",
                  d["legal_candidate_count"], ALL_RULES,
                  short=str(d["legal_candidate_count"])),
            claim("number", f"{d['excluded_count']} candidates are excluded "
                  f"({d['exclusions_orientation']})", d["excluded_count"], ALL_RULES,
                  short=str(d["excluded_count"])),
        ]
        if rec:
            cl.append(
                claim("verdict", f"the cheapest legal option is {rec['action']} at {inr(rec['cost_inr'])}",
                      rec, ALL_RULES + ["costs.json"],
                      short=f"{rec['action']} at {inr(rec['cost_inr'])}")
            )
            # How many share the winning price. Without this the model counts
            # the rows itself and gets it slightly wrong -- "three other
            # candidates are tied" when two others were -- which is a figure
            # the gate cannot catch, because every number in the sentence is
            # real. Counting is the engine's job, so the engine counts.
            tied = [o for o in ranked
                    if o.get("crew_id") and o["cost_inr"] == rec["cost_inr"]]
            if len(tied) > 1:
                cl.append(
                    claim("number",
                          f"{len(tied)} legal options tie at {inr(rec['cost_inr'])}, "
                          f"so cost does not separate them",
                          len(tied), ["costs.json"], short=str(len(tied)))
                )
        for o in d["options"]:
            cl.append(
                claim("number", f"{o['action']} costs {inr(o['cost_inr'])}", o["cost_inr"],
                      ["costs.json"], short=inr(o["cost_inr"]))
            )
        return envelope(
            f"{d['legal_candidate_count']} legal candidates, {d['excluded_count']} excluded "
            f"({d['exclusions_orientation']}). Ranked by cost, then crew id; "
            "cancellation is always last.",
            d,
            cl,
            ["excluded candidates carry the rule that stopped them; they are in data.exclusions"],
        )

    # ==================================================================
    # earliest_next_report
    # ==================================================================
    def earliest_next_report(self, release_utc: str) -> dict:
        """When may someone report again after being released?

        Collapsing the tool surface from seventeen to nine dropped this one,
        and the measured replay caught it: Q23 asks exactly this and the model
        had nothing to call, so it asked the controller for a clarification it
        did not need. Added back on evidence.
        """
        try:
            d = self.e.earliest_next_report(release_utc)
        except ValueError:
            return envelope(
                f"Could not read '{release_utc}' as a UTC time. "
                "Use YYYY-MM-DDTHH:MM:SSZ.",
                {"error": "bad timestamp"},
            )
        return envelope(
            f"Released {d['released_utc']}, earliest next report "
            f"{d['earliest_report_utc']} after {d['min_rest_hours']}h rest.",
            d,
            [
                claim(
                    "number",
                    f"earliest next report is {d['earliest_report_utc']}",
                    d["earliest_report_utc"],
                    ["RULE-REST-04"],
                    short=d["earliest_report_utc"],
                )
            ],
            ["this is the rest minimum alone; a specific duty still needs check_assignment"],
        )

    # ==================================================================
    # 8. draft_notification
    # ==================================================================
    def draft_notification(
        self, crew_id: str, pairing_id: str, from_date: str | None = None
    ) -> dict:
        d = self.e.draft_notification(crew_id, pairing_id, from_date)
        if "error" in d:
            return envelope(d["error"], d)
        return envelope(
            f"Callout draft for {crew_id} on {pairing_id}. Every time, place and "
            "flight number comes from the roster.",
            d,
            [claim("verdict", "callout notification drafted from roster records", d["text"],
                   ["rosters.json", "flights.json"])],
            ["this is a draft; it does not check legality -- use check_assignment first"],
        )

    # ==================================================================
    # 9. validate -- the explicit check for a claim the model wants to make
    # ==================================================================
    def validate(self, claim_kind: str, **kw) -> dict:
        """Turn a hypothesis into a verdict, using the same engine code the
        other tools use. Nothing is re-implemented here; this exists so that a
        statement the model wants to make has an obvious route to being checked
        rather than asserted.
        """
        if claim_kind == "assignment_legal":
            r = self.check_assignment(
                kw["crew_id"], kw["pairing_id"], kw.get("from_date"), kw.get("positioned", False)
            )
            if "error" in r["data"]:
                return r
            legal = r["data"]["rules"]["legal"]
            return envelope(
                f"Claim '{kw['crew_id']} can legally cover {kw['pairing_id']}' is "
                + ("CONFIRMED." if legal else "REFUTED."),
                {"claim": kw, "verdict": legal, "evidence": r["data"]["rules"]},
                [claim("legality",
                       f"{kw['crew_id']} covering {kw['pairing_id']} is "
                       + ("legal" if legal else "illegal: " + "; ".join(r["data"]["rules"]["issues"])),
                       legal, ALL_RULES,
                       short=("legal" if legal
                              else "; ".join(r["data"]["rules"]["issues"])))],
            )

        if claim_kind == "crew_qualified":
            f = self.e.rules.check_rating(kw["crew_id"], kw["aircraft_type"])
            ok = f is None
            return envelope(
                f"Claim '{kw['crew_id']} is rated on {kw['aircraft_type']}' is "
                + ("CONFIRMED." if ok else "REFUTED."),
                {"claim": kw, "verdict": ok, "evidence": None if ok else f.to_dict()},
                [claim("verdict",
                       f"{kw['crew_id']} is "
                       + ("rated" if ok else "not rated")
                       + f" on {kw['aircraft_type']}", ok, ["RULE-QUAL-05", "crew.json"],
                       short=("rated" if ok else "not rated") + f" on {kw['aircraft_type']}")],
            )

        if claim_kind == "cheapest_option":
            r = self.e.resolve_cover(
                kw["pairing_id"], kw.get("role"), kw.get("vacated_by"),
                kw.get("from_date"), kw.get("exclude_crew"),
            )
            if "error" in r:
                return envelope(r["error"], r)
            rec = r["recommended"]
            ok = bool(rec) and rec.get("crew_id") == kw.get("crew_id")
            return envelope(
                f"Claim '{kw.get('crew_id')} is the cheapest legal cover for "
                f"{kw['pairing_id']}' is " + ("CONFIRMED." if ok else "REFUTED."),
                {"claim": kw, "verdict": ok, "actual_cheapest": rec},
                [claim("verdict",
                       f"the cheapest legal cover for {kw['pairing_id']} is {rec['action']}"
                       if rec else "there is no legal cover", rec, ALL_RULES + ["costs.json"],
                       short=(rec["action"] if rec else "no legal cover"))],
            )

        return envelope(
            f"Unknown claim kind '{claim_kind}'.",
            {"error": "claim_kind must be assignment_legal, crew_qualified or cheapest_option"},
        )


# ----------------------------------------------------------------------
# OpenAI-compatible tool schemas
# ----------------------------------------------------------------------
def _s(t, desc, **kw):
    return {"type": t, "description": desc, **kw}


SCHEMAS: list[dict] = [
    {
        "name": "lookup",
        "description": (
            "Look up schedule or crew records. entity is one of flights, crew, "
            "reserves, certifications, pairings, risk, stations. on_date is "
            "optional for flights, so a schedule-wide question is one call."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "entity": _s("string", "flights|crew|reserves|certifications|pairings|risk|stations"),
                "on_date": _s("string", "YYYY-MM-DD"),
                "dep": _s("string", "departure station, or the origin for entity=stations"),
                "arr": _s("string", "arrival station"),
                "flight_no": _s("string", "e.g. DX412"),
                "aircraft": _s("string", "a tail (VT-DXA) or a fleet type (A320, ATR72); both filter"),
                "crew_id": _s("string", "e.g. C-1042"),
                "pairing_id": _s("string", "e.g. P-2291"),
                "rank": _s("string", "Captain|First Officer|Senior Cabin Crew|Cabin Crew"),
                "base": _s("string", "e.g. BLR"),
                "rating": _s("string", "aircraft type, e.g. A320"),
                "min_duty_hours_7d": _s("number", "only with on_date; finds crew near the 60h limit"),
                "within_days": _s("integer", "certifications expiring within N days of on_date"),
                "longest_block": _s("boolean", "return the longest block time in the matched set"),
            },
            "required": ["entity"],
        },
    },
    {
        "name": "crew_profile",
        "description": (
            "One crew member: rank, base, ratings, reserve window, this week's "
            "pairings, 7d duty and headroom, 28d block, certifications, risk."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "crew_id": _s("string", "e.g. C-1042"),
                "on_date": _s("string", "window end date for the rolling totals"),
            },
            "required": ["crew_id"],
        },
    },
    {
        "name": "trace_disruption",
        "description": (
            "Which flights lose a crew member when someone drops out, per day, "
            "with passenger counts. Does not rank candidates or compute cost."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "crew_id": _s("string", "the crew member who is unavailable"),
                "pairing_id": _s("string", "optional; inferred from the crew member"),
                "from_date": _s("string", "YYYY-MM-DD, if only part of the pairing is affected"),
            },
            "required": ["crew_id"],
        },
    },
    {
        "name": "check_assignment",
        "description": (
            "Can this person cover this pairing? Returns two separate verdicts: "
            "`callable` (the reserve on-call window) and `rules` (all seven "
            "rules). Report the half that was asked."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "crew_id": _s("string", ""),
                "pairing_id": _s("string", ""),
                "from_date": _s("string", "cover only from this date onwards"),
                "positioned": _s("boolean", "a deadhead has been arranged, satisfying RULE-BASE-07"),
            },
            "required": ["crew_id", "pairing_id"],
        },
    },
    {
        "name": "duty_timeline",
        "description": (
            "The crew member's merged week with the proposed cover inserted, the "
            "rest gap before each duty, and any breach marked. Use this to show "
            "why someone qualified, based and free is still illegal."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "crew_id": _s("string", ""),
                "pairing_id": _s("string", "optional proposed cover"),
                "from_date": _s("string", ""),
            },
            "required": ["crew_id"],
        },
    },
    {
        "name": "simulate_disruption",
        "description": (
            "Simulate a delay, a station closure or a cancellation, with the "
            "recovery assessment. For a delay, `mode` must be stated: "
            "'technical' holds report and grows the duty; 'positioning' shifts "
            "both edges and leaves FDP unchanged."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "kind": _s("string", "delay|closure|cancellation"),
                "aircraft": _s("string", "tail, for a delay"),
                "station": _s("string", "for a closure"),
                "on_date": _s("string", "YYYY-MM-DD"),
                "delay_hours": _s("number", "for a delay"),
                "mode": _s("string", "technical|positioning"),
                "start_utc": _s("string", "closure start, HH:MM"),
                "end_utc": _s("string", "closure end, HH:MM"),
                "flight_ids": {"type": "array", "items": {"type": "string"},
                               "description": "for a cancellation"},
            },
            "required": ["kind"],
        },
    },
    {
        "name": "resolve_cover",
        "description": (
            "Enumerate every candidate, simulate legality for each, cost and "
            "rank them, with cancellation always last. Pass `vacancies` for a "
            "joint plan across several pairings. Pass `exclude_crew` for "
            "'what if they are unavailable too' -- it re-ranks from scratch."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pairing_id": _s("string", ""),
                "role": _s("string", "the role to fill; inferred from vacated_by"),
                "vacated_by": _s("string", "the crew member who dropped out"),
                "from_date": _s("string", "cover only from this date onwards"),
                "exclude_crew": {"type": "array", "items": {"type": "string"},
                                 "description": "crew who are also unavailable"},
                "vacancies": {
                    "type": "array",
                    "description": "several vacancies to solve jointly",
                    "items": {
                        "type": "object",
                        "properties": {
                            "pairing_id": {"type": "string"},
                            "role": {"type": "string"},
                            "vacated_by": {"type": "string"},
                            "from_date": {"type": "string"},
                            "label": {"type": "string"},
                        },
                    },
                },
                "limit": _s("integer", "how many ranked options to return (default 8)"),
            },
            "required": [],
        },
    },
    {
        "name": "earliest_next_report",
        "description": (
            "Given a release time, the earliest a crew member may report for "
            "their next duty under the minimum-rest rule."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "release_utc": _s("string", "YYYY-MM-DDTHH:MM:SSZ, when they were released"),
            },
            "required": ["release_utc"],
        },
    },
    {
        "name": "draft_notification",
        "description": "Draft the callout message to a crew member, from roster records.",
        "parameters": {
            "type": "object",
            "properties": {
                "crew_id": _s("string", ""),
                "pairing_id": _s("string", ""),
                "from_date": _s("string", ""),
            },
            "required": ["crew_id", "pairing_id"],
        },
    },
    {
        "name": "validate",
        "description": (
            "Check a statement you are about to make. Use this whenever you have "
            "a hypothesis about legality, qualification or which option is "
            "cheapest, before putting it in an answer."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "claim_kind": _s("string", "assignment_legal|crew_qualified|cheapest_option"),
                "crew_id": _s("string", ""),
                "pairing_id": _s("string", ""),
                "aircraft_type": _s("string", "for crew_qualified, e.g. ATR72"),
                "from_date": _s("string", ""),
                "role": _s("string", ""),
                "vacated_by": _s("string", ""),
                "positioned": _s("boolean", ""),
                "exclude_crew": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["claim_kind"],
        },
    },
]

OPENAI_TOOLS = [{"type": "function", "function": s} for s in SCHEMAS]


#: Values that mean "not supplied". Some models fill in every property of a
#: schema rather than only the ones they mean, so a call arrives with
#: on_date="" and within_days=0 alongside the one argument that matters. Taking
#: those literally produced the worst routing failures we measured: an empty
#: on_date tripped the "needs a date" error and the model asked the controller
#: for a date it already had.
_EMPTY = ("", None, [], {})


def clean_args(args: dict) -> dict:
    """Drop parameters the model filled in but did not mean."""
    out = {}
    for k, v in (args or {}).items():
        if v in _EMPTY:
            continue
        # 0 is never a meaningful threshold or window for these two
        if k in ("min_duty_hours_7d", "within_days") and not v:
            continue
        out[k] = v
    return out


def dispatch(tools: Tools, name: str, args: dict) -> dict:
    fn: Callable | None = {
        "lookup": tools.lookup,
        "crew_profile": tools.crew_profile,
        "trace_disruption": tools.trace_disruption,
        "check_assignment": tools.check_assignment,
        "duty_timeline": tools.duty_timeline,
        "simulate_disruption": tools.simulate_disruption,
        "resolve_cover": tools.resolve_cover,
        "earliest_next_report": tools.earliest_next_report,
        "draft_notification": tools.draft_notification,
        "validate": tools.validate,
    }.get(name)
    if fn is None:
        return envelope(f"No such tool '{name}'.", {"error": "unknown tool"})
    try:
        return fn(**clean_args(args))
    except TypeError as exc:
        return envelope(f"Bad arguments for {name}: {exc}", {"error": str(exc)})
    except KeyError as exc:
        # A required field the schema did not insist on, or an id that does not
        # exist. This has to come back as a result rather than an exception:
        # the loop calls dispatch unguarded, so anything raised here kills the
        # whole turn instead of giving the model something it can correct.
        return envelope(
            f"{name} could not run: {exc} is missing or unknown. Check the id, "
            f"or supply that field, then call {name} again.",
            {"error": f"missing or unknown {exc}"},
        )
    except Exception as exc:  # pragma: no cover - a tool bug, not a model bug
        return envelope(
            f"{name} failed: {type(exc).__name__}: {exc}. Nothing was computed, "
            f"so do not state a figure from this call.",
            {"error": f"{type(exc).__name__}: {exc}"},
        )
