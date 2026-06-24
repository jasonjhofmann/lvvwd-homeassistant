# Changelog

All notable changes to this project are documented here. The format is based
on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — 2026-06-23

Initial public release.

### Added

- **Usage statistics (Tier-0, any meter size).** Logs in to
  `myaccount.lvvwd.com`, scrapes the monthly and daily water-usage charts, and
  writes Home Assistant external statistics:
  - `lvvwd:<account>_water` — monthly usage (gallons), bucketed at the first of
    each month at local-Pacific midnight, full-series upsert.
  - `lvvwd:<account>_water_daily` — daily usage (gallons), one point per
    local-Pacific day at midnight, `has_sum=True` / `has_mean=False`,
    append-only cumulative anchored on the last stored row. The current Pacific
    calendar day is always skipped (it posts late/partial).

  Statistic IDs are account-scoped, so multiple LVVWD accounts on one Home
  Assistant instance never collide.
- **Penny-accurate tariff engine** (`tariff.py`, pure stdlib, no Home Assistant
  imports): the LVVWD residential rate schedule — daily service charge, the
  four-tier volumetric block, excessive-use threshold, SNWA commodity, SNWA
  daily infrastructure charge, and the reliability surcharge — including the
  Jan-1 rate-split for periods that straddle a rate change. Generalized across
  all supported meter sizes for the daily service and SNWA infrastructure
  charges using the published 2026 rate tables. The 2025 schedule also carries
  the 3/4" service/infra columns (recovered from a real 3/4" statement that
  straddled Jan 1 2026), so a 3/4"-meter period crossing that boundary
  reconciles instead of raising; all six validating statements match to the cent.
- **Optional cost statistic** (`lvvwd:<account>_water_cost`, USD), off by
  default. When enabled in options, the open-period daily accrual is written so
  it can be wired as the Energy dashboard water source's cost.
- **Config flow** with credential validation (test-before-configure),
  reauthentication, and an options flow for meter size and the cost opt-in.
  Multiple accounts are supported: each entry becomes its own device named
  after the account (`LVVWD (<username>)`), so a second account gets its own
  distinct entity IDs (`sensor.lvvwd_<username>_…`) instead of colliding on the
  shared `sensor.lvvwd_*` slugs and being suffixed `_2`.
- **Diagnostics** with the username and password redacted.
- In-tree brand assets (original water-droplet artwork), a gold-target quality
  self-assessment, and CI (hassfest, HACS, ruff, pytest on Python 3.13 + 3.14,
  and a PII gate).

### Notes

- The cost statistic is **opt-in**; the default install writes usage only.
- Default meter size is `0.625"` (the most common residential size). Cost for
  meter sizes other than `1"` is computed from the published 2026 rate tables
  but is **unvalidated against a real non-1" bill** until one is contributed
  (see CONTRIBUTING.md); pre-2026 periods are priced with the 1"-only schedule,
  which never affects go-forward 2026+ users.

[0.1.0]: https://github.com/jasonjhofmann/lvvwd-homeassistant/releases/tag/v0.1.0
