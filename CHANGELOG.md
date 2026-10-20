# Changelog

All notable changes to this project are documented here. The format is based
on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] — 2026-10-20

- **Fixed: the cost statistic's history was wrong after about four weeks.**
  Every poll recomputed the cost from the portal's daily window alone and
  restarted the running total at $0 on the window's first day. The window
  slides forward each day, so every day that slid out kept a stale total,
  and the Energy dashboard showed roughly $0 a day for all but the last few
  weeks. The cost series is now append-only, like the daily usage series:
  each day is written once, continuing the stored total, and the open
  month's progress is saved between polls (`.storage/lvvwd.cost_<account>`,
  deleted when the entry is removed).
- **Changed: cost is priced by calendar month.** The portal gives no billing
  dates, so each calendar month is one bill. Its usage allowances (tier
  blocks, excessive-use threshold) are sized for the whole month from day
  one, so the running cost never falls; before, a growing day count could
  re-price earlier usage into cheaper tiers and give negative cost days. A
  month's last day equals that month's bill exactly. Existing cost rows are
  kept as they are, and new days continue from the last one.
- **Added: a Repairs issue when the portal can't be read.** Two scrapes in a
  row that fail to parse (LVVWD changed its pages) raise a *portal pages
  can't be read* issue telling you to update; a good scrape clears it. This
  completes the Gold quality-scale claim, whose `repair-issues` rule was
  still to do.
- **Added: a warning when pricing a year with no rate schedule.** LVVWD sets
  new rates each January 1; until an update adds them, that year is priced
  at the newest schedule the integration has, and the log says so once.
- **Changed: the *Data through* sensor is now a diagnostic entity**, as the
  quality-scale notes already said it was.
- Docs: README gains *Entities* and *Troubleshooting* sections and mentions
  *Reconfigure* for changing the password; `quality_scale.yaml` now cites
  sections that exist; stale comments in `api.py` and the tariff tests
  corrected.

## [0.2.1] — 2026-10-20

- **README brought up to date.** The `3/4"` schedule is validated against
  real statements too (since before 0.1.0), so the validation note and the
  limitation now say `1"` and `3/4"`. The cost option is listed under its
  real label, *Model billed cost*. Renaming the entry relabels the device but
  does not change entity IDs. The statistics page is under Settings → Tools
  since Home Assistant 2026.8. CONTRIBUTING.md and a `tariff.py` comment
  corrected to match. HACS shows the README from the latest release, which
  is why this ships as one. No integration code changes.

## [0.2.0] — 2026-10-18

- **Breaking: minimum Home Assistant raised to 2025.11.0 (was 2025.6.0).**
  This fixes statistics on the releases it drops: the statistics metadata
  declares `unit_class`, which `StatisticMetaData` only gained in HA 2025.11,
  so on HA 2025.6 through 2025.10 every statistics write raised `TypeError:
  'unit_class' is an invalid keyword argument for StatisticsMeta` and no water
  or cost history was recorded (the sensors still worked). Verified by running
  the full test suite against each release, with dependencies resolved as of
  its release date: all 54 tests pass on HA 2025.11.0, and three statistics
  tests fail on 2025.10.4 and 2025.6.3. On HA 2025.6 through 2025.10, stay on
  0.1.0 until you upgrade Home Assistant. The README now states the
  requirement.
- **Tests: device lookups work on every harness CI resolves.** The weekly
  Validate run had failed since 2026-08-31: HA dev hard-errors the deprecated
  `async_get_device` in test frames. A `_get_device` helper uses
  `async_get_device_by_identifier` where the harness has it (HA 2026.8+) and
  falls back on the older harness the Python 3.13 leg resolves. No integration
  code changes; the integration itself never called the old API.
- **CI: new Minimum supported HA job.** It reads the floor from `hacs.json`
  and the integration's requirements from `manifest.json`, resolves packages
  as of the floor's release date and restates HA's own exact pins (a fresh
  resolve of HA 2025.6 pulled a `pycares` too new for its `aiodns`), then
  imports every module and runs mypy in its default mode against that exact
  release. Default-mode mypy is what catches a TypedDict key the floor doesn't
  have: against HA 2025.6 it flags `unit_class`.

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

[0.3.0]: https://github.com/jasonjhofmann/lvvwd-homeassistant/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/jasonjhofmann/lvvwd-homeassistant/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/jasonjhofmann/lvvwd-homeassistant/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/jasonjhofmann/lvvwd-homeassistant/releases/tag/v0.1.0
