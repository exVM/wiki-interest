"""HTTP access to Wikimedia APIs with a local SQLite cache.

Two kinds of cached data:
  * daily pageview series, stored row-per-day with the contiguous date range
    already fetched, so repeated or extended queries only download the missing
    edges (pageviews for past days never change);
  * JSON metadata responses (Wikidata, redirects) with a TTL.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

import requests

SKILL_DIR = Path(__file__).resolve().parents[2]
# Analytics API reference: https://doc.wikimedia.org/generated-data-platform/aqs/analytics-api/reference/page-views.html
METRICS = "https://wikimedia.org/api/rest_v1/metrics"
PAGEVIEWS = f"{METRICS}/pageviews"
WIKIDATA = "https://www.wikidata.org/w/api.php"
# Wikimedia requires an identifying User-Agent; WIKITREND_CONTACT adds a contact.
USER_AGENT = "wiki-interest-skill/0.1 (Wikipedia pageview research; {}) python-requests".format(
    os.environ.get("WIKITREND_CONTACT", "https://agentskills.io; set WIKITREND_CONTACT")
)
META_TTL = 30 * 24 * 3600
FIRST_DAY = date(2015, 7, 1)  # earliest day the pageviews API serves

_local = threading.local()
_db_lock = threading.Lock()


class ApiError(RuntimeError):
    pass


def cache_path() -> Path:
    p = Path(os.environ.get("WIKITREND_CACHE", SKILL_DIR / ".cache" / "wikitrend.sqlite"))
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _db() -> sqlite3.Connection:
    conn = getattr(_local, "db", None)
    if conn is None:
        conn = sqlite3.connect(cache_path(), timeout=30)
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, body TEXT, fetched REAL);
            CREATE TABLE IF NOT EXISTS daily (series TEXT, day TEXT, views INTEGER,
                                              PRIMARY KEY (series, day));
            CREATE TABLE IF NOT EXISTS coverage (series TEXT PRIMARY KEY, start TEXT, end TEXT);
            """
        )
        _local.db = conn
    return conn


def _session() -> requests.Session:
    s = getattr(_local, "session", None)
    if s is None:
        s = requests.Session()
        s.headers["User-Agent"] = USER_AGENT
        _local.session = s
    return s


def http_json(url: str, params: dict | None = None) -> dict | None:
    """GET JSON with retries. Returns None on 404 (the pageviews API's 'no data')."""
    delay = 1.0
    attempts = 6
    for attempt in range(attempts):
        wait = delay
        try:
            r = _session().get(url, params=params, timeout=30)
        except requests.RequestException as e:
            if attempt == attempts - 1:
                raise ApiError(f"network error for {url}: {e}") from e
        else:
            if r.status_code == 404:
                return None
            if r.status_code == 200:
                return r.json()
            if r.status_code not in (429, 500, 502, 503, 504) or attempt == attempts - 1:
                raise ApiError(f"HTTP {r.status_code} for {r.url}: {r.text[:200]}"
                               + (" (rate limited: wait a minute and retry; cached data is kept)"
                                  if r.status_code == 429 else ""))
            retry_after = r.headers.get("Retry-After", "")
            if retry_after.isdigit():
                wait = min(float(retry_after), 60.0)
        time.sleep(wait)
        delay *= 2
    return None


def cached_json(url: str, params: dict | None = None, ttl: float = META_TTL) -> dict | None:
    key = url + "?" + json.dumps(params or {}, sort_keys=True)
    row = _db().execute("SELECT body, fetched FROM meta WHERE key=?", (key,)).fetchone()
    if row and time.time() - row[1] < ttl:
        return json.loads(row[0])
    data = http_json(url, params)
    with _db_lock:
        _db().execute("INSERT OR REPLACE INTO meta VALUES (?,?,?)", (key, json.dumps(data), time.time()))
        _db().commit()
    return data


def wikidata(params: dict) -> dict:
    data = cached_json(WIKIDATA, {"format": "json", **params})
    if data is None or "error" in data:
        raise ApiError(f"Wikidata error: {data and data.get('error')}")
    return data


def wiki_api(lang: str, params: dict) -> dict:
    data = cached_json(f"https://{lang}.wikipedia.org/w/api.php",
                       {"format": "json", "formatversion": "2", **params})
    if data is None:
        raise ApiError(f"{lang}.wikipedia API returned no data")
    return data


# --- daily pageviews -------------------------------------------------------

def last_complete_day() -> date:
    # Daily data is usually published within ~24h; stay two days behind to be safe.
    return date.today() - timedelta(days=2)


def _ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def _download(project: str, article: str | None, agent: str, start: date, end: date) -> dict[str, int]:
    if article is None:
        url = f"{PAGEVIEWS}/aggregate/{project}/all-access/{agent}/daily/{_ymd(start)}00/{_ymd(end)}00"
    else:
        title = quote(article.replace(" ", "_"), safe="")
        url = f"{PAGEVIEWS}/per-article/{project}/all-access/{agent}/{title}/daily/{_ymd(start)}00/{_ymd(end)}00"
    data = http_json(url)
    out: dict[str, int] = {}
    for item in (data or {}).get("items", []):
        ts = item["timestamp"]
        out[f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]}"] = int(item["views"])
    return out


def daily_views(project: str, article: str | None, start: date, end: date,
                agent: str = "user") -> dict[str, int]:
    """Daily views for an article (or the whole project if article is None).

    Days missing from the API response mean zero views (or the page not existing).
    Only the parts of [start, end] not already cached are downloaded.
    """
    start = max(start, FIRST_DAY)
    end = min(end, last_complete_day())
    if start > end:
        return {}
    series = f"{project}|{article if article is not None else '*TOTAL*'}|{agent}"
    db = _db()
    cov = db.execute("SELECT start, end FROM coverage WHERE series=?", (series,)).fetchone()
    todo: list[tuple[date, date]] = []
    if cov is None:
        todo.append((start, end))
        new_start, new_end = start, end
    else:
        cs, ce = date.fromisoformat(cov[0]), date.fromisoformat(cov[1])
        # Keep the cached range contiguous: extend it to cover the request.
        if start < cs:
            todo.append((start, cs - timedelta(days=1)))
        if end > ce:
            todo.append((ce + timedelta(days=1), end))
        new_start, new_end = min(start, cs), max(end, ce)
    if todo:
        # Download every missing edge before writing, so a failed request never
        # leaves the coverage record claiming days that were not stored.
        rows: dict[str, int] = {}
        for a, b in todo:
            rows.update(_download(project, article, agent, a, b))
        with _db_lock:
            db.executemany("INSERT OR REPLACE INTO daily VALUES (?,?,?)",
                           [(series, d, v) for d, v in rows.items()])
            db.execute("INSERT OR REPLACE INTO coverage VALUES (?,?,?)",
                       (series, new_start.isoformat(), new_end.isoformat()))
            db.commit()
    cur = db.execute("SELECT day, views FROM daily WHERE series=? AND day BETWEEN ? AND ?",
                     (series, start.isoformat(), end.isoformat()))
    return dict(cur.fetchall())


# --- monthly unique devices ------------------------------------
# Small monthly series, cached as JSON for a day (the newest month is published
# a few days after it ends, so a long TTL could freeze a missing month).
MONTHLY_TTL = 24 * 3600


def monthly_devices(project: str, start: date, end: date) -> dict[str, int]:
    """Unique devices per month for a whole project (all sites). Missing months are absent."""
    s, e = _ymd(date(start.year, start.month, 1)), _ymd(date(end.year, end.month, 1))
    data = cached_json(f"{METRICS}/unique-devices/{project}/all-sites/monthly/{s}/{e}", ttl=MONTHLY_TTL)
    return {f"{i['timestamp'][:4]}-{i['timestamp'][4:6]}": int(i["devices"]) for i in (data or {}).get("items", [])}


def cache_stats() -> dict:
    db = _db()
    return {
        "path": str(cache_path()),
        "series": db.execute("SELECT COUNT(*) FROM coverage").fetchone()[0],
        "daily_rows": db.execute("SELECT COUNT(*) FROM daily").fetchone()[0],
        "metadata_entries": db.execute("SELECT COUNT(*) FROM meta").fetchone()[0],
    }


def clear_cache() -> None:
    db = _db()
    with _db_lock:
        db.executescript("DELETE FROM meta; DELETE FROM daily; DELETE FROM coverage;")
        db.commit()
