# Las Vegas Valley Water District (LVVWD)

[![GitHub release](https://img.shields.io/github/v/release/jasonjhofmann/lvvwd-homeassistant?include_prereleases)](https://github.com/jasonjhofmann/lvvwd-homeassistant/releases)
[![Validate](https://github.com/jasonjhofmann/lvvwd-homeassistant/actions/workflows/validate.yml/badge.svg)](https://github.com/jasonjhofmann/lvvwd-homeassistant/actions/workflows/validate.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![License](https://img.shields.io/github/license/jasonjhofmann/lvvwd-homeassistant)](LICENSE)

Home Assistant integration that brings your **Las Vegas Valley Water District**
water usage into the **Energy dashboard**. LVVWD has no public API, so this
integration logs in to your `myaccount.lvvwd.com` account and reads the same
monthly and daily usage charts you see in the portal, then writes them as Home
Assistant long-term **statistics**.

> **Unofficial.** This project is not affiliated with, endorsed by, or
> supported by the Las Vegas Valley Water District or the Southern Nevada Water
> Authority. It works by automating your own login to your own account; using
> it may be subject to the LVVWD website terms of service — use it at your own
> discretion. Brand artwork here is an original water-droplet glyph, not the
> LVVWD/SNWA logo.

## What you get

- **Water usage in the Energy dashboard.** A daily-resolution usage statistic
  (gallons) you can add as a *Water* source, plus a monthly series for history.
- **Optional, penny-accurate cost** (USD), off by default. When enabled, the
  integration computes what LVVWD would bill for the open period — the daily
  service charge, four-tier volumetric block, excessive-use threshold, SNWA
  commodity and infrastructure charges, and the reliability surcharge — so the
  Energy dashboard can show dollars, not just gallons.

## Installation

Until this repo is in the HACS default store, add it as a custom repository:

1. HACS → ⋮ → *Custom repositories* → add
   `https://github.com/jasonjhofmann/lvvwd-homeassistant` (type: Integration).
2. Install **Las Vegas Valley Water District** and restart Home Assistant.

## Configuration

Settings → Devices & Services → Add Integration → **Las Vegas Valley Water
District**.

Enter your **portal username and password** — the same credentials you use at
`myaccount.lvvwd.com` (the username is your login name, *not* your email). They
are validated by a live login before the entry is created, then stored by Home
Assistant. There is no second factor on the portal.

### Parameters

| Parameter | Description |
| --- | --- |
| Username | Your `myaccount.lvvwd.com` login name |
| Password | Your `myaccount.lvvwd.com` password |

### Options

Open the integration → **Configure**:

| Option | Default | Description |
| --- | --- | --- |
| Meter size | `0.625"` (5/8", the most common residential size) | Sets the daily service and SNWA infrastructure charges used for cost. Find yours on a paper/PDF bill or on the meter. |
| Enable cost statistic | Off | When on, also writes the `…_water_cost` USD statistic. Usage statistics are always written regardless. |

If your password is rejected later (changed or expired), Home Assistant prompts
for **reauthentication** automatically.

## Statistics it creates

Statistic IDs are **account-scoped** — they include a slug of your username, so
multiple LVVWD accounts on one Home Assistant instance never collide. With a
username of `johndoe` you get:

| Statistic | Unit | Cadence | Notes |
| --- | --- | --- | --- |
| `lvvwd:johndoe_water` | gal | monthly | One point per month at the first of the month, local-Pacific midnight; full-series upsert. |
| `lvvwd:johndoe_water_daily` | gal | daily | One point per local-Pacific day at midnight; `has_sum=True`, `has_mean=False`. Append-only cumulative. The current Pacific day is skipped (it posts late/partial). |
| `lvvwd:johndoe_water_cost` | USD | daily | Only when the cost option is enabled. Open-period cost-to-date accrual; meant as the Energy water source's cost. |

### Wire it into the Energy dashboard

Settings → Dashboards → **Energy** → *Water consumption* → **Add water
source**, and pick **`lvvwd:<your-username>_water_daily`**. If you enabled the
cost option, set its associated cost to **Use an entity tracking the total
costs** and choose **`lvvwd:<your-username>_water_cost`**.

> **Cold-start note.** Right after install the statistics may be empty for a few
> minutes until the first poll finishes, and the Energy dashboard only lists a
> statistic once it has data — so add the water source **after** you see the
> daily statistic populate (Developer Tools → Statistics, or wait one poll).
> The first import backfills the full available daily window (~4 weeks) and the
> monthly series.

## How the data updates

The integration polls every **12 hours**. The portal's monthly chart changes
only once per billing cycle, and the daily chart exposes only roughly the last
**4 weeks** and lags "today" by about **3 days**, so more frequent polling buys
nothing. The current Pacific calendar day is always skipped because it posts
late and partial.

## Meter size and cost tiers

Cost is opt-in. When enabled, the bill is computed from the published 2026
LVVWD/SNWA residential rate tables:

- **Daily service charge** and **SNWA daily infrastructure charge** depend on
  your **meter size** (set it in Options). Supported sizes:
  `5/8", 3/4", 1", 1.5", 2", 3", 4", 6", 8", 10", 12"`.
- The **four-tier volumetric** block, the **excessive-use** threshold, and the
  **SNWA commodity** rate are the same for every residential meter size.
- Periods that straddle **January 1** are split at the rate change and priced on
  each side with that year's schedule.

The `1"` schedule is validated to the penny against real statements. Other meter
sizes use the published table values but are **unvalidated from a real bill**
(see *Limitations*).

## Limitations

- **Short daily history.** The portal only exposes ~4 weeks of daily data and
  lags ~3 days; there is no sub-daily data and no deep history. Home Assistant
  keeps everything it imports going forward.
- **No MFA / scraping fragility.** Login is single-step with no second factor.
  Because this reads HTML the portal renders, a portal redesign can break
  parsing until the integration is updated.
- **Non-`1"` cost is unvalidated from source.** Cost for meter sizes other than
  `1"` is computed from the published 2026 rate tables but has not been
  confirmed against a real non-`1"` bill. If you can help, see
  [CONTRIBUTING.md](CONTRIBUTING.md) — the anonymized numbers from one
  statement are enough. Pre-2026 periods are priced with the `1"`-only schedule,
  which never affects go-forward 2026+ users.
- **Unofficial / terms of service.** See the disclaimer at the top — this is not
  an official LVVWD product and automating the portal login may be subject to
  its terms.

## Removal

1. Settings → Devices & Services → **Las Vegas Valley Water District** → delete
   the entry (its device and entities are removed automatically).
2. Uninstall the integration from HACS and restart Home Assistant.
3. Long-term statistics persist by design; remove them from Developer Tools →
   Statistics if you no longer want the history.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) — architecture tour, dev setup, quality
gates (pytest + coverage, ruff), the PII gate, and how to contribute a real
bill to validate non-`1"` meters.

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements_test.txt
.venv/bin/python -m pytest tests -q --cov=custom_components.lvvwd
```
