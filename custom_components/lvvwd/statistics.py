"""External-statistics writers for the LVVWD integration.

Three account-scoped external statistics are maintained from one scrape:

* ``<acct>_water``       — MONTHLY consumption (gal), re-imported as a full
  idempotent upsert each run (no clear, so no metadata churn / dashboard
  flash).
* ``<acct>_water_daily`` — DAILY consumption (gal), APPEND-ONLY past the last
  stored row so overlapping re-imports keep their original cumulative sums.
* ``<acct>_water_cost``  — the modeled LVVWD statement (USD); written only
  when the user opts in. Statement anchors at each closed period end, plus a
  per-day accrual for the open period.

All buckets land at LOCAL Pacific midnight / first-of-month, converted to the
top of the hour in UTC (external statistics are hourly). The current Pacific
calendar day is always skipped (the portal posts it late/partial).

The last stored sum is read with the recorder's own
``get_last_statistics`` helper on the recorder executor — never raw sqlite —
so this stays correct across recorder backends.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.core import HomeAssistant

from . import tariff
from .const import (
    DEFAULT_METER_SIZE,
    DOMAIN,
    STAT_SUFFIX_WATER,
    STAT_SUFFIX_WATER_COST,
    STAT_SUFFIX_WATER_DAILY,
    statistic_id,
)

_LOGGER = logging.getLogger(__name__)

PACIFIC = ZoneInfo("America/Los_Angeles")

# Statistic name suffixes shown in the recorder UI (prefixed by the account
# username so multiple accounts stay distinguishable).
_NAME_WATER = "Water"
_NAME_WATER_DAILY = "Water (Daily)"
_NAME_WATER_COST = "Water Cost"


def _month_start_utc(year: int, month: int) -> datetime:
    """First-of-month 00:00 Pacific as a top-of-hour UTC datetime."""
    local = datetime(year, month, 1, tzinfo=PACIFIC)
    return local.astimezone(UTC)


def _day_start_utc(day: date) -> datetime:
    """Local-Pacific midnight of ``day`` as a top-of-hour UTC datetime."""
    local = datetime(day.year, day.month, day.day, tzinfo=PACIFIC)
    return local.astimezone(UTC)


async def _async_last_row(
    hass: HomeAssistant, stat_id: str
) -> tuple[float | None, datetime | None]:
    """Return ``(last_sum, last_start_utc)`` for a statistic, or ``(None, None)``.

    One recorder query yields both: ``start`` is always present on a row, and
    we request the ``sum`` value column. Runs on the recorder executor (never
    touches sqlite directly).
    """
    last = await get_instance(hass).async_add_executor_job(
        get_last_statistics, hass, 1, stat_id, True, {"sum"}
    )
    rows = last.get(stat_id)
    if not rows:
        return None, None
    row = rows[0]
    sum_value = row.get("sum")
    last_sum = float(sum_value) if sum_value is not None else None
    start = row.get("start")
    last_start = (
        datetime.fromtimestamp(float(start), tz=UTC) if start is not None else None
    )
    return last_sum, last_start


def _water_metadata(stat_id: str, name: str) -> StatisticMetaData:
    """Metadata for a gallons volume statistic."""
    return StatisticMetaData(
        has_mean=False,
        has_sum=True,
        mean_type=StatisticMeanType.NONE,
        name=name,
        source=DOMAIN,
        statistic_id=stat_id,
        unit_of_measurement="gal",
        unit_class="volume",
    )


def _cost_metadata(stat_id: str, name: str) -> StatisticMetaData:
    """Metadata for the USD cost statistic."""
    return StatisticMetaData(
        has_mean=False,
        has_sum=True,
        mean_type=StatisticMeanType.NONE,
        name=name,
        source=DOMAIN,
        statistic_id=stat_id,
        unit_of_measurement="USD",
        unit_class=None,
    )


def _build_monthly_rows(monthly: list[tuple[int, int, int]]) -> list[StatisticData]:
    """Full monthly series -> external-statistic rows (cumulative from zero).

    Re-imported in full every run: ``async_add_external_statistics`` upserts
    on ``start``, so re-adding the whole series is idempotent and avoids a
    clear (which would flash "Statistics not defined" on dashboards).
    """
    rows: list[StatisticData] = []
    cum = 0.0
    for year, month, gallons in monthly:
        cum += gallons
        rows.append(
            StatisticData(
                start=_month_start_utc(year, month),
                state=float(gallons),
                sum=round(cum, 1),
            )
        )
    return rows


def _build_daily_rows(
    daily: dict[str, int | None],
    last_sum: float | None,
    last_start: datetime | None,
    today_local: date,
) -> list[StatisticData]:
    """New daily rows only, continuing the cumulative from ``last_sum``.

    Drops days that are not yet posted (``None``), the current Pacific day
    (posted late/partial — a trailing 0 would poison the stat), and any day
    at/before the last stored row (append-only; stored sums stay untouched).
    """
    cum = last_sum or 0.0
    rows: list[StatisticData] = []
    for iso in sorted(daily):
        gallons = daily[iso]
        if gallons is None:
            continue
        day = date.fromisoformat(iso)
        if day >= today_local:
            continue
        start = _day_start_utc(day)
        if last_start is not None and start <= last_start:
            continue
        cum += gallons
        rows.append(StatisticData(start=start, state=float(gallons), sum=round(cum, 1)))
    return rows


def _build_cost_rows(
    points: list[tuple[date, Decimal, Decimal]],
) -> list[StatisticData]:
    """Cost points (local_date, delta, cumulative) -> external-statistic rows.

    Each point is bucketed at the local-Pacific midnight of its date. ``state``
    is the period/day amount (delta) and ``sum`` the running cumulative; both
    are coerced to float for the recorder.
    """
    rows: list[StatisticData] = []
    for day, state, cumulative in points:
        rows.append(
            StatisticData(
                start=_day_start_utc(day),
                state=float(state),
                sum=float(cumulative),
            )
        )
    return rows


async def async_write_statistics(
    hass: HomeAssistant,
    unique_id: str,
    monthly: list[tuple[int, int, int]],
    daily: dict[str, int | None],
    *,
    enable_cost: bool,
    meter_size: str = DEFAULT_METER_SIZE,
) -> None:
    """Write usage (and optionally cost) external statistics for one account."""
    today_local = datetime.now(PACIFIC).date()

    # --- monthly: full-series idempotent upsert ---
    if monthly:
        stat_id = statistic_id(unique_id, STAT_SUFFIX_WATER)
        rows = _build_monthly_rows(monthly)
        async_add_external_statistics(hass, _water_metadata(stat_id, _NAME_WATER), rows)
        _LOGGER.debug("Imported %d monthly point(s) into %s", len(rows), stat_id)

    # --- daily: append-only past the last stored row ---
    if daily:
        stat_id = statistic_id(unique_id, STAT_SUFFIX_WATER_DAILY)
        last_sum, last_start = await _async_last_row(hass, stat_id)
        rows = _build_daily_rows(daily, last_sum, last_start, today_local)
        if rows:
            async_add_external_statistics(
                hass, _water_metadata(stat_id, _NAME_WATER_DAILY), rows
            )
            _LOGGER.debug(
                "Appended %d daily point(s) into %s (sum now %.1f)",
                len(rows),
                stat_id,
                rows[-1]["sum"],
            )

    # --- cost: opt-in; modeled statement anchors + open-period accrual ---
    if enable_cost:
        await _async_write_cost(hass, unique_id, daily, meter_size, today_local)


async def _async_write_cost(
    hass: HomeAssistant,
    unique_id: str,
    daily: dict[str, int | None],
    meter_size: str,
    today_local: date,
) -> None:
    """Write the modeled cost statistic from the available daily series.

    Without printed statement records this models the open period from the
    earliest available daily reading. Days at/after the current Pacific day are
    excluded. The full series is re-emitted each run (idempotent upsert).
    """
    daily_gallons = {
        iso: gal
        for iso, gal in daily.items()
        if gal is not None and date.fromisoformat(iso) < today_local
    }
    if not daily_gallons:
        return

    period_start = min(date.fromisoformat(iso) for iso in daily_gallons)
    accrual = tariff.accrue_period(period_start, daily_gallons, meter_size=meter_size)
    if not accrual:
        return

    points: list[tuple[date, Decimal, Decimal]] = []
    prev = Decimal("0.00")
    for day, cost_to_date in accrual:
        points.append((day, cost_to_date - prev, cost_to_date))
        prev = cost_to_date

    stat_id = statistic_id(unique_id, STAT_SUFFIX_WATER_COST)
    rows = _build_cost_rows(points)
    async_add_external_statistics(hass, _cost_metadata(stat_id, _NAME_WATER_COST), rows)
    _LOGGER.debug(
        'Imported %d cost point(s) into %s (meter %s")',
        len(rows),
        stat_id,
        meter_size,
    )
