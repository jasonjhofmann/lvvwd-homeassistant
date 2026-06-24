"""Tariff-engine tests driven by an INDEPENDENT longhand oracle.

The expected subtotals here are NOT taken from any real LVVWD statement. They
are produced by ``oracle_bill`` below — a deliberately separate, from-scratch
re-implementation of the published rate math (it shares no code with
``tariff.compute_bill``; it does not import any of its helpers). If the two
agree on a battery of synthetic periods, the engine reproduces the documented
algorithm. Inputs (periods, usage, meter sizes) are all invented.

Rate values come from the published 2026 LVVWD/SNWA residential schedule (and
the 2025 1"-only schedule) — public tariff data, not account data.
"""

from __future__ import annotations

import datetime as dt
import math
from decimal import ROUND_HALF_UP, Decimal

import pytest

from custom_components.lvvwd import tariff
from custom_components.lvvwd.tariff import (
    DEFAULT_METER_SIZE,
    SUPPORTED_METER_SIZES,
    accrue_period,
    compute_bill,
)

# --- Independent rate data (re-typed by hand, not imported from tariff.py) ---

_ORACLE_SERVICE = {
    2025: {"1": "0.6528", "0.75": "0.5168"},
    2026: {
        "0.625": "0.4627",
        "0.75": "0.5328",
        "1": "0.6730",
        "1.5": "1.0232",
        "2": "1.4442",
        "3": "2.5662",
        "4": "3.8284",
        "6": "7.3346",
        "8": "11.5421",
        "10": "16.4507",
        "12": "24.1644",
    },
}
_ORACLE_INFRA = {
    2025: {"1": "1.4431", "0.75": "0.545"},
    2026: {
        "0.625": "0.5657",
        "0.75": "0.5657",
        "1": "1.4979",
        "1.5": "2.9956",
        "2": "4.7932",
        "3": "9.5853",
        "4": "14.9770",
        "6": "29.9536",
        "8": "47.9252",
        "10": "53.5095",
        "12": "53.5095",
    },
}
_ORACLE_TIER_RATES = {
    2025: ("1.56", "2.78", "4.14", "6.14"),
    2026: ("1.61", "2.87", "4.27", "6.33"),
}
_ORACLE_COMMODITY = {2025: "0.64", 2026: "0.67"}
_ORACLE_DAILY_BLOCKS = (167, 334, 667)
_ORACLE_EXCESSIVE = "9.00"
_ORACLE_RELIABILITY = "0.0025"
_ORACLE_SEASON = {"winter": 467, "spring": 533, "summer": 933, "fall": 867}


def _cent(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _schedule_year(day: dt.date) -> int:
    # Latest Jan-1 schedule on/before this day; we model 2025 and 2026.
    return 2026 if day >= dt.date(2026, 1, 1) else 2025


def _season(month: int) -> str:
    if month in (11, 12, 1, 2):
        return "winter"
    if month in (3, 4):
        return "spring"
    if month in (5, 6, 7, 8):
        return "summer"
    return "fall"


def _split_jan1(start: dt.date, end: dt.date) -> list[tuple[dt.date, dt.date]]:
    subs, s = [], start
    while s <= end:
        e = min(end, dt.date(s.year, 12, 31))
        subs.append((s, e))
        s = e + dt.timedelta(days=1)
    return subs


def _round_half_up_frac(num: int, den: int) -> int:
    return (2 * num + den) // (2 * den)


def _prorate(usage_kgal: int, day_counts: list[int]) -> list[int]:
    total = sum(day_counts)
    out, cum_days, prev = [], 0, 0
    for nd in day_counts:
        cum_days += nd
        cum = _round_half_up_frac(usage_kgal * cum_days, total)
        out.append(cum - prev)
        prev = cum
    return out


def _tier_caps(n_days: int) -> tuple[int, int, int]:
    # round-NEAREST-1000 of (daily block * days); independent integer math.
    return tuple((blk * n_days + 500) // 1000 for blk in _ORACLE_DAILY_BLOCKS)


def _tier_split(usage_kgal: int, n_days: int) -> tuple[int, int, int, int]:
    caps = _tier_caps(n_days)
    units, prev = [], 0
    for cap in caps:
        units.append(max(0, min(usage_kgal, cap) - min(usage_kgal, prev)))
        prev = cap
    units.append(max(0, usage_kgal - caps[-1]))
    return tuple(units)


def _excessive_threshold(start: dt.date, end: dt.date) -> int:
    # Sum over contiguous season runs of CEIL(avg * days / 1000).
    total, d = 0, start
    runs: list[tuple[str, int]] = []
    while d <= end:
        name = _season(d.month)
        if runs and runs[-1][0] == name:
            runs[-1] = (name, runs[-1][1] + 1)
        else:
            runs.append((name, 1))
        d += dt.timedelta(days=1)
    for name, days in runs:
        total += math.ceil(_ORACLE_SEASON[name] * days / 1000)
    return total


def oracle_bill(
    start: dt.date, end: dt.date, usage_kgal: int, meter_size: str
) -> Decimal:
    """A from-scratch subtotal for [start..end] inclusive — the test oracle."""
    subs = _split_jan1(start, end)
    day_counts = [(e - s).days + 1 for s, e in subs]
    sub_usage = _prorate(usage_kgal, day_counts)

    service_total = Decimal("0.00")
    tiers_total = Decimal("0.00")
    commodity_total = Decimal("0.00")
    infra_total = Decimal("0.00")

    for (s, _e), nd, u in zip(subs, day_counts, sub_usage, strict=True):
        yr = _schedule_year(s)
        service_total += _cent(Decimal(_ORACLE_SERVICE[yr][meter_size]) * nd)
        infra_total += _cent(Decimal(_ORACLE_INFRA[yr][meter_size]) * nd)
        commodity_total += _cent(Decimal(_ORACLE_COMMODITY[yr]) * u)
        units = _tier_split(u, nd)
        for i in range(4):
            tiers_total += _cent(Decimal(_ORACLE_TIER_RATES[yr][i]) * units[i])

    threshold = _excessive_threshold(start, end)
    exc_units = max(0, usage_kgal - threshold)
    excessive = _cent(Decimal(_ORACLE_EXCESSIVE) * exc_units)

    base = service_total + tiers_total + excessive + commodity_total
    reliability = _cent(base * Decimal(_ORACLE_RELIABILITY))
    return base + infra_total + reliability


# --- Synthetic periods (invented dates/usage), 5/8" and 1" ------------------

# (start, end, usage_kgal, meter_size)
SYNTHETIC_CASES = [
    # Single-season, in-2026, default 5/8" meter.
    ("2026-03-02", "2026-03-31", 18, "0.625"),
    ("2026-05-04", "2026-06-02", 41, "0.625"),
    ("2026-06-10", "2026-07-09", 55, "0.625"),
    # Same usage on a 1" meter (higher service + infra day-charges).
    ("2026-05-04", "2026-06-02", 41, "1"),
    ("2026-06-10", "2026-07-09", 55, "1"),
    # Two-season span (spring -> summer) crosses the excessive-threshold seams.
    ("2026-04-15", "2026-05-20", 60, "0.625"),
    ("2026-04-15", "2026-05-20", 60, "1"),
    # Below excessive threshold (no surcharge line).
    ("2026-02-01", "2026-02-28", 9, "0.625"),
    # Heavy summer use (deep into tier 4 + a large excessive line).
    ("2026-07-01", "2026-07-31", 120, "1"),
    # Period straddling Jan 1 (2025 + 2026 schedules apply per sub-period).
    ("2025-12-18", "2026-01-19", 40, "1"),
    ("2025-12-10", "2026-01-12", 33, "1"),
    # Straddle on a 3/4" meter — exercises the 2025 3/4" service/infra columns.
    ("2025-12-09", "2026-01-08", 10, "0.75"),
]


@pytest.mark.parametrize(("start", "end", "usage", "meter"), SYNTHETIC_CASES)
def test_compute_bill_matches_oracle(
    start: str, end: str, usage: int, meter: str
) -> None:
    """compute_bill reproduces the independent oracle to the penny."""
    s = dt.date.fromisoformat(start)
    e = dt.date.fromisoformat(end)
    expected = oracle_bill(s, e, usage, meter)
    result = compute_bill(s, e, usage, meter)
    assert result.subtotal == expected, (
        f'{start}..{end} {usage} kgal {meter}": '
        f"engine {result.subtotal} != oracle {expected}"
    )
    assert result.days == (e - s).days + 1


def test_default_meter_size_is_five_eighths() -> None:
    """The engine default is the 5/8" residential meter, not 1"."""
    assert DEFAULT_METER_SIZE == "0.625"
    s, e = dt.date(2026, 5, 4), dt.date(2026, 6, 2)
    assert compute_bill(s, e, 41).subtotal == compute_bill(s, e, 41, "0.625").subtotal


def test_one_inch_costs_more_than_five_eighths() -> None:
    """The larger meter's bigger service + infra day-charges raise the total."""
    s, e = dt.date(2026, 5, 4), dt.date(2026, 6, 2)
    one_inch = compute_bill(s, e, 41, "1").subtotal
    five_eighths = compute_bill(s, e, 41, "0.625").subtotal
    assert one_inch > five_eighths


# --- Invariants -------------------------------------------------------------


def test_jan1_split_halves_sum_to_total() -> None:
    """A Jan-1 straddling period splits into two sub-periods whose prorated
    usage sums back to the whole-period usage (no kgal lost or invented)."""
    s, e, usage = dt.date(2025, 12, 18), dt.date(2026, 1, 19), 40
    result = compute_bill(s, e, usage, "1")
    assert len(result.sub_periods) == 2
    assert result.sub_periods[0].schedule_year == 2025
    assert result.sub_periods[1].schedule_year == 2026
    assert sum(sp.usage_kgal for sp in result.sub_periods) == usage
    # Sub-period day counts also partition the whole period.
    assert sum(sp.days for sp in result.sub_periods) == result.days


def test_single_season_period_has_one_subperiod() -> None:
    """A period inside one calendar year is not split."""
    result = compute_bill("2026-05-04", "2026-06-02", 41, "0.625")
    assert len(result.sub_periods) == 1
    assert result.sub_periods[0].schedule_year == 2026


def test_daily_accrual_deltas_sum_to_period_total() -> None:
    """The day-over-day cost-to-date deltas sum to the final period subtotal,
    and the final accrual value equals the full-period bill exactly."""
    period_start = dt.date(2026, 6, 1)
    # Invented daily gallons; mixes a None and crosses several 1000-gal seams.
    daily = {
        "2026-06-01": 1200,
        "2026-06-02": 980,
        "2026-06-03": None,  # treated as 0 but still advances the day count
        "2026-06-04": 1500,
        "2026-06-05": 1100,
        "2026-06-06": 1700,
        "2026-06-07": 1300,
    }
    accrual = accrue_period(period_start, daily, "0.625")
    assert accrual, "expected a non-empty accrual series"

    # Deltas telescope to the final cumulative.
    deltas = [accrual[0][1]]
    for prev, cur in zip(accrual, accrual[1:], strict=False):
        deltas.append(cur[1] - prev[1])
    assert sum(deltas) == accrual[-1][1]

    # The final day equals compute_bill on floor(total_gallons / 1000) units.
    total_gal = sum(v for v in daily.values() if v)
    units = int(total_gal // 1000)
    last_day = accrual[-1][0]
    expected_final = compute_bill(period_start, last_day, units, "0.625").subtotal
    assert accrual[-1][1] == expected_final


def test_accrue_period_empty_when_no_data() -> None:
    """No usable daily readings -> no accrual rows."""
    assert accrue_period(dt.date(2026, 6, 1), {}) == []
    # Only days before the period start -> still empty.
    assert accrue_period(dt.date(2026, 6, 10), {"2026-06-01": 900}) == []


def test_all_supported_meter_sizes_have_2026_rates() -> None:
    """Every supported size prices a 2026 period (no missing table entry)."""
    s, e = dt.date(2026, 6, 1), dt.date(2026, 6, 30)
    for size in SUPPORTED_METER_SIZES:
        result = compute_bill(s, e, 30, size)
        assert result.subtotal > 0
        assert result.subtotal == oracle_bill(s, e, 30, size)


def test_larger_meter_never_cheaper() -> None:
    """Service + infra day-charges are monotonic in meter size, so a bigger
    meter never produces a smaller bill for identical usage/period."""
    s, e, usage = dt.date(2026, 6, 1), dt.date(2026, 6, 30), 30
    totals = [
        compute_bill(s, e, usage, size).subtotal for size in SUPPORTED_METER_SIZES
    ]
    assert totals == sorted(totals)


def test_negative_usage_rejected() -> None:
    with pytest.raises(ValueError):
        compute_bill("2026-06-01", "2026-06-30", -1, "0.625")


def test_end_before_start_rejected() -> None:
    with pytest.raises(ValueError):
        compute_bill("2026-06-30", "2026-06-01", 10, "0.625")


def test_verify_statements_against_synthetic_oracle() -> None:
    """A synthetic statement built from the oracle reconciles to the penny via
    the engine's own verify_statements (no real bills involved)."""
    cases = [
        ("2026-05-04", "2026-06-02", 41, "0.625"),
        ("2025-12-18", "2026-01-19", 40, "1"),
    ]
    statements = [
        {
            "period_start": s,
            "period_end": e,
            "usage_kgal": u,
            "meter_size": m,
            "subtotal": str(
                oracle_bill(dt.date.fromisoformat(s), dt.date.fromisoformat(e), u, m)
            ),
        }
        for s, e, u, m in cases
    ]
    assert tariff.verify_statements(statements) == []
