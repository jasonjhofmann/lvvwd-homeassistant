"""LVVWD residential water tariff engine — pure stdlib, NO Home Assistant imports.

Implements ``tariff/lvvwd-rates.yaml`` exactly, validated to the penny against
real statements (synthetic test fixtures live under ``tests/``). Importable from
the integration and from tests (``import`` after adding the package to the path).

THE BILL over an N-day period (start..end INCLUSIVE; start = prev read + 1):
  1) service        = service_per_day[meter_size] * N
  2) volumetric     = 4 tiers; cumulative cap k = round-NEAREST-1000(daily_cum_k * N)
                      with daily cums 167/334/667 gal; tier 4 unbounded
  3) excessive use  = max(0, usage - threshold) * $9.00/kgal, where threshold =
                      SUM over season PORTIONS of CEIL(season_avg * days / 1000)
                      (CEILING per portion, never round-nearest)
  4) SNWA commodity = usage_kgal * commodity_rate
  5) SNWA infra     = snwa_infra_per_day[meter_size] * N
  6) SNWA reliability = 0.25% * (1+2+3+4)   — base EXCLUDES infra (5)
  subtotal = 1+2+3+4+5+6;  every line rounds to the cent HALF-UP; reliability
  rounds to the cent at the end (its base = the already-rounded lines).

METER SIZE: both the daily service charge (Table A.5) and the SNWA residential
infrastructure charge (Table A.17.a) scale with meter size, so both are keyed by
meter size (inches as a string). All other levers — the tier daily blocks, tier
rates, SNWA commodity, reliability rate, and the excessive-use surcharge /
thresholds — are meter-size-independent. ``compute_bill`` defaults to the most
common residential size, 5/8" (``"0.625"``).

JAN-1 RATE SPLIT: rates adjust every Jan 1; a period straddling Jan 1 splits
into sub-periods at each Jan 1. Usage (whole 1000-gal units) is prorated by
day-count with CUMULATIVE half-up rounding (round one half, derive the other —
halves always sum to the total). Service / tiers (caps use SUB-period day
counts) / commodity / infra are computed PER sub-period. EXCEPTIONS: the
excessive-use threshold spans the WHOLE period (one line), and reliability
applies to the combined base.

SEASONS (Service Rules §7.9 Table 7-1; avg gal/day):
  winter 467 (Nov 1 – Feb 28/29), spring 533 (Mar 1 – Apr 30),
  summer 933 (May 1 – Aug 31),   fall 867 (Sep 1 – Oct 31).
  NOTE: fall (867) is from the official table but NOT yet bill-validated.

DAILY ACCRUAL DEFINITION (accrue_period):
  cost_to_date(d) = compute_bill(period_start, d, floor(cum_gallons(d)/1000))
  i.e. the bill that WOULD be issued if the meter were read at end of day d.
  Properties:
    * the final day's value equals compute_bill on the period total exactly
      (assuming billed usage = floor(total_gal/1000); if the utility's meter-
      read rounding differs by 1 unit the next statement anchor trues it up);
    * day-over-day deltas sum to the period total;
    * deltas are LUMPY at 1000-gal crossings and can occasionally be slightly
      NEGATIVE on low-use days (a growing N raises tier caps and the excessive
      threshold, shifting units into cheaper tiers). The cumulative is what
      matters; real statement anchors re-true it each cycle.

PRE-2026 METER SIZES: the 2025 rate schedule lists service/infra ONLY for the
1" meter (the only size present on the historical statements used to validate
the engine; the non-1" 2025 columns were never published in the 2026 PDF). Cost
is computed only for current/forward periods, so this never affects go-forward
2026+ users; a pre-2026 period on a non-1" meter raises ``KeyError`` by design.

Future years: add a new entry to RATE_SCHEDULES (and tariff/lvvwd-rates.yaml).
Dates past the latest known schedule price at that latest schedule (a stale-
rate approximation until the new Jan-1 schedule is added).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

__all__ = [
    "RATE_SCHEDULES",
    "DEFAULT_METER_SIZE",
    "SUPPORTED_METER_SIZES",
    "BillResult",
    "SubPeriod",
    "compute_bill",
    "accrue_period",
    "tier_units",
    "tier_caps_kgal",
    "season_portions",
    "excessive_threshold_kgal",
    "prorate_usage",
    "split_at_jan1",
    "schedule_for",
    "round_cent",
    "round_gal_nearest_kgal",
    "ceil_gal_kgal",
    "current_period_start",
    "verify_statements",
    "build_daily_cost_points",
]

D = Decimal
CENT = D("0.01")

DEFAULT_METER_SIZE = "0.625"
SUPPORTED_METER_SIZES = (
    "0.625",
    "0.75",
    "1",
    "1.5",
    "2",
    "3",
    "4",
    "6",
    "8",
    "10",
    "12",
)

# ---------------------------------------------------------------------------
# Tariff data (mirror of tariff/lvvwd-rates.yaml — keep in sync)
# ---------------------------------------------------------------------------

# Table A.5 Daily Service Charge ($/day) by meter size, and Table A.17.a SNWA
# residential Daily Infrastructure Charge ($/day) by meter size. The 2025
# schedule has only the 1" column (see PRE-2026 METER SIZES in the docstring).

RATE_SCHEDULES = (
    {  # 2025 schedule (through 2025-12-31) — 1" only
        "effective": dt.date(2025, 1, 1),
        "service_per_day": {"1": D("0.6528")},
        "tier_rates": (D("1.56"), D("2.78"), D("4.14"), D("6.14")),
        "commodity_per_kgal": D("0.64"),
        "snwa_infra_per_day": {"1": D("1.4431")},
    },
    {  # 2026 schedule (effective 2026-01-01) — all meter sizes
        "effective": dt.date(2026, 1, 1),
        "service_per_day": {
            "0.625": D("0.4627"),
            "0.75": D("0.5328"),
            "1": D("0.6730"),
            "1.5": D("1.0232"),
            "2": D("1.4442"),
            "3": D("2.5662"),
            "4": D("3.8284"),
            "6": D("7.3346"),
            "8": D("11.5421"),
            "10": D("16.4507"),
            "12": D("24.1644"),
        },
        "tier_rates": (D("1.61"), D("2.87"), D("4.27"), D("6.33")),
        "commodity_per_kgal": D("0.67"),
        "snwa_infra_per_day": {
            "0.625": D("0.5657"),
            "0.75": D("0.5657"),
            "1": D("1.4979"),
            "1.5": D("2.9956"),
            "2": D("4.7932"),
            "3": D("9.5853"),
            "4": D("14.9770"),
            "6": D("29.9536"),
            "8": D("47.9252"),
            "10": D("53.5095"),
            "12": D("53.5095"),
        },
    },
)

TIER_DAILY_CUM_GAL = (167, 334, 667)  # cumulative daily blocks; tier 4 unbounded
EXCESSIVE_RATE_PER_KGAL = D("9.00")  # both years
RELIABILITY_RATE = D("0.0025")  # base excludes SNWA infrastructure

# Season avg daily use (gal/day), Service Rules §7.9 Table 7-1.
# fall=867 is from the table, NOT yet bill-validated (no Sep/Oct statement).
SEASON_AVG_GAL_PER_DAY = {"winter": 467, "spring": 533, "summer": 933, "fall": 867}
_SEASON_BY_MONTH = {
    11: "winter",
    12: "winter",
    1: "winter",
    2: "winter",
    3: "spring",
    4: "spring",
    5: "summer",
    6: "summer",
    7: "summer",
    8: "summer",
    9: "fall",
    10: "fall",
}


# ---------------------------------------------------------------------------
# Rounding primitives
# ---------------------------------------------------------------------------


def round_cent(x):
    """Round a Decimal dollar amount to the cent, half UP (per-line rule)."""
    return D(x).quantize(CENT, rounding=ROUND_HALF_UP)


def round_gal_nearest_kgal(gal):
    """Round NEAREST 1000 gal (half up) -> whole-kgal units. Tier caps only."""
    return (int(gal) + 500) // 1000


def ceil_gal_kgal(gal):
    """CEILING to next 1000 gal -> whole-kgal units. Excessive threshold only."""
    return -(-int(gal) // 1000)


def _round_half_up_frac(num, den):
    """round(num/den) half up, exact integer arithmetic (num, den > 0 ints)."""
    return (2 * num + den) // (2 * den)


def _as_date(d):
    if isinstance(d, dt.datetime):
        return d.date()
    if isinstance(d, dt.date):
        return d
    return dt.date.fromisoformat(str(d))


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------


def schedule_for(day):
    """Rate schedule in effect on `day` (latest effective <= day).
    Dates past the newest schedule use the newest (stale-rate approximation
    until the next Jan-1 schedule is added). Dates before the earliest raise."""
    day = _as_date(day)
    chosen = None
    for sched in RATE_SCHEDULES:
        if sched["effective"] <= day and (
            chosen is None or sched["effective"] > chosen["effective"]
        ):
            chosen = sched
    if chosen is None:
        raise ValueError(
            f"no rate schedule for {day} "
            f"(earliest effective {RATE_SCHEDULES[0]['effective']})"
        )
    return chosen


def split_at_jan1(start, end):
    """Split [start..end] (inclusive) into sub-periods at every Jan 1 inside it."""
    start, end = _as_date(start), _as_date(end)
    subs = []
    s = start
    while s <= end:
        e = min(end, dt.date(s.year, 12, 31))
        subs.append((s, e))
        s = e + dt.timedelta(days=1)
    return subs


def prorate_usage(usage_kgal, sub_day_counts):
    """Prorate whole-kgal usage across sub-periods by day count.
    CUMULATIVE half-up rounding: cum_i = round(usage * cumdays_i / total), each
    sub gets cum_i - cum_{i-1} -> for 2 subs this is exactly 'round one half,
    derive the other'; halves always sum back to the total."""
    total_days = sum(sub_day_counts)
    out, cum_days, prev = [], 0, 0
    for nd in sub_day_counts:
        cum_days += nd
        cum_units = _round_half_up_frac(int(usage_kgal) * cum_days, total_days)
        out.append(cum_units - prev)
        prev = cum_units
    return out


def tier_caps_kgal(n_days):
    """Cumulative tier caps (kgal) for an n-day (sub-)period:
    round-NEAREST-1000(daily_cum * n) for daily cums 167/334/667."""
    return tuple(round_gal_nearest_kgal(blk * n_days) for blk in TIER_DAILY_CUM_GAL)


def tier_units(usage_kgal, n_days):
    """Whole-kgal units billed in each of the 4 tiers (tier 4 unbounded)."""
    usage = int(usage_kgal)
    caps = tier_caps_kgal(n_days)
    units, prev = [], 0
    for cap in caps:
        units.append(max(0, min(usage, cap) - min(usage, prev)))
        prev = cap
    units.append(max(0, usage - caps[-1]))
    return tuple(units)


def season_portions(start, end):
    """Contiguous season runs over [start..end] inclusive -> [(name, days), ...].
    Handles leap Feb (winter ends Feb 28/29) and the Nov–Feb year wrap."""
    start, end = _as_date(start), _as_date(end)
    portions = []
    d = start
    while d <= end:
        name = _SEASON_BY_MONTH[d.month]
        if portions and portions[-1][0] == name:
            portions[-1] = (name, portions[-1][1] + 1)
        else:
            portions.append((name, 1))
        d += dt.timedelta(days=1)
    return portions


def excessive_threshold_kgal(start, end):
    """Excessive-use threshold (whole kgal) for the WHOLE period:
    SUM over season portions of CEIL(season_avg_daily * portion_days / 1000).
    CEILING per portion — never round-nearest (Jan & May bills prove it)."""
    total = 0
    for name, days in season_portions(start, end):
        total += ceil_gal_kgal(SEASON_AVG_GAL_PER_DAY[name] * days)
    return total


# ---------------------------------------------------------------------------
# The bill
# ---------------------------------------------------------------------------


@dataclass
class SubPeriod:
    start: dt.date
    end: dt.date  # inclusive
    days: int
    schedule_year: int  # year of the rate schedule applied
    usage_kgal: int  # prorated whole-kgal usage in this sub-period
    service: Decimal
    tier_units: tuple  # (t1, t2, t3, t4) whole kgal
    tier_charges: tuple  # (Decimal x4), each rounded to the cent
    commodity: Decimal
    infra: Decimal


@dataclass
class BillResult:
    start: dt.date
    end: dt.date  # inclusive
    days: int
    usage_kgal: int
    sub_periods: list  # [SubPeriod, ...] (1 normally, 2 across Jan 1)
    service_total: Decimal
    tiers_total: Decimal
    commodity_total: Decimal
    infra_total: Decimal
    excessive_threshold_kgal: int
    excessive_units: int
    excessive_charge: Decimal
    base: Decimal  # service + tiers + excessive + commodity (NO infra)
    reliability: Decimal
    subtotal: Decimal


def compute_bill(start, end, usage_kgal, meter_size=DEFAULT_METER_SIZE):
    """Compute one LVVWD statement over [start..end] INCLUSIVE for `usage_kgal`
    whole 1000-gal units ('billed usage'). `meter_size` is the meter diameter in
    inches as a string (see SUPPORTED_METER_SIZES); it indexes BOTH the daily
    service charge and the SNWA infrastructure charge. Returns BillResult
    (Decimal money)."""
    start, end = _as_date(start), _as_date(end)
    if end < start:
        raise ValueError(f"end {end} before start {start}")
    usage_kgal = int(usage_kgal)
    if usage_kgal < 0:
        raise ValueError("negative usage")
    n = (end - start).days + 1

    subs = split_at_jan1(start, end)
    day_counts = [(e - s).days + 1 for s, e in subs]
    sub_usage = prorate_usage(usage_kgal, day_counts)

    sub_results = []
    for (s, e), nd, u in zip(subs, day_counts, sub_usage, strict=True):
        sched = schedule_for(s)
        units = tier_units(u, nd)
        sub_results.append(
            SubPeriod(
                start=s,
                end=e,
                days=nd,
                schedule_year=sched["effective"].year,
                usage_kgal=u,
                service=round_cent(sched["service_per_day"][meter_size] * nd),
                tier_units=units,
                tier_charges=tuple(
                    round_cent(units[i] * sched["tier_rates"][i]) for i in range(4)
                ),
                commodity=round_cent(u * sched["commodity_per_kgal"]),
                infra=round_cent(sched["snwa_infra_per_day"][meter_size] * nd),
            )
        )

    # Excessive use: WHOLE period, single line (NOT split at Jan 1).
    threshold = excessive_threshold_kgal(start, end)
    exc_units = max(0, usage_kgal - threshold)
    excessive = round_cent(exc_units * EXCESSIVE_RATE_PER_KGAL)

    zero = D("0.00")
    service_total = sum((sp.service for sp in sub_results), zero)
    tiers_total = sum((c for sp in sub_results for c in sp.tier_charges), zero)
    commodity_total = sum((sp.commodity for sp in sub_results), zero)
    infra_total = sum((sp.infra for sp in sub_results), zero)

    base = service_total + tiers_total + excessive + commodity_total
    reliability = round_cent(base * RELIABILITY_RATE)  # 0.25%, EXCLUDES infra
    subtotal = base + infra_total + reliability

    return BillResult(
        start=start,
        end=end,
        days=n,
        usage_kgal=usage_kgal,
        sub_periods=sub_results,
        service_total=service_total,
        tiers_total=tiers_total,
        commodity_total=commodity_total,
        infra_total=infra_total,
        excessive_threshold_kgal=threshold,
        excessive_units=exc_units,
        excessive_charge=excessive,
        base=base,
        reliability=reliability,
        subtotal=subtotal,
    )


# ---------------------------------------------------------------------------
# Daily accrual + cost-statistic series helpers (pure; the coordinator wrapper
# is thin)
# ---------------------------------------------------------------------------


def accrue_period(period_start, daily_gallons, meter_size=DEFAULT_METER_SIZE):
    """Daily cost accrual for an open billing period.

    daily_gallons: mapping {date (or ISO str): gallons} for days >= period_start.
    Returns [(date, cost_to_date Decimal), ...] for every calendar day from
    period_start through the last day present (missing days = 0 gal but still
    advance N). See module docstring 'DAILY ACCRUAL DEFINITION' for properties.
    """
    period_start = _as_date(period_start)
    gal = {}
    for k, v in (daily_gallons or {}).items():
        d = _as_date(k)
        if d >= period_start:
            gal[d] = float(v or 0.0)
    if not gal:
        return []
    last = max(gal)
    out = []
    cum_gal = 0.0
    d = period_start
    while d <= last:
        cum_gal += gal.get(d, 0.0)
        usage_units = int((cum_gal + 1e-6) // 1000.0)  # billed usage so far
        out.append((d, compute_bill(period_start, d, usage_units, meter_size).subtotal))
        d += dt.timedelta(days=1)
    return out


def _norm_statement(st):
    """Normalize a statement record (ISO strings / numbers) -> typed dict."""
    start = _as_date(st["start"] if "start" in st else st["period_start"])
    end = _as_date(st["end"] if "end" in st else st["period_end"])
    return {
        "start": start,
        "end": end,
        "days": int(st.get("days") or ((end - start).days + 1)),
        "usage_kgal": int(st["usage_kgal"]),
        "subtotal": D(str(st["subtotal"])),
        "meter_size": str(st.get("meter_size") or DEFAULT_METER_SIZE),
        "bill_date": st.get("bill_date"),
    }


def current_period_start(statements):
    """Start of the OPEN billing period = day after the latest statement end."""
    sts = [_norm_statement(s) for s in statements]
    return max(s["end"] for s in sts) + dt.timedelta(days=1)


def verify_statements(statements):
    """Recompute every statement; return a list of mismatch dicts (empty = all
    reconcile to the penny). Drift here = a tariff change -> update schedules."""
    bad = []
    for st in statements:
        s = _norm_statement(st)
        res = compute_bill(s["start"], s["end"], s["usage_kgal"], s["meter_size"])
        if res.subtotal != s["subtotal"] or res.days != s["days"]:
            bad.append(
                {
                    "start": s["start"].isoformat(),
                    "end": s["end"].isoformat(),
                    "printed": str(s["subtotal"]),
                    "computed": str(res.subtotal),
                    "days": s["days"],
                    "computed_days": res.days,
                }
            )
    return bad


def build_daily_cost_points(statements, daily_gallons, meter_size=DEFAULT_METER_SIZE):
    """Full lvvwd:water_cost series: statement anchors + open-period accrual.

    statements: list of {start, end, days, usage_kgal, subtotal[, bill_date]}
      (closed, bill-validated periods — PRINTED subtotal is the anchor value).
    daily_gallons: {date/ISO: gallons} from lvvwd:water_daily (may be None/{}).

    Returns [(local_date, state Decimal, sum Decimal), ...]:
      * one anchor point per statement at its period-END date (state = that
        period's subtotal, sum = running cumulative of printed subtotals);
      * one point per open-period day d >= current_period_start (state = the
        day's accrual delta — may be lumpy/slightly negative — and sum =
        anchor cumulative + cost_to_date(d)). The final accrual day equals the
        true bill of the open period to date. Daily data on/before the last
        statement end is ignored.
    """
    sts = sorted((_norm_statement(s) for s in statements), key=lambda s: s["end"])
    rows = []
    cum = D("0.00")
    for s in sts:
        cum += s["subtotal"]
        rows.append((s["end"], s["subtotal"], cum))
    if sts:
        pstart = sts[-1]["end"] + dt.timedelta(days=1)
    else:
        if not daily_gallons:
            return rows
        pstart = min(_as_date(k) for k in daily_gallons)
    prev = D("0.00")
    for d, ctd in accrue_period(pstart, daily_gallons, meter_size):
        rows.append((d, ctd - prev, cum + ctd))
        prev = ctd
    return rows
