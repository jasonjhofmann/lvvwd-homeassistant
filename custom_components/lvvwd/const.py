"""Constants for the Las Vegas Valley Water District integration."""

from datetime import timedelta

from homeassistant.util import slugify

DOMAIN = "lvvwd"

# Options keys (config entry options, not data).
CONF_METER_SIZE = "meter_size"
CONF_ENABLE_COST = "enable_cost"

# Most common residential meter size; tier-0 usage-only is the default and
# cost is opt-in. Meter size only affects cost (service + SNWA infrastructure
# day-charges), so usage-only users never need to set it.
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
DEFAULT_ENABLE_COST = False

# The portal publishes usage with multi-day lag; a couple of polls per day is
# plenty (new days/months appear only when LVVWD posts them).
DEFAULT_UPDATE_INTERVAL = timedelta(hours=12)

ATTRIBUTION = "Data provided by Las Vegas Valley Water District (lvvwd.com)"

# Account-management portal the user logs into; also the device link.
CONFIGURATION_URL = "https://myaccount.lvvwd.com"

# External-statistic id suffixes (account-scoped; see statistic_id()).
STAT_SUFFIX_WATER = "water"
STAT_SUFFIX_WATER_DAILY = "water_daily"
STAT_SUFFIX_WATER_COST = "water_cost"


def statistic_id(unique_id: str, suffix: str) -> str:
    """Return the account-scoped external statistic id for a series.

    External statistics are written under ``<DOMAIN>:<slug>_<suffix>`` so a
    single Home Assistant can hold several LVVWD accounts without their
    statistics colliding. ``unique_id`` is the (already lower-cased) account
    username; it is slugified so it is always a legal statistic id tail.
    """
    return f"{DOMAIN}:{slugify(unique_id)}_{suffix}"
