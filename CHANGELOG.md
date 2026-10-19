# Changelog

All notable changes to this project are documented here. The format is based
on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

- **Tests: device lookups work on every harness CI resolves.** The weekly
  Validate run had failed since 2026-08-31: HA dev hard-errors the deprecated
  `async_get_device` in test frames. A `_get_device` helper uses
  `async_get_device_by_identifier` where the harness has it (HA 2026.8+) and
  falls back on the older harness the Python 3.13 leg resolves. No integration
  code changes; the integration itself never called the old API.
- **CI: new Minimum supported HA job.** It reads the floor from `hacs.json`
  and the integration's requirements from `manifest.json`, resolves every
  package as of the floor's release date (a fresh resolve of HA 2025.6 pulls a
  `pycares` too new for its `aiodns` and fails to import), and imports every
  module against that exact release. Import check only; this integration has no
  mypy configuration yet.
- **Known issue: statistics fail on HA 2025.6 through 2025.10.** `hacs.json`
  declares HA 2025.6.0, but the statistics metadata passes `unit_class`, which
  `StatisticMetaData` only gained in HA 2025.11. On older releases every
  statistics write raises `TypeError: 'unit_class' is an invalid keyword
  argument for StatisticsMeta` (three `test_statistics.py` tests fail against
  HA 2025.6.3 and 2025.10.4). Not yet corrected.

## [0.1.0] — 2026-08-24

Initial public release. (The section was drafted 2026-06-23; the date above is
the actual first GitHub release, which also picked up the per-account device
naming, the 2025 3/4" tariff columns, and the `country: US` declaration in
`hacs.json` — LVVWD serves the Las Vegas valley only, and the HACS default
store expects region-locked integrations to declare their countries.)

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
