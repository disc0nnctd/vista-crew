"""Loaders and indices over problem_statement/data.

Everything downstream reads the dataset through this module, so there is
exactly one place that knows the file layout and the record order.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import cached_property
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "problem_statement" / "data"

REQUIRED = [
    "flights",
    "crew",
    "rosters",
    "duty_clocks",
    "reserve_pool",
    "certifications",
    "rules",
    "costs",
    "risk_signals",
    "scenarios",
    "questions",
]

PILOT_RANKS = {"Captain", "First Officer"}


def parse_utc(s: str) -> datetime:
    """'2026-09-14T02:30:00Z' -> naive UTC datetime.

    The whole dataset is UTC (rules.json time_convention), so we drop the
    marker rather than carry a tzinfo through every arithmetic site.
    """
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")


def fmt_utc(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def fmt_date(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def parse_clock(s: str) -> tuple[int, int]:
    """'06:30' -> (6, 30)."""
    h, m = s.split(":")
    return int(h), int(m)


def hhmm(hours: float) -> str:
    """1.3333 -> '1h20m'. Used in breach strings, which are graded literally."""
    total = int(round(hours * 60))
    return f"{total // 60}h{total % 60:02d}m"


@dataclass(frozen=True)
class Duty:
    """One duty day: a report time, a release time, and the legs between.

    Produced both from the roster (existing duties) and by the resolver
    (a proposed cover duty). The rules engine only ever sees Duty objects,
    so a hypothetical is checked by exactly the same code as a real one.
    """

    crew_id: str
    pairing_id: str
    on_date: date
    report_utc: datetime
    release_utc: datetime
    flight_ids: tuple[str, ...]
    aircraft: str
    role: str = ""
    proposed: bool = False

    @property
    def sectors(self) -> int:
        return len(self.flight_ids)

    @property
    def fdp_hours(self) -> float:
        return (self.release_utc - self.report_utc).total_seconds() / 3600.0

    def shifted(self, delay_hours: float, hold_report: bool) -> "Duty":
        """Apply a delay under one of the two conventions.

        hold_report=True is a technical delay: report is held, release slips,
        so the duty grows and FDP can breach.
        hold_report=False is positioning: both edges move, FDP is unchanged.
        """
        d = timedelta(hours=delay_hours)
        return Duty(
            crew_id=self.crew_id,
            pairing_id=self.pairing_id,
            on_date=self.on_date,
            report_utc=self.report_utc if hold_report else self.report_utc + d,
            release_utc=self.release_utc + d,
            flight_ids=self.flight_ids,
            aircraft=self.aircraft,
            role=self.role,
            proposed=self.proposed,
        )


# A deployment target with no filesystem sets this to the parsed tables before
# anything constructs a Dataset. The edge is the case in mind: Cloudflare's
# Python runtime has no `problem_statement/data` to read, and the alternative
# was a second loader that could drift from this one.
BUNDLED: dict | None = None


class Dataset:
    def __init__(self, data_dir: Path | str = DATA_DIR, records: dict | None = None):
        records = records if records is not None else BUNDLED
        if records is not None:
            self.dir = None
            for name in REQUIRED:
                setattr(self, f"_{name}", records[name])
            return
        self.dir = Path(data_dir)
        missing = [n for n in REQUIRED if not (self.dir / f"{n}.json").exists()]
        if missing:
            raise SystemExit(
                "Missing dataset files: "
                + ", ".join(f"{m}.json" for m in missing)
                + ". scenarios.json and questions.json are the grading surface; "
                "the system cannot be evaluated without them."
            )
        for name in REQUIRED:
            setattr(self, f"_{name}", json.loads((self.dir / f"{name}.json").read_text()))

    # --- raw tables -----------------------------------------------------
    @property
    def flights(self) -> list[dict]:
        return self._flights

    @property
    def crew(self) -> list[dict]:
        return self._crew

    @property
    def pairings(self) -> list[dict]:
        return self._rosters["pairings"]

    @property
    def flagged_exceptions(self) -> list[dict]:
        return self._rosters["flagged_exceptions"]

    @property
    def duty_clocks(self) -> list[dict]:
        return self._duty_clocks

    @property
    def reserve_pool(self) -> list[dict]:
        return self._reserve_pool

    @property
    def certifications(self) -> list[dict]:
        return self._certifications

    @property
    def rules(self) -> dict:
        return self._rules

    @property
    def costs(self) -> dict:
        return self._costs

    @property
    def risk_signals(self) -> list[dict]:
        return self._risk_signals

    @property
    def scenarios(self) -> list[dict]:
        return self._scenarios

    @property
    def questions(self) -> list[dict]:
        return self._questions

    # --- rule params ----------------------------------------------------
    @cached_property
    def rule_params(self) -> dict[str, dict]:
        return {r["rule_id"]: r.get("params", {}) for r in self.rules["rules"]}

    @cached_property
    def rule_text(self) -> dict[str, str]:
        """Rule id -> plain English. Rule ids mean nothing on their own; every
        surface that shows an id shows this next to it."""
        return {r["rule_id"]: r["text"] for r in self.rules["rules"]}

    # --- indices --------------------------------------------------------
    @cached_property
    def flight_by_id(self) -> dict[str, dict]:
        return {f["flight_id"]: f for f in self.flights}

    @cached_property
    def crew_by_id(self) -> dict[str, dict]:
        return {c["crew_id"]: c for c in self.crew}

    @cached_property
    def pairing_by_id(self) -> dict[str, dict]:
        return {p["pairing_id"]: p for p in self.pairings}

    @cached_property
    def clock_by_id(self) -> dict[str, dict]:
        return {d["crew_id"]: d for d in self.duty_clocks}

    @cached_property
    def risk_by_id(self) -> dict[str, dict]:
        return {r["crew_id"]: r for r in self.risk_signals}

    @cached_property
    def certs_by_crew(self) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for c in self.certifications:
            out.setdefault(c["crew_id"], []).append(c)
        return out

    @cached_property
    def reserve_by_id(self) -> dict[str, dict]:
        return {r["crew_id"]: r for r in self.reserve_pool}

    @cached_property
    def enumeration_order(self) -> list[str]:
        """Candidate enumeration follows duty_clocks.json record order.

        crew.json is sorted on export; the answer keys' exclusion lists follow
        duty_clocks. This is the order every candidate loop uses.
        """
        return [d["crew_id"] for d in self.duty_clocks]

    @cached_property
    def schedule_dates(self) -> list[str]:
        """Every date the schedule covers. Questions say "17 Sep" without a
        year, and a model that supplies the wrong one gets an empty result that
        still looks like an answer -- so the window is stated in the prompt and
        checked by the tools."""
        return sorted({f["date"] for f in self.flights})

    def date_in_schedule(self, on_date: str) -> bool:
        return on_date in self.schedule_dates

    @cached_property
    def aircraft_types(self) -> list[str]:
        """A320, ATR72. A controller names a type as readily as a tail, so
        anything that filters on aircraft has to know the difference."""
        return sorted({f["aircraft_type"] for f in self.flights})

    @cached_property
    def tails(self) -> list[str]:
        return sorted({f["aircraft"] for f in self.flights})

    def type_of_pairing(self, pairing: dict) -> str:
        """The fleet a pairing is flown on, read from its first leg."""
        first = pairing["days"][0]["flights"][0]
        return self.flight_by_id[first]["aircraft_type"]

    @cached_property
    def snapshot_utc(self) -> datetime:
        return parse_utc(self.duty_clocks[0]["as_of_utc"])

    # --- derived: duties -------------------------------------------------
    @cached_property
    def duties_by_crew(self) -> dict[str, list[Duty]]:
        """Every rostered duty day, per crew member, sorted by report time."""
        out: dict[str, list[Duty]] = {}
        for p in self.pairings:
            for day in p["days"]:
                for member in p["crew"]:
                    duty = Duty(
                        crew_id=member["crew_id"],
                        pairing_id=p["pairing_id"],
                        on_date=parse_date(day["date"]),
                        report_utc=parse_utc(day["report_utc"]),
                        release_utc=parse_utc(day["release_utc"]),
                        flight_ids=tuple(day["flights"]),
                        aircraft=p["aircraft"],
                        role=member["role"],
                    )
                    out.setdefault(member["crew_id"], []).append(duty)
        for v in out.values():
            v.sort(key=lambda d: d.report_utc)
        return out

    def duties_for(self, crew_id: str) -> list[Duty]:
        return self.duties_by_crew.get(crew_id, [])

    @cached_property
    def pairing_duties(self) -> dict[str, list[Duty]]:
        """The duty days of a pairing, independent of who is on it.

        crew_id is left blank; the resolver stamps a candidate onto these when
        it builds a proposed cover.
        """
        out: dict[str, list[Duty]] = {}
        for p in self.pairings:
            out[p["pairing_id"]] = [
                Duty(
                    crew_id="",
                    pairing_id=p["pairing_id"],
                    on_date=parse_date(day["date"]),
                    report_utc=parse_utc(day["report_utc"]),
                    release_utc=parse_utc(day["release_utc"]),
                    flight_ids=tuple(day["flights"]),
                    aircraft=p["aircraft"],
                    proposed=True,
                )
                for day in p["days"]
            ]
        return out

    # --- lookups a controller would actually speak -----------------------
    def find_pairing(
        self,
        pairing_id: str | None = None,
        aircraft: str | None = None,
        on_date: str | None = None,
        crew_id: str | None = None,
    ) -> list[dict]:
        """Accept whatever the desk says: a pairing id, or a tail plus a date,
        or the person who dropped out. Requiring the internal id costs a
        lookup round every time."""
        out = self.pairings
        if pairing_id:
            out = [p for p in out if p["pairing_id"] == pairing_id]
        if aircraft:
            want = aircraft.upper()
            # "A320" is a type, "VT-DXA" is a tail, and a controller says both.
            # Matching only tails made `aircraft="A320"` return zero pairings
            # with a confident count, which is the worst kind of wrong answer.
            if want in {t.upper() for t in self.aircraft_types}:
                out = [p for p in out if self.type_of_pairing(p).upper() == want]
            else:
                out = [p for p in out if p["aircraft"].upper() == want]
        if on_date:
            out = [p for p in out if any(d["date"] == on_date for d in p["days"])]
        if crew_id:
            out = [p for p in out if any(m["crew_id"] == crew_id for m in p["crew"])]
        return out

    def role_on_pairing(self, pairing_id: str, crew_id: str) -> str | None:
        p = self.pairing_by_id.get(pairing_id)
        if not p:
            return None
        for m in p["crew"]:
            if m["crew_id"] == crew_id:
                return m["role"]
        return None

    def stations(self) -> set[str]:
        s = set()
        for f in self.flights:
            s.add(f["dep_station"])
            s.add(f["arr_station"])
        return s

    def is_pilot(self, crew_id: str) -> bool:
        return self.crew_by_id[crew_id]["rank"] in PILOT_RANKS


_DEFAULT: Dataset | None = None


def load(data_dir: Path | str = DATA_DIR) -> Dataset:
    global _DEFAULT
    if _DEFAULT is None or Path(data_dir) != _DEFAULT.dir:
        _DEFAULT = Dataset(data_dir)
    return _DEFAULT
