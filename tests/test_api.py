"""Tests for the standalone LVVWD portal client (no Home Assistant)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

from custom_components.lvvwd.api import (
    LvvwdAuthError,
    LvvwdClient,
    LvvwdConnectionError,
    LvvwdParseError,
    parse_daily,
    parse_monthly,
)

from .conftest import load_fixture

MONTHLY_HTML = load_fixture("chart_monthlyall.html")
DAILY_HTML = load_fixture("chart_daily.html")

# Minimal synthetic login + chart-token pages (no real session tokens).
LOGIN_PAGE = (
    '<form action="index.cfml?p=SESSIONTOKEN">'
    '<input name="token" value="LOGINCSRF" />'
    "</form>"
)
# The portal's CSRF and chart tokens are hex blobs; the client's regexes
# require [0-9A-Fa-f]+, so the synthetic stand-ins are hex too.
AUTHED_PAGE = '<header data-token="abcdef0123456789">Welcome</header>'
WATER_USAGE_PAGE = '<div id="WaterUsageChart" data-id="0a1b2c3d4e5f">chart</div>'


# --- module-level parsers ---------------------------------------------------


def test_parse_monthly_skips_empty_periods() -> None:
    """Monthly parse returns sorted (year, month, gallons); empty periods drop."""
    result = parse_monthly(MONTHLY_HTML)
    assert result == sorted(result)  # already sorted
    assert (2024, 4, 41000) in result
    assert (2026, 5, 51000) in result
    # The empty 2026-Jun period is skipped, so no June 2026 entry exists.
    assert not any(y == 2026 and m == 6 for (y, m, _g) in result)


def test_parse_monthly_missing_series_raises() -> None:
    with pytest.raises(LvvwdParseError):
        parse_monthly("<div>no chart here</div>")


def test_parse_daily_resolves_dates_and_none() -> None:
    """Daily parse resolves year-less metas and maps empty values to None."""
    min_d, max_d, end_d, days = parse_daily(DAILY_HTML)
    assert min_d.isoformat() == "2026-05-21"
    assert max_d.isoformat() == "2026-06-17"
    assert end_d.isoformat() == "2026-06-17"
    assert days["2026-06-11"] == 1100
    assert days["2026-06-13"] is None  # empty value
    assert days["2026-06-17"] == 1420


def test_parse_daily_missing_input_raises() -> None:
    with pytest.raises(LvvwdParseError):
        parse_daily('<div class="ct-chart" data-series="[]"></div>')


# --- client login / fetch via a mocked session ------------------------------


class _FakeResponse:
    def __init__(self, url: str, text: str) -> None:
        self.url = url
        self.text = text

    def raise_for_status(self) -> None:
        return None


def _session_for(routes: dict[str, _FakeResponse]) -> MagicMock:
    """Build a MagicMock session whose GET/POST dispatch on a path substring."""

    def _dispatch(url: str, **_kwargs: object) -> _FakeResponse:
        for needle, resp in routes.items():
            if needle in url:
                return resp
        raise AssertionError(f"unexpected URL {url}")

    session = MagicMock(spec=requests.Session)
    session.headers = {}
    session.get.side_effect = _dispatch
    session.post.side_effect = _dispatch
    return session


def test_login_and_fetch_all_happy_path() -> None:
    """login() then fetch_all() walk the token chain and parse both charts."""
    base = "https://myaccount.lvvwd.com"
    routes = {
        "?lang=en": _FakeResponse(f"{base}/index.cfml?session=x", LOGIN_PAGE),
        "index.cfml?p=": _FakeResponse(f"{base}/account.cfml?p=acct", AUTHED_PAGE),
        "water-usage.cfml": _FakeResponse(f"{base}/water-usage.cfml", WATER_USAGE_PAGE),
    }
    # The chart endpoint returns monthly first, then daily windows. The daily
    # walk-back stops once the window reaches min-date (one window here).
    chart_responses = [
        _FakeResponse(f"{base}/water-usage-chart.cfml", MONTHLY_HTML),
        _FakeResponse(f"{base}/water-usage-chart.cfml", DAILY_HTML),
        _FakeResponse(f"{base}/water-usage-chart.cfml", DAILY_HTML),
        _FakeResponse(f"{base}/water-usage-chart.cfml", DAILY_HTML),
        _FakeResponse(f"{base}/water-usage-chart.cfml", DAILY_HTML),
    ]
    chart_iter = iter(chart_responses)

    def _dispatch(url: str, **_kwargs: object) -> _FakeResponse:
        if "water-usage-chart.cfml" in url:
            return next(chart_iter)
        for needle, resp in routes.items():
            if needle in url:
                return resp
        raise AssertionError(f"unexpected URL {url}")

    session = MagicMock(spec=requests.Session)
    session.headers = {}
    session.get.side_effect = _dispatch
    session.post.side_effect = _dispatch

    client = LvvwdClient("user", "pass", session=session)
    monthly, daily = client.fetch_all()

    assert (2026, 5, 51000) in monthly
    assert daily["2026-06-11"] == 1100
    assert daily["2026-06-13"] is None


def test_login_failure_bounces_to_index() -> None:
    """A login that lands back on index.cfml raises LvvwdAuthError."""
    base = "https://myaccount.lvvwd.com"
    routes = {
        "?lang=en": _FakeResponse(f"{base}/index.cfml?session=x", LOGIN_PAGE),
        # POST bounces back to index.cfml -> auth failure.
        "index.cfml?p=": _FakeResponse(f"{base}/index.cfml?failed=1", LOGIN_PAGE),
    }
    client = LvvwdClient("user", "bad", session=_session_for(routes))
    with pytest.raises(LvvwdAuthError):
        client.login()


def test_login_missing_token_raises_parse_error() -> None:
    base = "https://myaccount.lvvwd.com"
    routes = {"?lang=en": _FakeResponse(f"{base}/index.cfml", "<form></form>")}
    client = LvvwdClient("user", "pass", session=_session_for(routes))
    with pytest.raises(LvvwdParseError):
        client.login()


def test_network_error_wrapped() -> None:
    """A requests transport error becomes LvvwdConnectionError."""
    session = MagicMock(spec=requests.Session)
    session.headers = {}
    session.get.side_effect = requests.ConnectionError("boom")
    client = LvvwdClient("user", "pass", session=session)
    with pytest.raises(LvvwdConnectionError):
        client.login()


def test_missing_chart_token_raises() -> None:
    """An authed page without the WaterUsageChart data-id raises a parse error."""
    base = "https://myaccount.lvvwd.com"
    routes = {
        "?lang=en": _FakeResponse(f"{base}/index.cfml?session=x", LOGIN_PAGE),
        "index.cfml?p=": _FakeResponse(f"{base}/account.cfml", AUTHED_PAGE),
        "water-usage.cfml": _FakeResponse(
            f"{base}/water-usage.cfml", "<div>no chart</div>"
        ),
    }
    client = LvvwdClient("user", "pass", session=_session_for(routes))
    client.login()
    with pytest.raises(LvvwdParseError):
        client.fetch_all()
