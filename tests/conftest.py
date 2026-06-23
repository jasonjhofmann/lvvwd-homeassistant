"""Fixtures for the LVVWD tests.

All fixtures here are SYNTHETIC. The HTML in ``tests/fixtures/`` is hand-built
to the portal's documented "Response format" (HTML-entity-encoded Chartist
JSON), with invented gallons and no real session tokens; the usage numbers and
credentials below are likewise made up.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.lvvwd.const import (
    CONF_ENABLE_COST,
    CONF_METER_SIZE,
    DOMAIN,
)

FIXTURES = Path(__file__).parent / "fixtures"

# Synthetic credentials (never a real account login name).
TEST_USERNAME = "testuser"
TEST_PASSWORD = "hunter2"

# Synthetic scrape result matching what LvvwdClient.fetch_all() returns:
#   monthly = sorted [(year, month, gallons), ...]
#   daily   = {ISO-date: gallons | None}
SAMPLE_MONTHLY: list[tuple[int, int, int]] = [
    (2026, 4, 44000),
    (2026, 5, 51000),
    (2026, 6, 58000),
]
SAMPLE_DAILY: dict[str, int | None] = {
    "2026-06-11": 1100,
    "2026-06-12": 980,
    "2026-06-13": None,  # not yet posted
    "2026-06-14": 1350,
    "2026-06-15": 1200,
    "2026-06-16": 1500,
}


def load_fixture(name: str) -> str:
    """Read a sanitized HTML fixture from ``tests/fixtures/``."""
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(request: pytest.FixtureRequest) -> Iterator[None]:
    """Enable loading custom integrations in all tests.

    ``enable_custom_integrations`` pulls in ``hass``; the recorder test harness
    requires its own fixtures to resolve *before* ``hass`` is set up (PT-HACC's
    ``recorder_db_url`` asserts ``not hass_fixture_setup``). The integration
    declares ``recorder`` as a manifest dependency, so any test that brings up
    ``hass`` must set the in-memory recorder up first; pure tariff/api tests use
    neither and are left untouched.
    """
    if "recorder_mock" in request.fixturenames or "hass" in request.fixturenames:
        request.getfixturevalue("recorder_mock")
    request.getfixturevalue("enable_custom_integrations")
    yield


@pytest.fixture
def mock_client() -> Iterator[MagicMock]:
    """Patch ``LvvwdClient`` everywhere the integration constructs it.

    Both the config flow (``config_flow.LvvwdClient``) and entry setup
    (the top-level ``custom_components.lvvwd.LvvwdClient`` imported in
    ``__init__``) get the same instance, so ``login()`` and ``fetch_all()``
    can be steered from one place.
    """
    client = MagicMock()
    client.login.return_value = None
    client.fetch_all.return_value = (SAMPLE_MONTHLY, SAMPLE_DAILY)
    with (
        patch(
            "custom_components.lvvwd.config_flow.LvvwdClient",
            return_value=client,
        ),
        patch(
            "custom_components.lvvwd.LvvwdClient",
            return_value=client,
        ),
    ):
        yield client


def make_entry(
    *,
    username: str = TEST_USERNAME,
    password: str = TEST_PASSWORD,
    meter_size: str | None = None,
    enable_cost: bool | None = None,
) -> MockConfigEntry:
    """Build an LVVWD account MockConfigEntry.

    ``unique_id`` mirrors the production rule (``username.strip().lower()``);
    options are only set when explicitly provided so default-path tests see an
    empty options dict.
    """
    options: dict[str, object] = {}
    if meter_size is not None:
        options[CONF_METER_SIZE] = meter_size
    if enable_cost is not None:
        options[CONF_ENABLE_COST] = enable_cost
    return MockConfigEntry(
        domain=DOMAIN,
        title=f"LVVWD ({username})",
        data={CONF_USERNAME: username, CONF_PASSWORD: password},
        options=options,
        unique_id=username.strip().lower(),
    )
