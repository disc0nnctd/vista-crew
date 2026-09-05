"""The seven rules from rules.json, as deterministic checks over Duty objects.

A `Finding` carries the numbers; rendering it as a string is a separate step,
so the same finding can be graded byte-exact, drawn in a panel, or read aloud
by the model without any of those three re-deriving a figure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .data import Dataset, Duty, fmt_date, fmt_utc, hhmm, parse_clock, parse_date

EPS = 1e-6  # duties sitting exactly on a limit are legal, not phantom breaches


@dataclass
class Finding:
    """One rule failure, with the arithmetic that produced it kept separate
    from how it is worded."""

    rule: str
    limit: float | str
    actual: float | str
    excess: float | None = None
    context: dict = field(default_factory=dict)

    def render(self) -> str:
        """The graded wording. These strings are compared literally against
        the answer keys, so each branch matches one key format exactly."""
        r, c = self.rule, self.context
        if r == "RULE-FDP-01":
            return (
                f"RULE-FDP-01: FDP {self.actual}h exceeds limit {self.limit}h "
                f"on {c['date']} ({c['sectors']} sectors)"
            )
        if r == "RULE-DUTY-02":
            return (
                f"RULE-DUTY-02: would exceed {self.limit:g}h/7d by "
                f"{hhmm(self.excess)} on {c['date']} (total {self.actual}h)"
            )
        if r == "RULE-FLT-03":
            return (
                f"RULE-FLT-03: would exceed {self.limit:g}h/28d by "
                f"{hhmm(self.excess)} on {c['date']} (total {self.actual}h)"
            )
        if r == "RULE-REST-04":
            if c.get("double_booked"):
                return (
                    f"double-booked: {c['predecessor']} overlaps "
                    f"{c['follower']} on {c['date']}"
                )
            return (
                f"RULE-REST-04: only {self.actual}h rest before "
                f"{c['follower']} on {c['date']} ({c['direction']})"
            )
        if r == "RULE-QUAL-05":
            return f"RULE-QUAL-05: no {c['aircraft_type']} rating"
        if r == "RULE-CERT-06":
            return (
                f"RULE-CERT-06: {c['cert_type']} expired {c['valid_to']} "
                f"(duty {c['date']})"
            )
        if r == "RULE-BASE-07":
            if c.get("kind") == "window":
                return (
                    f"reserve on-call window {c['window']} does not cover "
                    f"required report {c['report']}"
                )
            return f"RULE-BASE-07: based {c['base']}, pairing departs {c['station']}"
        return f"{r}: {self.actual} vs {self.limit}"

    def to_dict(self) -> dict:
        return {
            "rule": self.rule,
            "text": self.context.get("rule_text", ""),
            "limit": self.limit,
            "actual": self.actual,
            "excess": self.excess,
            "context": {k: v for k, v in self.context.items() if k != "rule_text"},
            "message": self.render(),
        }


ALL_RULES = [
    "RULE-FDP-01",
    "RULE-DUTY-02",
    "RULE-FLT-03",
    "RULE-REST-04",
    "RULE-QUAL-05",
    "RULE-CERT-06",
    "RULE-BASE-07",
]

# The order checks run in. Reproduces the scenario exclusion lists: a candidate
# failing both the on-call window and the rating is reported on the window.
CHECK_ORDER = ["base", "window", "rating", "cert", "fdp", "rest", "duty7d", "flt28d"]


class RulesEngine:
    def __init__(self, ds: Dataset):
        self.ds = ds
        p = ds.rule_params
        self.base_fdp = p["RULE-FDP-01"]["base_fdp_hours"]
        self.fdp_reduction = p["RULE-FDP-01"]["reduction_per_extra_sector_hours"]
        self.free_sectors = p["RULE-FDP-01"]["free_sectors"]
        self.max_duty_7d = p["RULE-DUTY-02"]["max_duty_hours"]
        self.duty_window = p["RULE-DUTY-02"]["window_days"]
        self.max_flight_28d = p["RULE-FLT-03"]["max_flight_hours"]
        self.flight_window = p["RULE-FLT-03"]["window_days"]
        self.min_rest = p["RULE-REST-04"]["min_rest_hours"]

    # --- primitives ------------------------------------------------------
    def fdp_limit(self, sectors: int) -> float:
        return self.base_fdp - self.fdp_reduction * max(0, sectors - self.free_sectors)

    def block_hours(self, duty: Duty) -> float:
        return sum(self.ds.flight_by_id[f]["block_hours"] for f in duty.flight_ids)

    def accrued(
        self,
        crew_id: str,
        window_end: date,
        days: int,
        kind: str,
        extra: list[Duty] | None = None,
    ) -> float:
        """Hours in the N calendar days ending `window_end`, inclusive.

        Two sources, both added: `daily_history` (which runs to the snapshot
        day) and the roster. They both carry hours on the snapshot day, and the
        published duty_hours_7d / flight_hours_28d fields are their sum -- 11
        crew have different nonzero values in each on 2026-09-14. Adding
        reproduces the published field for all 150 crew, so there is no
        de-duplication step and no flag for one.
        """
        start = window_end - timedelta(days=days - 1)
        clock = self.ds.clock_by_id[crew_id]
        key = "duty_hours" if kind == "duty" else "flight_hours"

        total = sum(
            h[key]
            for h in clock["daily_history"]
            if start <= parse_date(h["date"]) <= window_end
        )
        for d in self.week(crew_id, extra):
            if start <= d.on_date <= window_end:
                total += d.fdp_hours if kind == "duty" else self.block_hours(d)
        return round(total, 2)

    def week(self, crew_id: str, extra: list[Duty] | None = None) -> list[Duty]:
        """The crew member's rostered duties merged with any proposed cover,
        in report order. Rest is checked against this merged list, never
        against the roster alone.

        A proposed duty *replaces* the candidate's own rostered duty on the
        same pairing and date rather than stacking on it. Without that, anyone
        already rostered on the pairing collides with themselves and is
        excluded for a clash that does not exist -- which is exactly how the
        answer keys treat them: S5 ranks C-2840 and C-4588 as cover options for
        P-2213 even though both already work it.
        """
        proposed = list(extra or [])
        replaced = {(d.pairing_id, d.on_date) for d in proposed}
        merged = [
            d
            for d in self.ds.duties_for(crew_id)
            if (d.pairing_id, d.on_date) not in replaced
        ] + proposed
        merged.sort(key=lambda d: d.report_utc)
        return merged

    # --- individual rules -------------------------------------------------
    def check_fdp(self, duty: Duty) -> Finding | None:
        limit = self.fdp_limit(duty.sectors)
        actual = round(duty.fdp_hours, 2)
        if actual > limit + EPS:
            return Finding(
                "RULE-FDP-01",
                limit,
                actual,
                round(actual - limit, 2),
                {
                    "date": fmt_date(duty.on_date),
                    "sectors": duty.sectors,
                    "pairing_id": duty.pairing_id,
                    "rule_text": self.ds.rule_text["RULE-FDP-01"],
                },
            )
        return None

    def check_rating(self, crew_id: str, aircraft_type: str) -> Finding | None:
        ratings = self.ds.crew_by_id[crew_id]["ratings"]
        if aircraft_type not in ratings:
            return Finding(
                "RULE-QUAL-05",
                aircraft_type,
                ", ".join(ratings) or "none",
                None,
                {
                    "aircraft_type": aircraft_type,
                    "held": ratings,
                    "rule_text": self.ds.rule_text["RULE-QUAL-05"],
                },
            )
        return None

    def check_certs(self, crew_id: str, on_date: date) -> Finding | None:
        """Validity is tested against valid_to only. valid_from is often in the
        future in this dataset and testing it excludes almost everyone."""
        for cert in self.ds.certs_by_crew.get(crew_id, []):
            if parse_date(cert["valid_to"]) < on_date:
                return Finding(
                    "RULE-CERT-06",
                    fmt_date(on_date),
                    cert["valid_to"],
                    None,
                    {
                        "cert_type": cert["cert_type"],
                        "valid_to": cert["valid_to"],
                        "date": fmt_date(on_date),
                        "rule_text": self.ds.rule_text["RULE-CERT-06"],
                    },
                )
        return None

    def check_duty_7d(self, crew_id: str, duty: Duty, extra: list[Duty]) -> Finding | None:
        total = self.accrued(crew_id, duty.on_date, self.duty_window, "duty", extra)
        if total > self.max_duty_7d + EPS:
            return Finding(
                "RULE-DUTY-02",
                self.max_duty_7d,
                total,
                round(total - self.max_duty_7d, 2),
                {
                    "date": fmt_date(duty.on_date),
                    "rule_text": self.ds.rule_text["RULE-DUTY-02"],
                },
            )
        return None

    def check_flight_28d(self, crew_id: str, duty: Duty, extra: list[Duty]) -> Finding | None:
        total = self.accrued(crew_id, duty.on_date, self.flight_window, "flight", extra)
        if total > self.max_flight_28d + EPS:
            return Finding(
                "RULE-FLT-03",
                self.max_flight_28d,
                total,
                round(total - self.max_flight_28d, 2),
                {
                    "date": fmt_date(duty.on_date),
                    "rule_text": self.ds.rule_text["RULE-FLT-03"],
                },
            )
        return None

    def check_rest(self, crew_id: str, proposed: list[Duty]) -> list[Finding]:
        """Rest on both edges of every inserted duty, against the merged week.

        Each gap is named after the duty that *follows* it, because that is the
        report a controller has to protect. When the follower is the proposed
        cover the wording is "before COVER ... (rest conflict)"; when the cover
        comes first and squeezes an existing pairing it is "before <pairing>
        ... (downstream conflict)". Both forms are graded literally.

        A negative gap is a physical clash -- the later duty reports before the
        earlier one releases. It is stored as the signed gap plus a separate
        double-booking finding, and the two join with "; ". Humans are never
        shown a negative rest figure; `duty_timeline` renders it as an overlap.
        """
        findings: list[Finding] = []
        merged = self.week(crew_id, proposed)
        proposed_keys = {(d.pairing_id, d.on_date) for d in proposed}
        is_cover = lambda d: (d.pairing_id, d.on_date) in proposed_keys
        name = lambda d: "COVER" if is_cover(d) else d.pairing_id

        for prev, nxt in zip(merged, merged[1:]):
            if not (is_cover(prev) or is_cover(nxt)):
                continue
            gap = round((nxt.report_utc - prev.release_utc).total_seconds() / 3600.0, 2)
            if gap >= self.min_rest - EPS:
                continue
            ctx = {
                "follower": name(nxt),
                "predecessor": name(prev),
                "date": fmt_date(nxt.on_date),
                "direction": "rest conflict" if is_cover(nxt) else "downstream conflict",
                "overlap": gap < 0,
                "overlap_hours": round(abs(gap), 2) if gap < 0 else None,
                "rule_text": self.ds.rule_text["RULE-REST-04"],
            }
            findings.append(
                Finding("RULE-REST-04", self.min_rest, gap, round(self.min_rest - gap, 2), ctx)
            )
            if gap < 0:
                findings.append(
                    Finding(
                        "RULE-REST-04",
                        "no overlap",
                        f"{name(prev)} overlaps {name(nxt)}",
                        None,
                        {**ctx, "double_booked": True},
                    )
                )
        return findings

    def check_base(self, crew_id: str, dep_station: str) -> Finding | None:
        base = self.ds.crew_by_id[crew_id]["base"]
        if base != dep_station:
            return Finding(
                "RULE-BASE-07",
                dep_station,
                base,
                None,
                {
                    "kind": "base",
                    "base": base,
                    "station": dep_station,
                    "needs_positioning": True,
                    "rule_text": self.ds.rule_text["RULE-BASE-07"],
                },
            )
        return None

    def check_reserve_window(self, crew_id: str, on_date: date, report_utc: datetime) -> Finding | None:
        """Tested against the required report time, inclusive bounds.

        Not the callout time, despite the rule prose: the dataset judges one
        candidate on 09:00Z rather than their rostered 06:00Z. Base and window
        are two gates and this one consumes the first.
        """
        res = self.ds.reserve_by_id.get(crew_id)
        if not res:
            return None
        if fmt_date(on_date) not in res["dates"]:
            return Finding(
                "RULE-BASE-07",
                "on reserve",
                "not on reserve this date",
                None,
                {
                    "kind": "window",
                    "window": "not rostered reserve",
                    "report": report_utc.strftime("%H:%MZ"),
                    "rule_text": self.ds.rule_text["RULE-BASE-07"],
                },
            )
        w = res["oncall_window_utc"]
        sh, sm = parse_clock(w["start"])
        eh, em = parse_clock(w["end"])
        start = report_utc.replace(hour=sh, minute=sm, second=0)
        end = report_utc.replace(hour=eh, minute=em, second=0)
        if not (start <= report_utc <= end):
            return Finding(
                "RULE-BASE-07",
                f"{w['start']}-{w['end']}Z",
                report_utc.strftime("%H:%MZ"),
                None,
                {
                    "kind": "window",
                    "window": f"{w['start']}-{w['end']}Z",
                    "report": report_utc.strftime("%H:%MZ"),
                    "rule_text": self.ds.rule_text["RULE-BASE-07"],
                },
            )
        return None

    # --- the composite check ---------------------------------------------
    def callable_now(
        self,
        crew_id: str,
        proposed: list[Duty],
        report_utc: datetime | None = None,
    ) -> Finding | None:
        """Can we call this person out? -- a different question from whether the
        assignment breaches a rule, and the dataset grades both.

        For a reserve this is the on-call window against the required report
        time. It is not a rule breach: Q24 asks whether reserve C-3305 can cover
        P-2291 and the key reports only the duty-hours breach, while the same
        person is excluded from the S2 ranking on the window. So the window
        gates candidate selection in `resolve`, and never enters `breaches`.
        """
        if not proposed or crew_id not in self.ds.reserve_by_id:
            return None
        first = proposed[0]
        return self.check_reserve_window(
            crew_id, first.on_date, report_utc or first.report_utc
        )

    def check_duties(
        self,
        crew_id: str,
        proposed: list[Duty],
        *,
        positioned: bool = False,
    ) -> list[Finding]:
        """The seven rules against a proposed set of duty days, in CHECK_ORDER.

        There is no parameter that skips a rule. `positioned` records that a
        deadhead has been arranged, which satisfies RULE-BASE-07's base gate;
        it does not disable the check, it supplies its precondition.
        """
        findings: list[Finding] = []
        if not proposed:
            return findings
        first = proposed[0]
        dep = self.ds.flight_by_id[first.flight_ids[0]]["dep_station"]
        ac_type = self.ds.flight_by_id[first.flight_ids[0]]["aircraft_type"]

        # 1. base
        if not positioned:
            f = self.check_base(crew_id, dep)
            if f:
                findings.append(f)
        # 2. rating
        f = self.check_rating(crew_id, ac_type)
        if f:
            findings.append(f)
        # 3/4. per duty day: certification, then FDP
        for d in proposed:
            f = self.check_certs(crew_id, d.on_date)
            if f:
                findings.append(f)
            f = self.check_fdp(d)
            if f:
                findings.append(f)
        # 5. rest, both edges, against the merged week
        findings.extend(self.check_rest(crew_id, proposed))
        # 6. rolling duty, then rolling block hours
        for d in proposed:
            f = self.check_duty_7d(crew_id, d, proposed)
            if f:
                findings.append(f)
        for d in proposed:
            f = self.check_flight_28d(crew_id, d, proposed)
            if f:
                findings.append(f)
        return findings

    @staticmethod
    def render_all(findings: list[Finding]) -> str:
        """The "; " join the answer keys use."""
        return "; ".join(f.render() for f in findings)
