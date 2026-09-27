"""Trend statistics and trust checks for pageview series.

Everything works on *average daily views per calendar month* (so February is
not penalised) computed from daily data. Methods are deliberately robust and
dependency-light:

  * Theil-Sen slope on log(views) -> % change per year, with Sen's 95% CI; the log
    offset is 1% of the series median, so the result does not depend on units
    (a +1 offset would flatten trends of small values such as views per million)
  * Mann-Kendall test for a monotonic trend
  * year-over-year change (last 12 months vs the 12 before), immune to seasonality
  * spike detection on daily data (robust z-score against a 29-day rolling median);
    the trend is recomputed with spikes capped to see if they drive the result
"""

from __future__ import annotations

import math
from collections import OrderedDict
from datetime import date, timedelta

import numpy as np

Z95 = 1.959964


def day_range(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def to_array(daily: dict[str, int], days: list[date]) -> np.ndarray:
    return np.array([daily.get(d.isoformat(), 0) for d in days], dtype=float)


def monthly_mean(days: list[date], values: np.ndarray) -> "OrderedDict[str, float]":
    """Average daily value for each calendar month."""
    sums: OrderedDict[str, list[float]] = OrderedDict()
    for d, v in zip(days, values):
        sums.setdefault(d.strftime("%Y-%m"), []).append(v)
    return OrderedDict((m, float(np.mean(v))) for m, v in sums.items())


def rolling_median(values: np.ndarray, window: int = 29) -> np.ndarray:
    half = window // 2
    padded = np.pad(values, half, mode="edge")
    view = np.lib.stride_tricks.sliding_window_view(padded, window)
    return np.median(view, axis=1)


def detect_spikes(days: list[date], values: np.ndarray) -> dict:
    """Find days far above the local baseline (news events, bots, Main Page features)."""
    if len(values) < 30 or values.sum() == 0:
        return {"share_of_views": 0.0, "events": [], "capped": values.copy(), "mask": np.zeros(len(values), bool)}
    base = rolling_median(values)
    r = np.log1p(values) - np.log1p(base)
    scale = 1.4826 * np.median(np.abs(r - np.median(r))) or 0.05
    mask = (r > np.median(r) + 5 * scale) & (values > 2 * base) & (values - base > 20)
    excess = np.where(mask, values - base, 0.0)
    capped = np.where(mask, base, values)
    events = []
    i = 0
    while i < len(mask):
        if mask[i]:
            j = i
            while j + 1 < len(mask) and mask[j + 1]:
                j += 1
            k = i + int(np.argmax(values[i:j + 1]))
            events.append({
                "start": days[i].isoformat(), "days": j - i + 1, "peak_day": days[k].isoformat(),
                "peak_views": int(values[k]), "x_baseline": round(float(values[k] / max(base[k], 1)), 1),
                "excess_views": int(excess[i:j + 1].sum()),
            })
            i = j + 1
        else:
            i += 1
    events.sort(key=lambda e: -e["excess_views"])
    return {
        "share_of_views": float(excess.sum() / values.sum()),
        "events": events[:5],
        "n_events": len(events),
        "capped": capped,
        "mask": mask,
    }


def mann_kendall(y: np.ndarray) -> tuple[float, float, float]:
    """Return (S, variance of S, two-sided p-value)."""
    n = len(y)
    diff = np.sign(y[None, :] - y[:, None])
    s = float(np.triu(diff, 1).sum())
    _, counts = np.unique(y, return_counts=True)
    var = (n * (n - 1) * (2 * n + 5) - sum(t * (t - 1) * (2 * t + 5) for t in counts)) / 18.0
    if var <= 0:
        return s, 0.0, 1.0
    z = (s - np.sign(s)) / math.sqrt(var)
    return s, var, math.erfc(abs(z) / math.sqrt(2))


def theil_sen(y: np.ndarray, var_s: float) -> tuple[float, float, float]:
    """Median pairwise slope with Sen's (1968) 95% confidence interval."""
    n = len(y)
    i, j = np.triu_indices(n, 1)
    slopes = np.sort((y[j] - y[i]) / (j - i))
    m = len(slopes)
    slope = float(np.median(slopes))
    c = Z95 * math.sqrt(max(var_s, 0))
    lo_idx = int(max(0, math.floor((m - c) / 2) - 1))
    hi_idx = int(min(m - 1, math.ceil((m + c) / 2)))
    return slope, float(slopes[lo_idx]), float(slopes[hi_idx])


def safe_log(values) -> np.ndarray:
    """log(x + c) with c = 1% of the median positive value: handles zero months without
    depending on the unit (views/day vs views per million give the same % trend)."""
    v = np.asarray(values, dtype=float)
    pos = v[v > 0]
    return np.log(v + (0.01 * float(np.median(pos)) if len(pos) else 1.0))


def trend(monthly: list[float]) -> dict:
    """Trend of monthly average daily views, expressed as % change per year."""
    y = safe_log(monthly)
    if len(y) < 6:
        return {"pct_per_year": None, "ci_low": None, "ci_high": None, "p_value": None, "r2": None}
    _, var_s, p = mann_kendall(y)
    slope, lo, hi = theil_sen(y, var_s)
    pct = lambda s: round((math.exp(12 * s) - 1) * 100, 1)  # noqa: E731
    x = np.arange(len(y))
    resid = y - (slope * x + np.median(y - slope * x))
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = max(0.0, 1 - float((resid ** 2).sum()) / ss_tot) if ss_tot > 0 else 0.0
    return {"pct_per_year": pct(slope), "ci_low": pct(lo), "ci_high": pct(hi), "p_value": round(p, 4),
            "r2": round(r2, 2)}


def period_change(monthly: list[float]) -> dict:
    """Last 12 months vs previous 12 (or last half vs first half if the window is short)."""
    n = len(monthly)
    if n >= 24:
        a, b, kind = monthly[-24:-12], monthly[-12:], "yoy"
    else:
        h = n // 2
        a, b, kind = monthly[:h], monthly[n - h:], "half_over_half"
    ma, mb = float(np.mean(a)) if a else 0.0, float(np.mean(b)) if b else 0.0
    return {"kind": kind, "pct": round((mb / ma - 1) * 100, 1) if ma > 0 else None,
            "before_avg_daily": round(ma, 1), "after_avg_daily": round(mb, 1), "abs_change": round(mb - ma, 1)}


def same_month_yoy(months: list[str], monthly: list[float]) -> dict | None:
    """Last month vs the same month a year earlier, and how many same-month pairs went up."""
    if len(monthly) < 13:
        return None
    pairs = [(monthly[i - 12], monthly[i]) for i in range(12, len(monthly)) if monthly[i - 12] > 0]
    a, b = monthly[-13], monthly[-1]
    return {"month": months[-1], "vs_month": months[-13],
            "pct": round((b / a - 1) * 100, 1) if a > 0 else None,
            "months_up": sum(1 for x, y in pairs if y > x), "months_compared": len(pairs)}


def recent_shift(monthly: list[float]) -> float | None:
    """Last 3 months vs the same 3 months a year earlier, in % (seasonality-neutral).

    Catches a recent jump or drop that a 24-month slope does not show yet."""
    if len(monthly) < 15:
        return None
    a, b = float(np.mean(monthly[-15:-12])), float(np.mean(monthly[-3:]))
    return round((b / a - 1) * 100, 1) if a > 0 else None


def describe(months: list[str], monthly: list[float]) -> dict:
    """Volume and stability of the monthly series (average daily views per month)."""
    v = np.asarray(monthly, dtype=float)
    i_max, i_min = int(np.argmax(v)), int(np.argmin(v))
    mom = np.diff(safe_log(v))
    vol = float(np.std(mom) * 100) if len(mom) >= 2 else None
    return {
        "median": round(float(np.median(v)), 1),
        "peak": {"month": months[i_max], "value": round(float(v[i_max]), 1)},
        "min": {"month": months[i_min], "value": round(float(v[i_min]), 1)},
        "last_month": {"month": months[-1], "value": round(float(v[-1]), 1)},
        "last_3m": round(float(v[-3:].mean()), 1),
        "cv_pct": round(float(v.std() / v.mean() * 100), 0) if v.mean() > 0 else None,
        # typical month-to-month change; seasonal cycles count as volatility too
        "mom_volatility_pct": None if vol is None else round(vol, 0),
        "stability": None if vol is None else "stable" if vol < 15 else "moderate" if vol < 35 else "volatile",
    }


def detect_dips_and_gaps(days: list[date], values: np.ndarray) -> dict:
    """Flag (never remove) days far below the local baseline, and runs of zero-view days
    inside an article's life that look like missing data, outages or page moves."""
    out: dict = {"dips": [], "gaps": [], "gap_days": 0}
    if len(values) < 30 or values.sum() == 0:
        return out
    base = rolling_median(values)
    r = np.log1p(values) - np.log1p(base)
    scale = 1.4826 * np.median(np.abs(r - np.median(r))) or 0.05
    first = int(np.nonzero(values)[0][0])
    zero = (values == 0) & (np.arange(len(values)) >= first)
    dip = (r < np.median(r) - 5 * scale) & (values < 0.5 * base) & (base - values > 20) & ~zero
    for mask, key in ((dip, "dips"), (zero, "gaps")):
        i = 0
        while i < len(mask):
            if not mask[i]:
                i += 1
                continue
            j = i
            while j + 1 < len(mask) and mask[j + 1]:
                j += 1
            n = j - i + 1
            if key == "dips":
                b = float(np.median(base[i:j + 1]))
            else:  # the rolling median inside a 15+ day zero run is 0 itself: look around the run
                around = values[max(first, i - 29):i]
                if len(around) < 7:
                    around = values[j + 1:j + 30]
                b = float(np.median(around)) if len(around) else 0.0
            if key == "dips":
                out["dips"].append({"start": days[i].isoformat(), "days": n,
                                    "min_views": int(values[i:j + 1].min()), "baseline": round(b, 0)})
            elif n >= 3 and b >= 3:  # 3+ zero days where ~3+ views/day are normal: not chance
                out["gaps"].append({"start": days[i].isoformat(), "days": n, "baseline": round(b, 0)})
                out["gap_days"] += n
            i = j + 1
    out["dips"] = out["dips"][:5]
    return out


MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August",
               "September", "October", "November", "December"]


def seasonality(months: list[str], monthly: list[float]) -> dict | None:
    """Correlation of the month-of-year pattern between the last two years."""
    if len(monthly) < 24:
        return None
    y = safe_log(monthly[-24:])
    if np.ptp(y) == 0:
        return None
    x = np.arange(24)
    y = np.exp(y - np.polyval(np.polyfit(x, y, 1), x))  # remove the trend: a steady decline is not a cycle
    y1, y2 = y[:12], y[12:]
    if y1.std() == 0 or y2.std() == 0:
        return None
    i1, i2 = y1 / y1.mean(), y2 / y2.mean()
    r = float(np.corrcoef(i1, i2)[0, 1])
    idx = (i1 + i2) / 2
    peak = MONTH_NAMES[int(months[-12:][int(np.argmax(idx))][5:]) - 1]
    low = MONTH_NAMES[int(months[-12:][int(np.argmin(idx))][5:]) - 1]
    return {"repeat_correlation": round(r, 2), "peak_month": peak, "low_month": low,
            "amplitude_pct": round(float((idx.max() - idx.min()) * 100), 0),
            "strong": bool(r >= 0.6 and idx.max() - idx.min() >= 0.25)}


def direction(t: dict) -> str:
    if t["pct_per_year"] is None:
        return "insufficient data"
    if t["ci_low"] > 0:
        return "rising"
    if t["ci_high"] < 0:
        return "falling"
    return "no clear trend"


def _assess_one(m: dict, t: dict, tx: dict, pc: dict, rs: float | None = None) -> dict:
    verdict = direction(t)
    checks: list[dict] = []

    def check(name: str, ok: bool, note: str) -> None:
        checks.append({"check": name, "ok": bool(ok), "note": note})

    check("enough_volume", m["avg_daily_views"] >= 30,
          f"{m['avg_daily_views']:.0f} views/day on average" +
          ("" if m["avg_daily_views"] >= 30 else " - small numbers are noisy"))
    check("full_coverage", m["coverage"] >= 0.9,
          f"article has data on {m['coverage'] * 100:.0f}% of the window's days"
          + ("" if m["coverage"] >= 0.9 else " (created/renamed mid-window, or data gaps?)"))
    if verdict in ("rising", "falling"):
        sign = 1 if verdict == "rising" else -1
        check("statistically_significant", t["p_value"] < 0.05, f"Mann-Kendall p={t['p_value']}")
        check("period_change_agrees", pc["pct"] is not None and pc["pct"] * sign >= 5,
              f"{pc['kind']} change {pc['pct']}%")
        check("not_driven_by_spikes", direction(tx) == verdict and m["spikes"]["share_of_views"] < 0.2,
              f"{m['spikes']['share_of_views'] * 100:.0f}% of views are spike excess; "
              f"trend without spikes {tx['pct_per_year']}%/yr")
        seas = m.get("seasonality")
        if m["n_months"] < 24:
            check("seasonality_handled", False, f"only {m['n_months']} months - seasonality cannot be ruled out")
        elif seas and seas["strong"]:
            check("seasonality_handled", True, f"seasonal (peak month {seas['peak_month']}), window covers 2+ cycles")
    else:
        sign = 0
        width = (t["ci_high"] - t["ci_low"]) if t["ci_high"] is not None else 999
        check("precise_estimate", width <= 20, f"95% CI {t['ci_low']}..{t['ci_high']}%/yr")
    if rs is not None and verdict != "insufficient data":
        # a big recent move that the trend verdict does not show (or contradicts), spikes capped
        ok = abs(rs) < 20 or (sign != 0 and rs * sign > 0)
        check("recent_level_consistent", ok,
              f"last 3 months {'+' if rs >= 0 else ''}{rs}% vs the same months a year earlier (spikes capped)"
              + ("" if ok else " - a recent shift the trend verdict does not show yet"))
    failed = sum(not c["ok"] for c in checks)
    return {"verdict": verdict, "confidence": "high" if failed == 0 else "medium" if failed == 1 else "low",
            "checks": checks}


def assess(m: dict) -> dict:
    """Verdicts for absolute views and for share of the wiki's traffic.

    Absolute views answer "are more people reading this?"; share answers "is this
    topic gaining attention relative to everything else in that language?", which
    removes platform-wide shifts (e.g. search/AI answers taking Wikipedia traffic)."""
    return {
        "absolute": _assess_one(m, m["trend"], m["trend_ex_spikes"], m["period_change"], m.get("recent_shift")),
        "share": _assess_one(m, m["trend_normalized"], m["trend_normalized_ex_spikes"],
                             m["period_change_normalized"], m.get("recent_shift_normalized")),
    }
