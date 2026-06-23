"""Client for the LVVWD ``myaccount.lvvwd.com`` customer portal.

Las Vegas Valley Water District has no public API. This module scrapes the
ColdFusion portal that customers log into, walking the same login + chart-token
chain a browser does and parsing the HTML fragments the chart endpoint returns
(usage values are encoded as HTML-entity-escaped Chartist JSON, in gallons).

This module intentionally has no Home Assistant imports so it stays importable
standalone (see ``scripts/smoke_test.py``) and unit-testable against captured
HTML fixtures. The coordinator runs the blocking ``requests`` calls via
``hass.async_add_executor_job``.

Portal mechanics (reverse-engineered):

* **Auth** — ``GET /?lang=en`` returns the login page; parse the ``token``
  hidden input (login CSRF) and the form ``action`` (``index.cfml?p=<token>``,
  NOT plain ``index.cfml``). ``POST`` the credentials + ``token`` to that
  action; a successful login lands on an authed page, a failure bounces back to
  ``index.cfml``. One ``requests.Session`` carries the ColdFusion session
  cookie.
* **CSRF** — every XHR needs ``X-Requested-With: XMLHttpRequest`` plus
  ``X-CSRFToken: <header data-token>`` parsed off the authed page.
* **Chart token** — ``GET water-usage.cfml`` carries the chart token as the
  ``data-id`` on ``<div id="WaterUsageChart">``; it becomes the ``p`` param for
  the chart endpoint. Each token is a server-side-encrypted blob.
* **Usage** — ``GET water-usage-chart.cfml`` with ``p``, ``obj=true``,
  ``type=detailed``, ``origin=init`` and ``interval=monthlyall`` (multi-year
  monthly comparison) or ``interval=daily`` (a 7-day window ending on ``date``;
  the ``#DailyDate`` input carries ``data-min-date``/``data-max-date`` bounding
  availability to roughly the last four weeks). Daily metas are ``Thu 5/28`` —
  no year — and are resolved against the window-end date.
"""

from __future__ import annotations

import datetime
import html
import json
import logging
import re
import time
from typing import Any

import requests

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://myaccount.lvvwd.com"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Network/per-request timeout for every portal call (seconds).
REQUEST_TIMEOUT = 30

# Pause between successive daily-window requests so the walk-back is gentle.
_DAILY_WINDOW_PAUSE = 0.4

_MONTHS = {
    name: index + 1
    for index, name in enumerate(
        [
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug",
            "Sep",
            "Oct",
            "Nov",
            "Dec",
        ]
    )
}


class LvvwdError(Exception):
    """Base error for all LVVWD client failures."""


class LvvwdAuthError(LvvwdError):
    """Login failed (bad credentials / bounced back to ``index.cfml``)."""


class LvvwdConnectionError(LvvwdError):
    """A network-level problem talking to the portal."""


class LvvwdParseError(LvvwdError):
    """The portal responded but its HTML did not match the expected shape."""


def parse_monthly(html_text: str) -> list[tuple[int, int, int]]:
    """Decode a ``interval=monthlyall`` fragment to sorted ``(year, month, gallons)``.

    The chart ``<div>`` carries ``data-series`` (HTML-entity-encoded Chartist
    JSON, ``[[{"meta": "Jun 2024", "value": "132380"}, ...], ...]``) and
    ``data-legend`` (e.g. ``[2024, 2025, 2026]``). Values are gallons; empty /
    no-data periods encode as a quoted-empty string and are skipped.
    """
    series_match = re.search(r'data-series="([^"]*)"', html_text)
    legend_match = re.search(r'data-legend="([^"]*)"', html_text)
    if not series_match or not legend_match:
        raise LvvwdParseError("monthly series/legend not found")
    try:
        series = json.loads(html.unescape(series_match.group(1)))
        legend = json.loads(html.unescape(legend_match.group(1)))
    except (ValueError, TypeError) as err:
        raise LvvwdParseError(f"monthly JSON decode failed: {err}") from err

    out: list[tuple[int, int, int]] = []
    for year_index, row in enumerate(series):
        try:
            year = int(legend[year_index])
        except (IndexError, ValueError, TypeError):
            continue
        for point in row:
            value = str(point.get("value", "")).strip().strip('"').strip()
            if not value.lstrip("-").isdigit():
                continue  # empty / no-data period
            month_name = (
                str(point.get("meta", "")).split()[0] if point.get("meta") else ""
            )
            if month_name in _MONTHS:
                out.append((year, _MONTHS[month_name], int(value)))
    out.sort()
    return out


def parse_daily(
    fragment: str,
) -> tuple[datetime.date, datetime.date, datetime.date, dict[str, int | None]]:
    """Decode one ``interval=daily`` fragment.

    Returns ``(min_date, max_date, end_date, {ISO-date: gallons | None})``. The
    ``#DailyDate`` input carries ``data-min-date`` / ``data-max-date`` (bounding
    availability) and ``value`` (the window end). Series metas are ``Thu 5/28``
    with no year, so each is matched against the 7-day window ending on the
    input value (year-boundary safe). Empty values decode to ``None``.
    """
    tag_match = re.search(r'<input[^>]*id="DailyDate"[^>]*>', fragment)
    if not tag_match:
        raise LvvwdParseError("daily fragment: #DailyDate input not found")
    tag = tag_match.group(0)

    def _attr(name: str) -> str:
        attr_match = re.search(rf'{re.escape(name)}="([^"]*)"', tag)
        if not attr_match:
            raise LvvwdParseError(f"daily fragment: missing #DailyDate attr {name}")
        return attr_match.group(1)

    try:
        min_date = datetime.date.fromisoformat(_attr("data-min-date"))
        max_date = datetime.date.fromisoformat(_attr("data-max-date"))
        end_date = datetime.date.fromisoformat(_attr("value"))
    except ValueError as err:
        raise LvvwdParseError(f"daily fragment: bad date attr ({err})") from err

    series_match = re.search(r'data-series="([^"]*)"', fragment)
    if not series_match:
        raise LvvwdParseError("daily fragment: data-series not found")
    try:
        series = json.loads(html.unescape(series_match.group(1)))
    except (ValueError, TypeError) as err:
        raise LvvwdParseError(f"daily JSON decode failed: {err}") from err

    window = [end_date - datetime.timedelta(days=offset) for offset in range(7)]
    out: dict[str, int | None] = {}
    for point in series[0] if series else []:
        meta_match = re.search(r"(\d{1,2})/(\d{1,2})", str(point.get("meta", "")))
        if not meta_match:
            continue
        month, day = int(meta_match.group(1)), int(meta_match.group(2))
        match = [d for d in window if d.month == month and d.day == day]
        if not match:
            continue
        value = str(point.get("value", "")).strip().strip('"').strip()
        out[match[0].isoformat()] = int(value) if value.lstrip("-").isdigit() else None
    return min_date, max_date, end_date, out


class LvvwdClient:
    """Synchronous scraping client for the LVVWD customer portal.

    One instance == one set of credentials. ``login()`` establishes an authed
    session; ``fetch_all()`` returns the monthly and daily usage series.
    """

    def __init__(
        self,
        username: str,
        password: str,
        base_url: str = BASE_URL,
        session: requests.Session | None = None,
    ) -> None:
        self._username = username
        self._password = password
        self._base_url = base_url.rstrip("/")
        self._session = session or requests.Session()
        self._session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        self._xhr_headers: dict[str, str] = {}
        self._chart_token: str | None = None

    # -- low-level helpers ---------------------------------------------------

    def _url(self, path: str) -> str:
        return f"{self._base_url}/{path.lstrip('/')}"

    def _get(self, path: str, **kwargs: Any) -> requests.Response:
        try:
            return self._session.get(self._url(path), timeout=REQUEST_TIMEOUT, **kwargs)
        except requests.RequestException as err:
            raise LvvwdConnectionError(f"GET {path} failed: {err}") from err

    def _post(self, url: str, **kwargs: Any) -> requests.Response:
        try:
            return self._session.post(url, timeout=REQUEST_TIMEOUT, **kwargs)
        except requests.RequestException as err:
            raise LvvwdConnectionError(f"POST failed: {err}") from err

    @staticmethod
    def _cache_buster() -> int:
        return int(time.time() * 1000)

    # -- auth ----------------------------------------------------------------

    def login(self) -> None:
        """Authenticate to the portal and prime the XHR/CSRF headers.

        Raises :class:`LvvwdAuthError` on a credential bounce, and
        :class:`LvvwdConnectionError` on a network failure.
        """
        # 1) Login page -> CSRF token + form action.
        resp = self._get("/?lang=en")
        login_page_url = resp.url
        token_match = re.search(
            r'name="token"[^>]*value="([^"]+)"', resp.text
        ) or re.search(r'value="([^"]+)"[^>]*name="token"', resp.text)
        action_match = re.search(r'<form[^>]*action="([^"]+)"', resp.text)
        if not token_match or not action_match:
            raise LvvwdParseError("login form/token not found")
        post_url = action_match.group(1)
        if not post_url.startswith("http"):
            post_url = self._url(post_url)

        # 2) Submit credentials to the form's own action.
        resp = self._post(
            post_url,
            data={
                "username": self._username,
                "password": self._password,
                "Remember": "1",
                "token": token_match.group(1),
            },
            headers={
                "Origin": self._base_url,
                "Referer": login_page_url,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        if "index.cfml" in resp.url:
            raise LvvwdAuthError("login failed (bounced to index.cfml)")

        # 3) Capture the XHR CSRF token off the authed page.
        csrf_match = re.search(r'data-token="([0-9A-Fa-f]+)"', resp.text)
        self._xhr_headers = {"X-Requested-With": "XMLHttpRequest", "Referer": resp.url}
        if csrf_match:
            self._xhr_headers["X-CSRFToken"] = csrf_match.group(1)

    def _ensure_chart_token(self) -> str:
        """GET ``water-usage.cfml`` -> chart token (``#WaterUsageChart data-id``)."""
        if self._chart_token:
            return self._chart_token
        resp = self._get(
            "/water-usage.cfml",
            params={"_": self._cache_buster()},
            headers=self._xhr_headers,
        )
        match = re.search(
            r'id="WaterUsageChart"[^>]*data-id="([0-9A-Fa-f]+)"', resp.text
        )
        if not match:
            raise LvvwdParseError("WaterUsageChart data-id not found")
        self._chart_token = match.group(1)
        return self._chart_token

    # -- usage ---------------------------------------------------------------

    def _fetch_monthly(self, chart_token: str) -> list[tuple[int, int, int]]:
        resp = self._get(
            "/water-usage-chart.cfml",
            params={
                "p": chart_token,
                "type": "detailed",
                "interval": "monthlyall",
                "origin": "init",
                "obj": "true",
                "_": self._cache_buster(),
            },
            headers=self._xhr_headers,
        )
        return parse_monthly(resp.text)

    def _fetch_daily_all(self, chart_token: str) -> dict[str, int | None]:
        """Walk 7-day windows (end = max, max-7, ...) back to ``data-min-date``."""
        base_params = {
            "p": chart_token,
            "type": "detailed",
            "interval": "daily",
            "origin": "init",
            "obj": "true",
        }
        resp = self._get(
            "/water-usage-chart.cfml",
            params={**base_params, "_": self._cache_buster()},
            headers=self._xhr_headers,
        )
        min_date, _max_date, end_date, days = parse_daily(resp.text)
        all_days: dict[str, int | None] = dict(days)
        end = end_date
        # A window ending at `end` covers down to end-6; the next end is end-7.
        while end - datetime.timedelta(days=7) >= min_date:
            end = end - datetime.timedelta(days=7)
            time.sleep(_DAILY_WINDOW_PAUSE)
            resp = self._get(
                "/water-usage-chart.cfml",
                params={
                    **base_params,
                    "date": end.isoformat(),
                    "date-display": end.strftime("%m/%d/%Y"),
                    "_": self._cache_buster(),
                },
                headers=self._xhr_headers,
            )
            _, _, _, days = parse_daily(resp.text)
            for iso, gallons in days.items():
                all_days.setdefault(iso, gallons)
        return all_days

    def fetch_all(
        self,
    ) -> tuple[list[tuple[int, int, int]], dict[str, int | None]]:
        """Log in (if needed) and return ``(monthly, daily)``.

        ``monthly`` is a sorted list of ``(year, month, gallons)`` back to
        service start. ``daily`` is ``{ISO-date: gallons | None}`` over the full
        available window (roughly the last four weeks; ``None`` == not yet
        posted).
        """
        if not self._xhr_headers:
            self.login()
        chart_token = self._ensure_chart_token()
        monthly = self._fetch_monthly(chart_token)
        daily = self._fetch_daily_all(chart_token)
        return monthly, daily
