"""The typed query layer: every Tier-1 lookup, one entity at a time.

These are filters over the dataset, not analysis. Anything that simulates a
change or costs an option lives in engine.py.
"""

from __future__ import annotations

from datetime import timedelta

from .data import Dataset, fmt_date, parse_date, parse_utc

ENTITIES = ["flights", "crew", "pairings", "reserves", "certifications", "risk", "stations"]


class Query:
    def __init__(self, ds: Dataset):
        self.ds = ds
        from .rules import RulesEngine

        self.rules = RulesEngine(ds)

    # ------------------------------------------------------------------
    def flights(
        self,
        on_date: str | None = None,
        dep: str | None = None,
        arr: str | None = None,
        flight_no: str | None = None,
        aircraft: str | None = None,
        longest_block: bool = False,
    ) -> dict:
        """The date is optional on purpose: "longest block time in the
        schedule" is one call, not seven."""
        rows = self.ds.flights
        if on_date:
            rows = [f for f in rows if f["date"] == on_date]
        if dep:
            rows = [f for f in rows if f["dep_station"] == dep.upper()]
        if arr:
            rows = [f for f in rows if f["arr_station"] == arr.upper()]
        if flight_no:
            rows = [f for f in rows if f["flight_no"] == flight_no.upper()]
        if aircraft:
            rows = [f for f in rows if f["aircraft"].upper() == aircraft.upper()]

        out = {
            "count": len(rows),
            "flight_numbers": sorted({f["flight_no"] for f in rows}, key=lambda n: rows[[r["flight_no"] for r in rows].index(n)]["dep_utc"]) if rows else [],
            "flights": rows,
        }
        # preserve schedule order for the flight-number list
        seen, ordered = set(), []
        for f in rows:
            if f["flight_no"] not in seen:
                seen.add(f["flight_no"])
                ordered.append(f["flight_no"])
        out["flight_numbers"] = ordered

        if rows:
            by_type: dict[str, int] = {}
            for f in rows:
                by_type[f["aircraft_type"]] = max(
                    by_type.get(f["aircraft_type"], 0), f["seats"]
                )
            out["seats_by_type"] = by_type
            out["most_seats_on_one_leg"] = max(by_type.values())
            # One place formats this, so the tool claim, the scoreboard entry
            # and the panel cannot word the same fact three different ways.
            top = max(by_type.items(), key=lambda kv: kv[1])
            rest = sorted((k, v) for k, v in by_type.items() if k != top[0])
            # Only a comparison. With one fleet type in the result there is
            # nothing to compare it against, and the claim came out as
            # "the most seats at risk is any A320 leg (162 seats), against " --
            # which the model then wrote into an answer.
            if rest:
                out["most_seats_at_risk"] = {
                    "flights": f"any {top[0]} leg ({top[1]} seats)",
                    "vs": ", ".join(f"{k} legs ({v} seats)" for k, v in rest),
                }

        if longest_block and rows:
            mx = max(f["block_hours"] for f in rows)
            out["longest_block"] = {
                "block_hours": mx,
                "flights": [f["flight_no"] for f in rows if f["block_hours"] == mx],
            }
            out["longest_block"]["flights"] = sorted(set(out["longest_block"]["flights"]))
        return out

    # ------------------------------------------------------------------
    def crew_profile(self, crew_id: str, on_date: str | None = None) -> dict:
        """One record with everything a controller asks about a person, so
        "what is their base, rating and headroom" is a single call."""
        c = self.ds.crew_by_id.get(crew_id)
        if not c:
            return {"error": f"Unknown crew {crew_id}"}
        clock = self.ds.clock_by_id[crew_id]
        risk = self.ds.risk_by_id.get(crew_id, {})
        res = self.ds.reserve_by_id.get(crew_id)
        end = parse_date(on_date) if on_date else self.ds.snapshot_utc.date()
        duty7 = self.rules.accrued(crew_id, end, 7, "duty")
        flt28 = self.rules.accrued(crew_id, end, 28, "flight")
        return {
            "crew_id": crew_id,
            "name": c["name"],
            "rank": c["rank"],
            "base": c["base"],
            "ratings": c["ratings"],
            "seniority": c["seniority"],
            "status": c["status"],
            "reachability_minutes": c["reachability_minutes"],
            "as_of": fmt_date(end),
            "duty_hours_7d": duty7,
            "duty_headroom_7d": round(self.rules.max_duty_7d - duty7, 2),
            "duty_limit_7d": self.rules.max_duty_7d,
            "flight_hours_28d": flt28,
            "flight_limit_28d": self.rules.max_flight_28d,
            "snapshot_duty_hours_7d": clock["duty_hours_7d"],
            "snapshot_flight_hours_28d": clock["flight_hours_28d"],
            "last_rest_ended": clock["last_rest_ended"],
            "reserve": {
                "is_reserve": res is not None,
                "dates": res["dates"] if res else [],
                "oncall_window_utc": res["oncall_window_utc"] if res else None,
            },
            "risk": {
                "disruption_risk_score": risk.get("disruption_risk_score"),
                "drivers": risk.get("drivers", []),
            },
            "certifications": self.ds.certs_by_crew.get(crew_id, []),
            "min_rest_hours": self.rules.min_rest,
            "pairings_this_week": self._week_rows(crew_id),
        }

    def _week_rows(self, crew_id: str) -> list[dict]:
        """The rostered week, with the real FDP limit and the real rest gap on
        every row. Nothing on this list is a default: a screen that prints a
        limit nobody computed is the failure the whole design is against."""
        duties = self.ds.duties_for(crew_id)
        rows = []
        for i, d in enumerate(duties):
            gap = None
            if i > 0:
                gap = round(
                    (d.report_utc - duties[i - 1].release_utc).total_seconds() / 3600.0, 2
                )
            rows.append(
                {
                    "pairing_id": d.pairing_id,
                    "date": fmt_date(d.on_date),
                    "aircraft": d.aircraft,
                    "role": d.role,
                    "report_utc": d.report_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "release_utc": d.release_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "sectors": d.sectors,
                    "flights": list(d.flight_ids),
                    "fdp_hours": round(d.fdp_hours, 2),
                    "fdp_limit": self.rules.fdp_limit(d.sectors),
                    "proposed": False,
                    "rest_before_hours": gap,
                    "rest_before_ok": None if gap is None else gap >= self.rules.min_rest - 1e-6,
                    "overlaps_previous_by_hours": round(abs(gap), 2)
                    if gap is not None and gap < 0
                    else None,
                }
            )
        return rows

    # ------------------------------------------------------------------
    def crew(
        self,
        rank: str | None = None,
        base: str | None = None,
        rating: str | None = None,
        on_date: str | None = None,
        min_duty_hours_7d: float | None = None,
        status: str = "active",
    ) -> dict:
        """Returns 7d duty per row when dated, so "who is near the limit" is
        one call rather than a scan plus 150 profile lookups."""
        rows = []
        end = parse_date(on_date) if on_date else None
        for crew_id in self.ds.enumeration_order:
            c = self.ds.crew_by_id[crew_id]
            if status and c["status"] != status:
                continue
            if rank and c["rank"] != rank:
                continue
            if base and c["base"] != base.upper():
                continue
            if rating and rating not in c["ratings"]:
                continue
            row = {
                "crew_id": crew_id,
                "name": c["name"],
                "rank": c["rank"],
                "base": c["base"],
                "ratings": c["ratings"],
            }
            if end:
                d7 = self.rules.accrued(crew_id, end, 7, "duty")
                row["duty_hours_7d"] = d7
                row["duty_headroom_7d"] = round(self.rules.max_duty_7d - d7, 2)
                row["as_of"] = fmt_date(end)
                if min_duty_hours_7d is not None and d7 < min_duty_hours_7d:
                    continue
            elif min_duty_hours_7d is not None:
                return {
                    "error": "min_duty_hours_7d needs on_date: a rolling total "
                    "is only defined against a window end date"
                }
            rows.append(row)
        return {"count": len(rows), "crew": rows}

    # ------------------------------------------------------------------
    def reserves(self, on_date: str, base: str | None = None) -> dict:
        rows = []
        for r in self.ds.reserve_pool:
            if on_date not in r["dates"]:
                continue
            if base and r["base"] != base.upper():
                continue
            c = self.ds.crew_by_id[r["crew_id"]]
            rows.append(
                {
                    "crew_id": r["crew_id"],
                    "name": c["name"],
                    "rank": c["rank"],
                    "base": r["base"],
                    "ratings": c["ratings"],
                    "window": r["oncall_window_utc"],
                    "reachability_minutes": c["reachability_minutes"],
                }
            )
        return {"date": on_date, "base": base, "count": len(rows), "reserves": rows}

    # ------------------------------------------------------------------
    def certifications_expiring(self, from_date: str, within_days: int = 30) -> dict:
        start = parse_date(from_date)
        end = start + timedelta(days=within_days)
        rows = [
            {
                "crew_id": c["crew_id"],
                "cert_type": c["cert_type"],
                "valid_to": c["valid_to"],
                "days_left": (parse_date(c["valid_to"]) - start).days,
                "rank": self.ds.crew_by_id[c["crew_id"]]["rank"],
            }
            for c in self.ds.certifications
            if start <= parse_date(c["valid_to"]) <= end
        ]
        return {
            "from_date": from_date,
            "within_days": within_days,
            "count": len(rows),
            "certifications": rows,
        }

    # ------------------------------------------------------------------
    def pairings(
        self,
        pairing_id: str | None = None,
        aircraft: str | None = None,
        on_date: str | None = None,
        crew_id: str | None = None,
    ) -> dict:
        found = self.ds.find_pairing(pairing_id, aircraft, on_date, crew_id)
        rows = []
        for p in found:
            rows.append(
                {
                    "pairing_id": p["pairing_id"],
                    "aircraft": p["aircraft"],
                    "aircraft_type": self.ds.flight_by_id[p["days"][0]["flights"][0]][
                        "aircraft_type"
                    ],
                    "days": p["days"],
                    "crew": p["crew"],
                }
            )
        return {"count": len(rows), "pairings": rows}

    # ------------------------------------------------------------------
    def risk(self, crew_id: str) -> dict:
        r = self.ds.risk_by_id.get(crew_id)
        if not r:
            return {"error": f"No risk signal for {crew_id}"}
        return {
            "crew_id": crew_id,
            "score": r["disruption_risk_score"],
            "drivers": r["drivers"],
            "as_of_utc": r["as_of_utc"],
        }

    # ------------------------------------------------------------------
    def stations(self, from_station: str | None = None) -> dict:
        if from_station:
            dests = sorted(
                {
                    f["arr_station"]
                    for f in self.ds.flights
                    if f["dep_station"] == from_station.upper()
                }
            )
            return {"from": from_station.upper(), "nonstop_destinations": dests}
        return {"stations": sorted(self.ds.stations())}
