# Contributing

## Architecture (5-minute tour)

```
custom_components/lvvwd/
  api.py           myaccount.lvvwd.com scraper (login + ColdFusion token chain +
                   monthly/daily HTML chart parse). NO Home Assistant imports —
                   synchronous requests, run from the coordinator on the
                   executor; testable standalone.
  tariff.py        Pure-stdlib penny-accurate residential bill engine (NO Home
                   Assistant imports). The rate tables mirror the published
                   LVVWD/SNWA schedules; keep them in sync each Jan 1.
  config_flow.py   Account flow (portal username/password, validated by a live
                   login) + reauth + options (meter size, cost opt-in).
  coordinator.py   One DataUpdateCoordinator per account. Polls every 12 hours,
                   writes usage statistics (and cost when enabled).
  statistics.py    Builds hour-aligned rows and calls async_add_external_statistics;
                   cumulative sums anchored via recorder get_last_statistics.
  entity.py        Base entity: device-per-account wiring.
  sensor.py        Latest-month and data-through sensors.
  diagnostics.py   Config-entry diagnostics (credentials redacted).
  brand/           Brand assets — generated, do not hand-edit; see scripts/.
  quality_scale.yaml  Self-assessment vs the core integration quality scale.
                   Keep statuses in sync with code changes.
```

Key invariants:

- **`api.py` and `tariff.py` stay free of Home Assistant imports** (api.py is a
  standalone scraper; tariff.py is a pure engine — both are unit-tested without
  HA).
- **Statistic IDs are account-scoped** (`lvvwd:<slug(username)>_<suffix>`), so
  two LVVWD accounts on one instance never collide.
- **`strings.json` and `translations/en.json` are kept identical** (copy on
  every change).
- **Never commit anything private.** `.lvvwd_secrets.json`, `samples/`, and
  `*.har` are gitignored. The CI **PII gate** fails the build if a known
  private token leaks into the tree — do not defeat it.

## Development setup

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements_test.txt
.venv/bin/python -m pytest tests -q --cov=custom_components.lvvwd
.venv/bin/python -m ruff check custom_components tests scripts
.venv/bin/python -m ruff format --check custom_components tests scripts
```

CI enforces ruff (lint + format), pytest with `--cov-fail-under=80` on Python
3.13 and 3.14, hassfest, HACS validation, and the PII gate. (mypy strict is a
Platinum goal deferred past v0.1 — see `quality_scale.yaml`.)

Brand assets regenerate with `python3 scripts/generate_brand.py` (Pillow). The
glyph is original artwork (a generic blue water droplet), **not** the LVVWD or
SNWA logo.

## Validating the tariff engine with a real bill (community contribution)

The cost engine is bill-accurate for the `1"` meter and validated to the penny
against the maintainer's own statements, but two cases are **not yet validated
from a real source**:

- **Non-`1"` meter sizes** — the daily service and SNWA infrastructure charges
  are taken from the published 2026 rate tables, but no real `5/8"` (the
  default), `3/4"`, `1.5"`, … bill has confirmed them end to end.
- **The fall season** (Sep/Oct) excessive-use average (867 gal/day) is from the
  official table but has not been seen on a real statement.

If you are an LVVWD residential customer and want to help close these gaps, you
can contribute the **numbers only** from a paper/PDF statement. Open an issue
with **exactly** these anonymized fields — and **nothing else** (no account
number, name, address, meter number, or bill PDF):

```json
{
  "meter_size": "0.625",
  "period_start": "2026-05-19",
  "period_end": "2026-06-17",
  "days": 30,
  "usage_kgal": 41,
  "printed_subtotal": "478.55"
}
```

`printed_subtotal` is the water-service subtotal *before* any sewer/refuse or
other district line items. That is enough for the engine to reproduce (or
correct) the bill; please do **not** attach the statement image or any
identifying detail.

## Making a release

1. Update `CHANGELOG.md` and bump `version` in `manifest.json` (manifest keys
   must stay sorted: `domain`, `name`, then alphabetical — hassfest enforces).
2. Commit, push, and wait for the Validate workflow to go **green**.
3. Tag and create the GitHub release **after** the green run (HACS submission
   rules require the release to post-date passing validation).
