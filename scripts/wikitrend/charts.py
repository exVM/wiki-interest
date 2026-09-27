"""Static charts (PNG) for analyses and reports.

chart.png - two panels, never a dual axis: monthly views on the left (absolute,
or indexed with --relative), share of the language edition's traffic indexed on
the right (comparable across languages of very different size).
compare.png - bar panels per series: audience size, change vs the previous
period (views and share), and month-to-month volatility.
Colours follow series order, not rank, and are the same in both files.
"""

from __future__ import annotations

import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

# Validated categorical palette (fixed order); max 8 series per chart.
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e4e3df"
MAX_SERIES = 8


def series_label(s: dict, res: dict) -> str:
    n_topics = len(res["topics"])
    n_langs = len(res["params"]["langs"])
    if n_topics == 1:
        return s["lang"]
    if n_langs == 1:
        return s["topic"]
    return f"{s['topic']} · {s['lang']}"


def index_span(n_months: int) -> int:
    """Months averaged as the index base: a full year cancels seasonality when there is room."""
    return 12 if n_months >= 24 else 3


def index_base_text(n_months: int) -> str:
    return f"average of the first {index_span(n_months)} months = 100"


def _index(vals: list[float]) -> list[float | None]:
    nz = [v for v in vals if v > 0]
    if len(nz) < 3:
        return [None] * len(vals)
    first = next(i for i, v in enumerate(vals) if v > 0)
    span = index_span(len(vals))
    base = sum(vals[first:first + span]) / span or 1
    return [v / base * 100 if i >= first else None for i, v in enumerate(vals)]


def _style() -> None:
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8.5, "axes.edgecolor": GRID,
                         "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2})


def _month_ticks(ax, months: list[str]) -> None:
    step = max(1, math.ceil(len(months) / 6))  # at most ~6 labels per panel so they never touch
    x = list(range(len(months)))
    ax.set_xticks(x[::step], months[::step], rotation=0)


def trend_chart(res: dict, path: Path) -> str | None:
    ok = [s for s in res["series"] if s["status"] == "ok"]
    if not ok:
        return None
    shown = ok[:MAX_SERIES]
    relative = bool(res["params"].get("relative"))
    _style()
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 3.6), dpi=200)
    months = shown[0]["monthly"]["months"]
    x = list(range(len(months)))
    nan = float("nan")
    left = [[nan if v is None else v for v in _index(s["monthly"]["avg_daily_views"])] if relative
            else s["monthly"]["avg_daily_views"] for s in shown]
    vmax = max(max(s["monthly"]["avg_daily_views"]) for s in shown)
    vmin = min(max(min(s["monthly"]["avg_daily_views"]), 0.5) for s in shown)
    log = not relative and vmax / vmin > 20
    for i, s in enumerate(shown):
        c, lbl = PALETTE[i], series_label(s, res)
        ax1.plot(x, left[i], color=c, lw=2, label=lbl, solid_capstyle="round")
        idx = _index(s["monthly"]["per_million"])
        ax2.plot(x, [nan if v is None else v for v in idx], color=c, lw=2, label=lbl)
        if len(shown) <= 4:  # selective direct labels at the line end
            ax1.annotate(lbl, (x[-1], left[i][-1]), xytext=(4, 0),
                         textcoords="offset points", va="center", fontsize=7.5, color=INK2)
    base = index_base_text(len(months))
    if relative:
        ax1.axhline(100, color=INK2, lw=0.8, ls=(0, (3, 3)))
        ax1.set_title(f"Views, index ({base})", loc="left", color=INK, fontsize=9)
    else:
        if log:
            ax1.set_yscale("log")
        ax1.set_title("Average daily views per month" + (" (log scale)" if log else ""),
                      loc="left", color=INK, fontsize=9)
    ax1.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax2.axhline(100, color=INK2, lw=0.8, ls=(0, (3, 3)))
    ax2.set_title(f"Share of the wiki's traffic, index ({base})", loc="left", color=INK, fontsize=9)
    for ax in (ax1, ax2):
        _month_ticks(ax, months)
        ax.grid(axis="y", color=GRID, lw=0.6)
        ax.spines[["top", "right"]].set_visible(False)
        ax.set_xlim(0, len(x) - 1 + (len(x) * 0.08 if ax is ax1 and len(shown) <= 4 else 0))
    rect = (0, 0, 1, 1)
    if len(shown) >= 2:  # legend below both panels so it never covers data
        handles, labels = ax1.get_legend_handles_labels()
        ncol = min(len(shown), 8) if sum(len(x) for x in labels) <= 90 else 4  # long labels: two rows
        fig.legend(handles, labels, loc="lower center", ncol=ncol, frameon=False, fontsize=7.5)
        rect = (0, 0.07 if ncol >= len(shown) else 0.12, 1, 1)
    if len(ok) > MAX_SERIES:
        fig.text(0.99, 0.01, f"+{len(ok) - MAX_SERIES} series in the table only", color=INK2, fontsize=7, ha="right")
    fig.tight_layout(rect=rect)
    fig.savefig(path, facecolor="white")
    plt.close(fig)
    return str(path)


def _short(label: str, n: int = 22) -> str:
    if len(label) <= n:
        return label
    head, sep, tail = label.rpartition(" · ")  # keep the language
    if sep:
        return head[:max(3, n - len(tail) - 4)].rstrip() + "… · " + tail
    return label[:n - 1] + "…"


def _bar_axes(ax, title: str) -> None:
    ax.set_title(title, loc="left", color=INK, fontsize=8.5)
    ax.grid(axis="x", color=GRID, lw=0.6)
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)
    ax.tick_params(axis="x", labelsize=7)


def _bars(ax, ys, values, colors, fmt, height, texts=None, **kw) -> None:
    vals = [0 if v is None else v for v in values]
    ax.barh(ys, vals, color=colors, height=height, edgecolor="white", linewidth=0.8, **kw)
    texts = texts or [None] * len(values)
    for y, v, raw, txt in zip(ys, vals, values, texts):
        ax.annotate(txt or ("n/a" if raw is None else fmt(raw)), (v, y), xytext=(3 if v >= 0 else -3, 0),
                    textcoords="offset points", va="center", ha="left" if v >= 0 else "right",
                    fontsize=6.5, color=INK2)


def _xlim(ax, values) -> None:
    vals = [v for v in values if v is not None] + [0]
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.3 or 1
    ax.set_xlim(lo - (pad if lo < 0 else 0), hi + pad)


def compare_chart(res: dict, path: Path) -> str | None:
    """Per-series bars: audience size, change vs the previous period, volatility."""
    ok = [s for s in res["series"] if s["status"] == "ok"][:MAX_SERIES]
    if not ok:
        return None
    _style()
    labels = [_short(series_label(s, res)) for s in ok]
    colors = PALETTE[:len(ok)]
    ys = list(range(len(ok)))[::-1]  # first series on top, same order as the legend
    period = ("last 12 m vs previous 12" if ok[0]["period_change"]["kind"] == "yoy"
              else "2nd vs 1st half of window")
    fig, axes = plt.subplots(1, 3, figsize=(10, max(1.8, 0.7 + 0.3 * len(ok))), dpi=200, sharey=True)

    views = [s["avg_daily_views"] for s in ok]
    _bars(axes[0], ys, views, colors, lambda v: f"{v:,.0f}", 0.62)
    _xlim(axes[0], views)
    _bar_axes(axes[0], "Audience: views/day, last 12 m")

    ch_v = [s["period_change"]["pct"] for s in ok]
    ch_s = [s["period_change_normalized"]["pct"] for s in ok]
    def with_capped(s, v, key="period_change_ex_spikes"):  # a change made by spikes must not look like growth
        x = (s.get(key) or {}).get("pct")
        return f"{v:+.0f}% ({x:+.0f}% w/o spikes)" if None not in (v, x) and abs(v - x) >= 10 else None
    _bars(axes[1], [y + 0.18 for y in ys], ch_v, colors, lambda v: f"{v:+.0f}%", 0.34,
          texts=[with_capped(s, v) for s, v in zip(ok, ch_v)], label="views")
    _bars(axes[1], [y - 0.18 for y in ys], ch_s, colors, lambda v: f"{v:+.0f}%", 0.34,
          texts=[with_capped(s, v, "period_change_normalized_ex_spikes") for s, v in zip(ok, ch_s)], alpha=0.4, hatch="////", label="share of wiki")
    _xlim(axes[1], ch_v + ch_s)
    axes[1].axvline(0, color=INK2, lw=0.8)
    _bar_axes(axes[1], f"Change, % ({period})")
    axes[1].legend(loc="upper center", bbox_to_anchor=(0.5, -0.14 if len(ok) > 2 else -0.3), ncol=2,
                   fontsize=6.5, frameon=False, handlelength=1.2)

    vol = [s["describe"]["mom_volatility_pct"] for s in ok]
    _bars(axes[2], ys, vol, colors, lambda v: f"{v:.0f}%", 0.62)
    _xlim(axes[2], vol)
    _bar_axes(axes[2], "Volatility: typical monthly change")

    axes[0].set_yticks(ys, labels, fontsize=7.5)
    n_all = sum(s["status"] == "ok" for s in res["series"])
    if n_all > MAX_SERIES:
        fig.text(0.99, 0.01, f"+{n_all - MAX_SERIES} series in the table only", color=INK2, fontsize=7, ha="right")
    fig.tight_layout(w_pad=1.2)
    fig.savefig(path, facecolor="white")
    plt.close(fig)
    return str(path)
