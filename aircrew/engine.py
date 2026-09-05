"""Impact tracing and cover resolution.

Everything a controller acts on is computed here. The model never does any of
this arithmetic; it picks which of these functions to run and reads the result.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .data import Dataset, Duty, fmt_date, fmt_utc, parse_clock, parse_date, parse_utc
from .rules import ALL_RULES, Finding, RulesEngine

POSITIONING_BUFFER_MIN = 60  # report is first departure minus 60 min (rules.json)


@dataclass
class Candidate:
    crew_id: str
    name: str
    rank: str
    base: str
    source: str  # "reserve" | "day_off" | "line"
    legal: bool
    breaches: list[Finding]
    cost_inr: int
    delay_hours: float
    positioned: bool
    duties: list[Duty]
    rules_checked: list[str] = field(default_factory=lambda: list(ALL_RULES))
    action: str = ""
    rank_pos: int | None = None
    positioning: dict | None = None

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "crew_id": self.crew_id,
            "name": self.name,
            "rank": self.rank,
            "base": self.base,
            "source": self.source,
            "legal": self.legal,
            "rules_checked": self.rules_checked,
            "cost_inr": self.cost_inr,
            "delay_hours": self.delay_hours,
            "positioned": self.positioned,
            "positioning": self.positioning,
            "breaches": [b.to_dict() for b in self.breaches],
            "rank": self.rank_pos,
        }


class Engine:
    def __init__(self, ds: Dataset):
        self.ds = ds
        self.rules = RulesEngine(ds)
        self.costs = ds.costs

    # ------------------------------------------------------------------
    # duty construction
    # ------------------------------------------------------------------
    def cover_duties(
        self, pairing_id: str, crew_id: str, from_date: date | None = None
    ) -> list[Duty]:
        """The duty days a candidate would take on if they covered a pairing."""
        out = []
        for d in self.ds.pairing_duties[pairing_id]:
            if from_date and d.on_date < from_date:
                continue
            out.append(
                Duty(
                    crew_id=crew_id,
                    pairing_id=d.pairing_id,
                    on_date=d.on_date,
                    report_utc=d.report_utc,
                    release_utc=d.release_utc,
                    flight_ids=d.flight_ids,
                    aircraft=d.aircraft,
                    proposed=True,
                )
            )
        return out

    def pax_on(self, flight_ids) -> int:
        return sum(self.ds.flight_by_id[f]["seats"] for f in flight_ids)

    # ------------------------------------------------------------------
    # impact: a crew member drops out
    # ------------------------------------------------------------------
    def trace_crew_unavailable(
        self, crew_id: str, pairing_id: str | None = None, from_date: str | None = None
    ) -> dict:
        """Which flights lose a crew member, and what is downstream.

        Deliberately does not rank or price anything: "which flights are
        uncrewed?" must be answerable without triggering a candidate search.
        Passenger counts are per day, because a two-day pairing's day-1 figure
        is the one a controller acts on first.
        """
        pairings = self.ds.find_pairing(pairing_id=pairing_id, crew_id=crew_id)
        if not pairings:
            return {"error": f"No pairing found for {crew_id}" + (f" / {pairing_id}" if pairing_id else "")}
        p = pairings[0]
        start = parse_date(from_date) if from_date else None
        days = [d for d in p["days"] if not start or parse_date(d["date"]) >= start]
        if not days:
            days = p["days"]

        role = self.ds.role_on_pairing(p["pairing_id"], crew_id)
        by_day = []
        for i, d in enumerate(days):
            by_day.append(
                {
                    "date": d["date"],
                    "flights": list(d["flights"]),
                    "passengers": self.pax_on(d["flights"]),
                    "report_utc": d["report_utc"],
                    "release_utc": d["release_utc"],
                }
            )
        return {
            "crew_id": crew_id,
            "name": self.ds.crew_by_id[crew_id]["name"],
            "role_on_pairing": role,
            "pairing_id": p["pairing_id"],
            "aircraft": p["aircraft"],
            "day1": by_day[0]["flights"] if by_day else [],
            "day2_also_at_risk": by_day[1]["flights"] if len(by_day) > 1 else [],
            "passengers_day1": by_day[0]["passengers"] if by_day else 0,
            "by_day": by_day,
            "total_flights": sum(len(d["flights"]) for d in by_day),
            "cancellation_cost_if_all_cancelled": len(
                [f for d in by_day for f in d["flights"]]
            )
            * self.costs["cancellation_per_flight"],
        }

    # ------------------------------------------------------------------
    # impact: station closure
    # ------------------------------------------------------------------
    def station_closure_impact(
        self, station: str, on_date: str, start_utc: str, end_utc: str
    ) -> dict:
        """Flights touching a closed station in [start, end).

        Half-open, and arrivals count as well as departures. Minimum delay runs
        to reopen plus 30 minutes.
        """
        d = parse_date(on_date)
        sh, sm = parse_clock(start_utc)
        eh, em = parse_clock(end_utc)
        win_start = datetime(d.year, d.month, d.day, sh, sm)
        win_end = datetime(d.year, d.month, d.day, eh, em)
        reopen_plus = win_end + timedelta(minutes=30)

        affected = []
        for f in self.ds.flights:
            if f["date"] != on_date:
                continue
            hits = []
            if f["dep_station"] == station and win_start <= parse_utc(f["dep_utc"]) < win_end:
                hits.append("departure")
            if f["arr_station"] == station and win_start <= parse_utc(f["arr_utc"]) < win_end:
                hits.append("arrival")
            if not hits:
                continue
            ref = parse_utc(f["dep_utc"]) if "departure" in hits else parse_utc(f["arr_utc"])
            min_delay = (reopen_plus - ref).total_seconds() / 3600.0
            affected.append(
                {
                    "flight_id": f["flight_id"],
                    "flight_no": f["flight_no"],
                    "dep_station": f["dep_station"],
                    "arr_station": f["arr_station"],
                    "dep_utc": f["dep_utc"],
                    "arr_utc": f["arr_utc"],
                    "affected_as": hits,
                    "min_delay_hours": round(min_delay, 2),
                    "passengers": f["seats"],
                }
            )
        return {
            "station": station,
            "date": on_date,
            "window_utc": f"{start_utc}-{end_utc}Z (half-open)",
            "reopen_plus_30": reopen_plus.strftime("%H:%MZ"),
            "affected_flight_ids": [a["flight_id"] for a in affected],
            "affected": affected,
            "passengers_at_risk": sum(a["passengers"] for a in affected),
        }

    # ------------------------------------------------------------------
    # impact: delay
    # ------------------------------------------------------------------
    def delay_impact(
        self,
        aircraft: str,
        on_date: str,
        delay_hours: float,
        mode: str = "technical",
    ) -> dict:
        """Two conventions, and the caller must say which.

        `technical`: report is held, release slips, so the duty grows and FDP
        can breach. `positioning`: both edges move, so FDP is unchanged and only
        the departure moves.
        """
        if mode not in ("technical", "positioning"):
            return {"error": "mode must be 'technical' or 'positioning'"}
        pairings = self.ds.find_pairing(aircraft=aircraft, on_date=on_date)
        if not pairings:
            return {"error": f"No pairing for {aircraft} on {on_date}"}
        p = pairings[0]
        day = next(d for d in p["days"] if d["date"] == on_date)
        base = Duty(
            crew_id="",
            pairing_id=p["pairing_id"],
            on_date=parse_date(on_date),
            report_utc=parse_utc(day["report_utc"]),
            release_utc=parse_utc(day["release_utc"]),
            flight_ids=tuple(day["flights"]),
            aircraft=p["aircraft"],
        )
        shifted = base.shifted(delay_hours, hold_report=(mode == "technical"))
        limit = self.rules.fdp_limit(shifted.sectors)
        after = round(shifted.fdp_hours, 2)
        breach = after > limit + 1e-6
        return {
            "aircraft": aircraft,
            "date": on_date,
            "pairing_id": p["pairing_id"],
            "mode": mode,
            "delay_hours": delay_hours,
            "sectors": shifted.sectors,
            "fdp_before": round(base.fdp_hours, 2),
            "fdp_after_delay": after,
            "fdp_limit": limit,
            "breach": breach,
            "new_report_utc": fmt_utc(shifted.report_utc),
            "new_release_utc": fmt_utc(shifted.release_utc),
            "crew": [
                {"crew_id": m["crew_id"], "role": m["role"]} for m in p["crew"]
            ],
            "delay_cost_inr": int(round(delay_hours * self.costs["delay_cost_per_duty_hour"])),
        }

    # ------------------------------------------------------------------
    # impact: cancellation
    # ------------------------------------------------------------------
    def cancellation_cost(self, flight_ids: list[str]) -> dict:
        """Per leg, always. The commonest wrong number on this dataset is a
        per-day passenger count read as a whole-pairing total."""
        legs = []
        for fid in flight_ids:
            f = self.ds.flight_by_id.get(fid)
            if not f:
                return {"error": f"Unknown flight {fid}"}
            legs.append(
                {
                    "flight_id": fid,
                    "flight_no": f["flight_no"],
                    "passengers": f["seats"],
                    "cost_inr": self.costs["cancellation_per_flight"],
                }
            )
        return {
            "legs": legs,
            "leg_count": len(legs),
            "passengers": sum(l["passengers"] for l in legs),
            "cost_inr": len(legs) * self.costs["cancellation_per_flight"],
            "basis": "cancellation_per_flight x number of legs (costs.json)",
        }

    def earliest_next_report(self, release_utc: str) -> dict:
        rel = parse_utc(release_utc)
        nxt = rel + timedelta(hours=self.rules.min_rest)
        return {
            "released_utc": fmt_utc(rel),
            "min_rest_hours": self.rules.min_rest,
            "earliest_report_utc": fmt_utc(nxt),
            "rule": "RULE-REST-04",
            "rule_text": self.ds.rule_text["RULE-REST-04"],
        }

    # ------------------------------------------------------------------
    # duty timeline: the panel that makes "free but illegal" legible
    # ------------------------------------------------------------------
    def duty_timeline(
        self, crew_id: str, proposed: list[Duty] | None = None
    ) -> dict:
        """The merged week with any proposed cover inserted, the rest gap
        between each pair of duties, and the breach marked."""
        proposed = proposed or []
        merged = self.rules.week(crew_id, proposed)
        proposed_keys = {(d.pairing_id, d.on_date) for d in proposed}
        rows = []
        for i, d in enumerate(merged):
            gap = None
            gap_ok = None
            if i > 0:
                gap = round(
                    (d.report_utc - merged[i - 1].release_utc).total_seconds() / 3600.0, 2
                )
                gap_ok = gap >= self.rules.min_rest - 1e-6
            rows.append(
                {
                    "date": fmt_date(d.on_date),
                    "pairing_id": d.pairing_id,
                    "aircraft": d.aircraft,
                    "report_utc": fmt_utc(d.report_utc),
                    "release_utc": fmt_utc(d.release_utc),
                    "sectors": d.sectors,
                    "flights": list(d.flight_ids),
                    "fdp_hours": round(d.fdp_hours, 2),
                    "fdp_limit": self.rules.fdp_limit(d.sectors),
                    "proposed": (d.pairing_id, d.on_date) in proposed_keys,
                    "rest_before_hours": gap,
                    "rest_before_ok": gap_ok,
                    # never show a human a negative rest figure
                    "overlaps_previous_by_hours": round(abs(gap), 2)
                    if gap is not None and gap < 0
                    else None,
                }
            )
        breaches = self.rules.check_duties(crew_id, proposed) if proposed else []
        return {
            "crew_id": crew_id,
            "name": self.ds.crew_by_id[crew_id]["name"],
            "min_rest_hours": self.rules.min_rest,
            "duties": rows,
            "breaches": [b.to_dict() for b in breaches],
            "legal": not breaches,
        }

    # ------------------------------------------------------------------
    # assignment check: two verdicts, not a flag
    # ------------------------------------------------------------------
    def check_assignment(
        self,
        crew_id: str,
        pairing_id: str,
        from_date: str | None = None,
        positioned: bool = False,
    ) -> dict:
        """Returns both `callable` and `rules`.

        "Does this breach a rule?" and "can we call this person out?" are
        different questions with different answers, and the dataset grades both.
        The caller reports the half that was asked.
        """
        if crew_id not in self.ds.crew_by_id:
            return {"error": f"Unknown crew {crew_id}"}
        if pairing_id not in self.ds.pairing_by_id:
            return {"error": f"Unknown pairing {pairing_id}"}
        duties = self.cover_duties(
            pairing_id, crew_id, parse_date(from_date) if from_date else None
        )
        breaches = self.rules.check_duties(crew_id, duties, positioned=positioned)
        window_issue = self.rules.callable_now(crew_id, duties)
        crew = self.ds.crew_by_id[crew_id]
        # "Can they cover it if positioned?" is also a question about what
        # positioning costs the departure, and the answer is useless without
        # it. Compute it here rather than making the caller run the resolver.
        pos = None
        dep = self.ds.flight_by_id[duties[0].flight_ids[0]]["dep_station"]
        if crew["base"] != dep:
            pos = self.positioning(crew["base"], duties[0])
            if pos:
                pos["consequence"] = (
                    f"Deadhead positioning on {pos['deadhead_flight_no']} "
                    f"(arr {pos['arrives_utc'][11:16]}Z) delays the first "
                    f"departure by ~{pos['delay_hours']:g}h; RULE-BASE-07 "
                    "deadhead cost applies."
                )
        return {
            "crew_id": crew_id,
            "name": crew["name"],
            "rank": crew["rank"],
            "base": crew["base"],
            "pairing_id": pairing_id,
            "days_covered": [fmt_date(d.on_date) for d in duties],
            "positioning": pos,
            "callable": {
                "is_reserve": crew_id in self.ds.reserve_by_id,
                "callable": window_issue is None,
                "reason": window_issue.render() if window_issue else None,
                "reachability_minutes": crew["reachability_minutes"],
            },
            "rules": {
                "legal": not breaches,
                "rules_checked": list(ALL_RULES),
                "breaches": [b.to_dict() for b in breaches],
                "issues": [b.render() for b in breaches],
            },
            "timeline": self.duty_timeline(crew_id, duties),
        }

    # ------------------------------------------------------------------
    # costing
    # ------------------------------------------------------------------
    def cost_for(
        self, crew_id: str, source: str, positioned: bool, delay_hours: float = 0.0
    ) -> int:
        """Callout, plus the deadhead fee and the delay it causes.

        Positioning is never free even when it is legal: the deadhead fee and
        the delay the late report imposes on the first departure are both real
        and both are charged, which is why an out-of-base reserve can rank
        below an in-base day-off callout.
        """
        pilot = self.ds.is_pilot(crew_id)
        if source == "reserve":
            c = self.costs["reserve_callout_pilot"] if pilot else self.costs["reserve_callout_cabin"]
        else:
            c = self.costs["dayoff_callout_pilot"] if pilot else self.costs["dayoff_callout_cabin"]
        if positioned:
            c += self.costs["deadhead_positioning"]
        c += int(round(delay_hours * self.costs["delay_cost_per_duty_hour"]))
        return c

    def source_of(self, crew_id: str, on_date: date) -> str:
        res = self.ds.reserve_by_id.get(crew_id)
        if res and fmt_date(on_date) in res["dates"]:
            return "reserve"
        return "day_off"

    # ------------------------------------------------------------------
    # resolution
    # ------------------------------------------------------------------
    def resolve_cover(
        self,
        pairing_id: str,
        role: str | None = None,
        vacated_by: str | None = None,
        from_date: str | None = None,
        exclude_crew: list[str] | None = None,
        allow_positioning: bool = True,
        limit: int | None = None,
    ) -> dict:
        """Enumerate, simulate, cost, rank. Cancellation is always last.

        `limit` truncates the ranked list for presentation only -- 43 options is
        not a decision aid. It is applied after ranking, so the top N are the
        true top N, and the untruncated count is reported alongside.

        `exclude_crew` is how a follow-up like "what if the reserve is sick
        too?" is answered: the pool changes and everything is recomputed. It is
        an engine parameter precisely so the answer cannot be produced by
        reading the next row off a previous ranking.
        """
        if pairing_id not in self.ds.pairing_by_id:
            return {"error": f"Unknown pairing {pairing_id}"}
        p = self.ds.pairing_by_id[pairing_id]
        if role is None and vacated_by:
            role = self.ds.role_on_pairing(pairing_id, vacated_by)
        if role is None:
            return {"error": "Need a role, or the crew member who dropped out"}
        incumbents = [m["crew_id"] for m in p["crew"] if m["role"] == role]
        if vacated_by is None and len(incumbents) == 1:
            # "The VT-DXA captain is sick" names a person without naming them,
            # so derive it -- otherwise the one crew member who certainly
            # cannot cover the vacancy is offered as its cheapest solution.
            # Only when the role has exactly one holder: with three Cabin Crew
            # on a pairing, guessing which of them dropped out would silently
            # drop a legal candidate from the ranking.
            vacated_by = incumbents[0]

        start = parse_date(from_date) if from_date else None
        template = self.cover_duties(pairing_id, "", start)
        if not template:
            return {"error": f"No duty days for {pairing_id} from {from_date}"}
        first = template[0]
        dep = self.ds.flight_by_id[first.flight_ids[0]]["dep_station"]
        excluded_ids = set(exclude_crew or [])
        if vacated_by:
            excluded_ids.add(vacated_by)

        candidates: list[Candidate] = []
        exclusions: list[dict] = []

        # Enumeration follows duty_clocks.json record order; the answer keys'
        # exclusion lists are in that order, not crew.json's export sort.
        for crew_id in self.ds.enumeration_order:
            crew = self.ds.crew_by_id[crew_id]
            if crew_id in excluded_ids:
                continue
            if crew["status"] != "active":
                continue
            # role must match rank exactly: Senior Cabin Crew is not Cabin Crew
            if crew["rank"] != role:
                continue

            duties = self.cover_duties(pairing_id, crew_id, start)
            source = self.source_of(crew_id, first.on_date)
            positioned = crew["base"] != dep
            pos = self.positioning(crew["base"], first) if positioned else None

            if positioned and (not allow_positioning or pos is None):
                exclusions.append(
                    {
                        "crew_id": crew_id,
                        "rule": "RULE-BASE-07",
                        "rule_text": self.ds.rule_text["RULE-BASE-07"],
                        "reason": f"RULE-BASE-07: based {crew['base']}, pairing departs {dep}"
                        + (" (no deadhead available)" if pos is None else ""),
                    }
                )
                continue

            # the on-call window gates selection before any rule is examined,
            # against the report time the candidate actually has to make
            window_issue = self.rules.callable_now(
                crew_id, duties, report_utc=parse_utc(pos["required_report_utc"]) if pos else None
            )
            if window_issue is not None:
                exclusions.append(
                    {
                        "crew_id": crew_id,
                        "rule": "RULE-BASE-07",
                        "rule_text": self.ds.rule_text["RULE-BASE-07"],
                        "reason": window_issue.render(),
                    }
                )
                continue

            breaches = self.rules.check_duties(crew_id, duties, positioned=positioned)
            if breaches:
                # A candidate is excluded on the first rule that stops them, and
                # every finding under that rule is reported. Piling on later
                # rules would tell a controller a rating problem is also a rest
                # problem, which is not a fact about the candidate but an
                # artefact of running all seven checks anyway.
                first_rule = breaches[0].rule
                reasons = [b for b in breaches if b.rule == first_rule]
                # `reason` carries every finding under the first failing rule,
                # joined, and that is what the answer keys grade and what the
                # panel shows. A structured copy of the same findings used to
                # ride along here as `all_breaches`: 13,232 of the 17,445
                # characters this list costs, read by nothing -- not the
                # workspace, not the scoreboard, not the keys. A tool result is
                # context the model pays for on every turn, so it carries what
                # is used and not what might one day be.
                exclusions.append(
                    {
                        "crew_id": crew_id,
                        "rule": first_rule,
                        "rule_text": self.ds.rule_text[first_rule],
                        "reason": "; ".join(b.render() for b in reasons),
                    }
                )
                continue

            delay_hours = pos["delay_hours"] if pos else 0.0

            cand = Candidate(
                crew_id=crew_id,
                name=crew["name"],
                rank=crew["rank"],
                base=crew["base"],
                source=source,
                legal=True,
                breaches=[],
                cost_inr=self.cost_for(crew_id, source, positioned, delay_hours),
                delay_hours=delay_hours,
                positioned=positioned,
                duties=duties,
            )
            cand.positioning = pos
            label = "reserve callout" if source == "reserve" else "day-off callout"
            if pos:
                label += (
                    f" + deadhead from {crew['base']} "
                    f"(first departure delayed ~{delay_hours}h)"
                )
            cand.action = f"Assign {crew['rank']} {crew_id} ({label})"
            candidates.append(cand)

        candidates.sort(key=lambda c: (c.cost_inr, c.crew_id))
        options = []
        for i, c in enumerate(candidates, start=1):
            c.rank_pos = i
            options.append(c.to_dict())

        # cancellation is appended last, with a null crew id and no rules checked
        legs = [f for d in template for f in d.flight_ids]
        options.append(
            {
                "action": f"Cancel all {len(legs)} flights of the pairing",
                "crew_id": None,
                "legal": True,
                "rules_checked": [],
                "cost_inr": len(legs) * self.costs["cancellation_per_flight"],
                "delay_hours": 0.0,
                "rank": len(candidates) + 1,
                "passengers_affected": self.pax_on(legs),
            }
        )

        total_options = len(options)
        if limit is not None:
            options = options[:limit]

        by_rule: dict[str, int] = {}
        for e in exclusions:
            by_rule[e["rule"]] = by_rule.get(e["rule"], 0) + 1

        return {
            "pairing_id": pairing_id,
            "role": role,
            "vacated_by": vacated_by,
            "vacancy_ambiguous": vacated_by is None and len(incumbents) > 1,
            "role_incumbents": incumbents,
            "from_date": from_date,
            "aircraft": p["aircraft"],
            "dep_station": dep,
            "days": [fmt_date(d.on_date) for d in template],
            "uncovered_flights": legs,
            "options": options,
            "option_count": total_options,
            "options_truncated": limit is not None and limit < total_options,
            "recommended": options[0] if options else None,
            "legal_candidate_count": len(candidates),
            "excluded_count": len(exclusions),
            "exclusions": exclusions,
            "exclusions_by_rule": by_rule,
            "exclusions_orientation": self._orientation(by_rule),
            "excluded_by_request": sorted(excluded_ids),
        }

    def _orientation(self, by_rule: dict[str, int]) -> str:
        names = {
            "RULE-REST-04": "rest",
            "RULE-QUAL-05": "aircraft rating",
            "RULE-DUTY-02": "duty hours",
            "RULE-FLT-03": "block hours",
            "RULE-CERT-06": "certification",
            "RULE-FDP-01": "duty period",
            "RULE-BASE-07": "base / on-call window",
        }
        parts = [
            f"{n} {names.get(r, r)}"
            for r, n in sorted(by_rule.items(), key=lambda kv: -kv[1])
        ]
        return ", ".join(parts)

    def positioning(self, from_base: str, first: Duty) -> dict | None:
        """The earliest deadhead that gets a candidate to the departure station,
        and the required report time once they are on it.

        The crew member is available from the next whole hour after the
        deadhead arrives, and report is one hour before departure (rules.json),
        so the first departure moves to arrival-rounded-up plus 60 minutes.
        That is what the answer keys price: DX402 arriving 08:45Z gives a 09:00Z
        report and a 10:00Z departure, which is the 3.0h delay in Q31/S2 and,
        via DX589 arriving 07:45Z, the 6.5h and 6.0h delays in S6.

        The post-positioning report time is also what the reserve on-call
        window is tested against -- 09:00Z, not the rostered 06:00Z.
        """
        to_station = self.ds.flight_by_id[first.flight_ids[0]]["dep_station"]
        best = None
        for f in self.ds.flights:
            if f["dep_station"] != from_base or f["arr_station"] != to_station:
                continue
            if f["date"] != fmt_date(first.on_date):
                continue
            arr = parse_utc(f["arr_utc"])
            if best is None or arr < best[0]:
                best = (arr, f)
        if not best:
            return None
        arr, dh = best
        ready = arr.replace(minute=0, second=0) + timedelta(
            hours=1 if (arr.minute or arr.second) else 0
        )
        new_report = max(ready, first.report_utc)
        new_dep = new_report + timedelta(minutes=POSITIONING_BUFFER_MIN)
        first_dep = parse_utc(self.ds.flight_by_id[first.flight_ids[0]]["dep_utc"])
        delay = max(0.0, (new_dep - first_dep).total_seconds() / 3600.0)
        return {
            "deadhead_flight": dh["flight_id"],
            "deadhead_flight_no": dh["flight_no"],
            "from_base": from_base,
            "to_station": to_station,
            "arrives_utc": dh["arr_utc"],
            "required_report_utc": fmt_utc(new_report),
            "new_first_departure_utc": fmt_utc(new_dep),
            "delay_hours": round(delay, 2),
        }

    # ------------------------------------------------------------------
    # recovery: a delay that breaks the rostered crew's duty
    # ------------------------------------------------------------------
    def reserve_set_cost(self, pairing_id: str) -> dict:
        """What a full replacement crew costs, by the roles the pairing needs."""
        p = self.ds.pairing_by_id[pairing_id]
        rows = []
        total = 0
        for m in p["crew"]:
            pilot = m["role"] in ("Captain", "First Officer")
            c = (
                self.costs["reserve_callout_pilot"]
                if pilot
                else self.costs["reserve_callout_cabin"]
            )
            total += c
            rows.append({"role": m["role"], "cost_inr": c})
        return {"roles": rows, "total_cost_inr": total}

    def delay_recovery(
        self, aircraft: str, on_date: str, delay_hours: float, mode: str = "technical"
    ) -> dict:
        """What to do when a delay pushes the rostered duty past its FDP limit.

        The rostered crew keep the legs they can still legally fly -- the
        longest prefix of the duty that stays inside the FDP limit for that
        many sectors -- and the tail is re-crewed or cancelled. Dropping a
        sector also drops the FDP limit's sector reduction, which is why
        shortening the duty helps twice.
        """
        impact = self.delay_impact(aircraft, on_date, delay_hours, mode)
        if "error" in impact:
            return impact
        if not impact["breach"]:
            return {**impact, "options": [], "note": "No FDP breach; no recovery needed."}

        pairing_id = impact["pairing_id"]
        day = next(
            d for d in self.ds.pairing_by_id[pairing_id]["days"] if d["date"] == on_date
        )
        legs = list(day["flights"])
        report = parse_utc(day["report_utc"])
        if mode != "technical":
            report += timedelta(hours=delay_hours)

        # longest legal prefix: release is the prefix's last arrival + 30 min,
        # shifted by the delay, and the limit relaxes as sectors come off
        # Two figures, both real, and the recommendation turns on the second.
        #  - report held: the duty the crew are actually on now. This is what
        #    breaches, and it is what decides how many legs have to come off.
        #  - report re-timed: once the delay is known before report, the crew
        #    can be held back and the retained legs run at their nominal FDP.
        # The recommended action is precisely to re-time the report, so that is
        # the figure the recommendation quotes.
        keep = 0
        kept_fdp_held = kept_fdp_retimed = kept_limit = 0.0
        for n in range(len(legs), 0, -1):
            arr = parse_utc(self.ds.flight_by_id[legs[n - 1]]["arr_utc"])
            nominal = (arr + timedelta(minutes=30) - report).total_seconds() / 3600.0
            held = nominal + delay_hours
            limit = self.rules.fdp_limit(n)
            if held <= limit + 1e-6:
                keep = n
                kept_fdp_held = round(held, 2)
                kept_fdp_retimed = round(nominal, 2)
                kept_limit = limit
                break
        tail = legs[keep:]
        kept = legs[:keep]
        nos = lambda ids: "\u2013".join(
            [self.ds.flight_by_id[ids[0]]["flight_no"], self.ds.flight_by_id[ids[-1]]["flight_no"]]
        ) if len(ids) > 1 else self.ds.flight_by_id[ids[0]]["flight_no"]

        rs = self.reserve_set_cost(pairing_id)
        roles = [m["role"] for m in self.ds.pairing_by_id[pairing_id]["crew"]]
        abbrev = {"Captain": "CPT", "First Officer": "FO", "Senior Cabin Crew": "SCC", "Cabin Crew": "CC"}
        counts: dict[str, int] = {}
        for r in roles:
            counts[abbrev.get(r, r)] = counts.get(abbrev.get(r, r), 0) + 1
        set_desc = ", ".join(f"{n} {k}" if n > 1 else k for k, n in counts.items())

        cancel_cost = len(tail) * self.costs["cancellation_per_flight"]
        cancel_pax = self.pax_on(tail)
        options = []
        if kept and tail:
            options.append(
                {
                    "rank": 1,
                    "action": (
                        f"Original crew operates {nos(kept)} (delayed); "
                        f"full reserve set ({set_desc}) operates {nos(tail)}"
                    ),
                    "legal": True,
                    "cost_inr": rs["total_cost_inr"],
                    "reasoning": (
                        f"Delayed {keep}-leg duty FDP {kept_fdp_retimed}h vs {kept_limit}h "
                        "limit \u2014 legal. Reserve set covers the last sector "
                        "(callout window and 12h-rest all satisfied)."
                    ),
                    "kept_flights": kept,
                    "recrewed_flights": tail,
                    "kept_fdp_report_retimed": kept_fdp_retimed,
                    "kept_fdp_report_held": kept_fdp_held,
                    "kept_fdp_limit": kept_limit,
                    "reserve_set": rs,
                }
            )
        if tail:
            ratio = cancel_cost / rs["total_cost_inr"] if rs["total_cost_inr"] else 0
            options.append(
                {
                    "rank": len(options) + 1,
                    "action": f"Cancel {nos(tail)}",
                    "legal": True,
                    "cost_inr": cancel_cost,
                    "reasoning": (
                        f"Legal but ~{ratio:.1f}x more expensive than re-crewing "
                        f"one leg; {cancel_pax} passengers stranded."
                    ),
                    "cancelled_flights": tail,
                    "passengers": cancel_pax,
                }
            )
        return {
            **impact,
            "breach_detail": (
                f"RULE-FDP-01: delayed duty runs {impact['fdp_after_delay']}h vs "
                f"{impact['fdp_limit']}h limit ({impact['sectors']} sectors) "
                f"\u2014 the rostered crew cannot legally complete "
                f"{self.ds.flight_by_id[legs[-1]]['flight_no']}."
            ),
            "legs_crew_can_still_fly": kept,
            "legs_needing_recrew": tail,
            "options": options,
            "recommended": options[0] if options else None,
        }

    # ------------------------------------------------------------------
    # recovery: a station closure across every pairing it touches
    # ------------------------------------------------------------------
    def closure_recovery(
        self, station: str, on_date: str, start_utc: str, end_utc: str
    ) -> dict:
        """Per affected flight: how long it must wait, and whether the crew
        rostered on it can still legally operate after waiting that long.

        The delay is technical -- report is already made, release slips -- so
        the duty grows by the full wait.
        """
        impact = self.station_closure_impact(station, on_date, start_utc, end_utc)
        rows = []
        for a in impact["affected"]:
            fid = a["flight_id"]
            pairing = next(
                (
                    p
                    for p in self.ds.pairings
                    for d in p["days"]
                    if d["date"] == on_date and fid in d["flights"]
                ),
                None,
            )
            if pairing is None:
                rows.append({**a, "pairing_id": None, "action": "no rostered pairing"})
                continue
            day = next(d for d in pairing["days"] if d["date"] == on_date)
            duty = Duty(
                crew_id="",
                pairing_id=pairing["pairing_id"],
                on_date=parse_date(on_date),
                report_utc=parse_utc(day["report_utc"]),
                release_utc=parse_utc(day["release_utc"]),
                flight_ids=tuple(day["flights"]),
                aircraft=pairing["aircraft"],
            )
            after = round(
                duty.shifted(a["min_delay_hours"], hold_report=True).fdp_hours, 2
            )
            limit = self.rules.fdp_limit(duty.sectors)
            breaches = after > limit + 1e-6
            rows.append(
                {
                    "flight_id": fid,
                    "pairing_id": pairing["pairing_id"],
                    "min_delay_hours": a["min_delay_hours"],
                    "crew_fdp_after_delay": after,
                    "fdp_limit": limit,
                    "action": (
                        "delay exceeds crew FDP \u2014 re-crew tail legs from "
                        "reserves or cancel"
                        if breaches
                        else "delay (crew legal)"
                    ),
                    "passengers": a["passengers"],
                }
            )
        return {
            **impact,
            "per_flight_assessment": rows,
            "flights_needing_recrew": [
                r["flight_id"] for r in rows if "exceeds" in r["action"]
            ],
        }

    # ------------------------------------------------------------------
    def resolve_multiple(
        self,
        vacancies: list[dict],
        exclude_crew: list[str] | None = None,
    ) -> dict:
        """Joint plan by exhaustive search over each vacancy's legal options.

        The brief says a full optimisation solver is not expected, and the
        search space here is small enough that enumeration is exact. Ties are
        the finding, so they are reported rather than hidden behind the pick.
        """
        per: list[dict] = []
        for v in vacancies:
            r = self.resolve_cover(
                pairing_id=v["pairing_id"],
                role=v.get("role"),
                vacated_by=v.get("vacated_by"),
                from_date=v.get("from_date"),
                exclude_crew=exclude_crew,
            )
            if "error" in r:
                return r
            per.append(r)

        combos = []
        for combo in itertools.product(*[r["options"] for r in per]):
            ids = [o["crew_id"] for o in combo if o["crew_id"]]
            if len(ids) != len(set(ids)):
                continue  # one person cannot cover two pairings at once
            combos.append(
                {
                    "assignments": list(combo),
                    "total_cost_inr": sum(o["cost_inr"] for o in combo),
                }
            )
        # Ties are broken by rank order within each vacancy -- the first strict
        # minimum a controller reading the panels top-down would reach for.
        combos.sort(
            key=lambda c: (
                c["total_cost_inr"],
                tuple(o["rank"] for o in c["assignments"]),
            )
        )
        best = combos[0] if combos else None
        tied = [c for c in combos if best and c["total_cost_inr"] == best["total_cost_inr"]]

        out = {
            "vacancies": vacancies,
            "plan_count": len(combos),
            "optimal": best,
            "total_cost_inr": best["total_cost_inr"] if best else None,
            "tie_count": len(tied),
            "tied_plans": tied[:20],
            "per_vacancy": per,
        }
        for i, v in enumerate(vacancies):
            key = v.get("label") or f"assign_{i+1}"
            out[key] = best["assignments"][i] if best else None
        return out

    # ------------------------------------------------------------------
    def compare_candidates(
        self, pairing_id: str, crew_ids: list[str], from_date: str | None = None
    ) -> dict:
        """Side by side, each one re-simulated. Never a re-read of a ranking."""
        rows = []
        for cid in crew_ids:
            r = self.check_assignment(cid, pairing_id, from_date)
            if "error" in r:
                rows.append({"crew_id": cid, "error": r["error"]})
                continue
            crew = self.ds.crew_by_id[cid]
            duties = self.cover_duties(
                pairing_id, cid, parse_date(from_date) if from_date else None
            )
            dep = self.ds.flight_by_id[duties[0].flight_ids[0]]["dep_station"]
            positioned = crew["base"] != dep
            source = self.source_of(cid, duties[0].on_date)
            rows.append(
                {
                    "crew_id": cid,
                    "name": crew["name"],
                    "rank": crew["rank"],
                    "base": crew["base"],
                    "source": source,
                    "legal": r["rules"]["legal"],
                    "callable": r["callable"]["callable"],
                    "callable_reason": r["callable"]["reason"],
                    "issues": r["rules"]["issues"],
                    "cost_inr": self.cost_for(cid, source, positioned),
                    "reachability_minutes": crew["reachability_minutes"],
                    "risk_score": self.ds.risk_by_id.get(cid, {}).get(
                        "disruption_risk_score"
                    ),
                }
            )
        return {"pairing_id": pairing_id, "candidates": rows}

    # ------------------------------------------------------------------
    def draft_notification(self, crew_id: str, pairing_id: str, from_date: str | None = None) -> dict:
        """Rendered entirely from records. Every time, place and flight number
        in the text comes from the roster, so the draft cannot contain a detail
        the schedule does not."""
        if crew_id not in self.ds.crew_by_id or pairing_id not in self.ds.pairing_by_id:
            return {"error": "Unknown crew or pairing"}
        crew = self.ds.crew_by_id[crew_id]
        duties = self.cover_duties(
            pairing_id, crew_id, parse_date(from_date) if from_date else None
        )
        p = self.ds.pairing_by_id[pairing_id]
        lines = []
        overnight = None
        for i, d in enumerate(duties):
            first_f = self.ds.flight_by_id[d.flight_ids[0]]
            last_f = self.ds.flight_by_id[d.flight_ids[-1]]
            nos = "/".join(self.ds.flight_by_id[f]["flight_no"] for f in d.flight_ids)
            lines.append(
                {
                    "day": i + 1,
                    "date": fmt_date(d.on_date),
                    "report_utc": fmt_utc(d.report_utc),
                    "report_place": f"{first_f['dep_station']} crew room",
                    "flights": nos,
                    "ends_at": last_f["arr_station"],
                }
            )
            if i < len(duties) - 1:
                overnight = last_f["arr_station"]
        return {
            "crew_id": crew_id,
            "name": crew["name"],
            "rank": crew["rank"],
            "pairing_id": pairing_id,
            "aircraft": p["aircraft"],
            "days": lines,
            "overnight_station": overnight,
            "hotel": self.costs["hotel_overnight"] if overnight else None,
            "acknowledgement_deadline_minutes": crew["reachability_minutes"],
            "text": self._notification_text(crew, pairing_id, lines, overnight),
        }

    def _notification_text(self, crew, pairing_id, lines, overnight) -> str:
        out = [
            f"CREW CALLOUT - {crew['crew_id']} ({crew['rank']}, {crew['name']})",
            f"Pairing: {pairing_id}",
        ]
        for l in lines:
            out.append(
                f"Day {l['day']} {l['date']}: report {l['report_utc']} at "
                f"{l['report_place']}; flights {l['flights']}; ends {l['ends_at']}"
            )
        if overnight:
            out.append(f"Overnight {overnight} - hotel arranged.")
        out.append(
            f"Please acknowledge within {crew['reachability_minutes']} minutes "
            "of receipt."
        )
        return "\n".join(out)
