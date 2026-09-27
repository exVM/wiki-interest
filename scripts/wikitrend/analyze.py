"""Build and analyse topic x language pageview series."""

from __future__ import annotations

import calendar
import csv
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path

import numpy as np

from . import stats
from .api import FIRST_DAY, ApiError, daily_views, last_complete_day, monthly_devices
from .resolve import redirects_to, resolve_topic


def month_window(months: int | None, start: str | None, end: str | None) -> tuple[date, date]:
    """Inclusive (first day, last day) of a window of whole calendar months."""
    lcd = last_complete_day()
    if end:
        ey, em = map(int, end.split("-"))
    else:
        ey, em = lcd.year, lcd.month
        if lcd.day != calendar.monthrange(ey, em)[1]:  # current month incomplete
            ey, em = (ey, em - 1) if em > 1 else (ey - 1, 12)
    end_d = date(ey, em, calendar.monthrange(ey, em)[1])
    if start:
        sy, sm = map(int, start.split("-"))
    else:
        total = ey * 12 + em - 1 - ((months or 24) - 1)
        sy, sm = total // 12, total % 12 + 1
    start_d = max(date(sy, sm, 1), FIRST_DAY)
    if start_d > end_d:
        raise ValueError("start month is after end month")
    if end_d > lcd:
        end_d = lcd
    return start_d, end_d


def _slug(text: str) -> str:
    s = re.sub(r"[^\w]+", "-", text.lower(), flags=re.U).strip("-")
    return s[:60] or "analysis"


def _safe(fn, *args):
    """Devices are supporting context: an API failure must not stop the analysis."""
    try:
        return fn(*args)
    except ApiError:
        return None


def edition_metrics(devices: dict[str, int] | None) -> dict | None:
    if not devices:
        return None
    vals = list(devices.values())
    return {"devices_per_month": int(round(float(np.mean(vals[-12:])))), "months": len(vals),
            "last_month": list(devices)[-1], "trend": stats.trend(vals) if len(vals) >= 6 else None}


def _series_metrics(days, views, totals, extra) -> dict:
    months_map = stats.monthly_mean(days, views)
    months, vals = list(months_map), list(months_map.values())
    per_m = np.divide(views, totals, out=np.zeros_like(views), where=totals > 0) * 1e6
    norm_vals = list(stats.monthly_mean(days, per_m).values())
    spikes = stats.detect_spikes(days, views)
    capped_vals = list(stats.monthly_mean(days, spikes["capped"]).values())
    capped_norm = np.divide(spikes["capped"], totals, out=np.zeros_like(views), where=totals > 0) * 1e6
    capped_norm_vals = list(stats.monthly_mean(days, capped_norm).values())
    anomalies = stats.detect_dips_and_gaps(days, views)
    nz = np.nonzero(views)[0]
    # days with data: from the first recorded view on, minus zero-view gaps inside that span
    coverage = (len(views) - nz[0] - anomalies["gap_days"]) / len(views) if len(nz) else 0.0
    # An article created (or renamed) mid-window has zeros before it: counting them as "before"
    # fakes a rising trend. Statistics start at the first full month with data; charts keep the window.
    k = 0
    if len(nz) and nz[0] >= 14:
        first = days[nz[0]]
        k = min(months.index(first.strftime("%Y-%m")) + (first.day > 1), len(months) - 1)
    full = (months, vals, norm_vals, capped_vals, capped_norm_vals)
    months, vals, norm_vals, capped_vals, capped_norm_vals = (x[k:] for x in full)
    last = min(12, len(vals))
    m = {
        "stats_from": months[0] if k and months else None,
        "n_months": len(months),
        "avg_daily_views": round(float(np.mean(vals[-last:])), 1),
        "total_views": int(views.sum()),
        "per_million_views": round(float(np.mean(norm_vals[-last:])), 2),
        "coverage": round(float(coverage), 3),
        "trend": stats.trend(vals),
        "trend_normalized": stats.trend(norm_vals),
        "trend_ex_spikes": stats.trend(capped_vals),
        "trend_normalized_ex_spikes": stats.trend(capped_norm_vals),
        "period_change": stats.period_change(vals),
        "period_change_normalized": stats.period_change(norm_vals),
        "period_change_ex_spikes": stats.period_change(capped_vals),
        "period_change_normalized_ex_spikes": stats.period_change(capped_norm_vals),
        "recent_shift": stats.recent_shift(capped_vals),
        "recent_shift_normalized": stats.recent_shift(capped_norm_vals),
        "same_month_yoy": stats.same_month_yoy(months, vals),
        "same_month_yoy_normalized": stats.same_month_yoy(months, norm_vals),
        "describe": stats.describe(months, vals),
        "seasonality": stats.seasonality(months, vals),
        "spikes": {k: spikes[k] for k in ("share_of_views", "events", "n_events") if k in spikes},
        "anomalies": anomalies,
        "_capped_daily": spikes["capped"],  # raw intermediate for series_daily.csv, not stored in JSON
        **extra,
    }
    m["spikes"]["share_of_views"] = round(m["spikes"]["share_of_views"], 3)
    m["assessment"] = stats.assess(m)
    months, vals, norm_vals, capped_vals, _ = full
    m["monthly"] = {"months": months, "avg_daily_views": [round(v, 1) for v in vals],
                    "per_million": [round(v, 3) for v in norm_vals],
                    "spike_capped": [round(v, 1) for v in capped_vals]}
    return m


def _hash(obj) -> str:
    return hashlib.sha1(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


def run(topics: list[str], langs: list[str], months: int | None = 24, start: str | None = None,
        end: str | None = None, max_redirects: int = 25, out_dir: str | None = None,
        question: str | None = None, relative: bool = False, parent: dict | None = None) -> dict:
    """Every call recomputes everything from the downloaded data; `parent` (a previous analysis.json)
    is only compared with afterwards, never reused."""
    start_d, end_d = month_window(months, start, end)
    days = stats.day_range(start_d, end_d)
    resolved = [resolve_topic(t, langs) for t in topics]

    # Collect every (lang, title) whose views we need, including redirects.
    plans = []  # (topic_idx, lang, [main titles], [redirect titles])
    for ti, r in enumerate(resolved):
        for lang in langs:
            mains = [it["titles"][lang] for it in r["items"] if lang in it["titles"]]
            plans.append((ti, lang, mains))
    # MediaWiki action API etiquette: serial requests. Pageviews REST tolerates parallelism.
    redirect_of = {(lang, t): redirects_to(lang, t, max_redirects)
                   for lang, t in dict.fromkeys((lang, t) for _, lang, mains in plans for t in mains)}
    redir = [[x for t in mains for x in redirect_of[(lang, t)]] for _, lang, mains in plans]
    with ThreadPoolExecutor(6) as pool:
        jobs = {(lang, None) for lang in langs}
        for (ti, lang, mains), rd in zip(plans, redir):
            jobs.update((lang, t) for t in mains + rd)
        jobs = sorted(jobs, key=lambda j: (j[0], j[1] or ""))
        fetched = dict(zip(jobs, pool.map(
            lambda j: daily_views(f"{j[0]}.wikipedia.org", j[1], start_d, end_d), jobs)))
        # Supporting context: unique devices of each edition.
        devices = dict(zip(langs, pool.map(
            lambda l: _safe(monthly_devices, f"{l}.wikipedia.org", start_d, end_d), langs)))
    editions = {l: edition_metrics(devices[l]) for l in langs}

    totals = {lang: stats.to_array(fetched[(lang, None)], days) for lang in langs}
    series, daily = [], []
    for (ti, lang, mains), rd in zip(plans, redir):
        r = resolved[ti]
        base = {"topic": r["label"], "topic_index": ti, "lang": lang, "titles": mains,
                "qids": [it["qid"] for it in r["items"] if lang in it["titles"]]}
        if not mains:
            series.append({**base, "status": "no article in this language"})
            continue
        main_v = sum((stats.to_array(fetched[(lang, t)], days) for t in mains), np.zeros(len(days)))
        red_v = sum((stats.to_array(fetched[(lang, t)], days) for t in rd), np.zeros(len(days)))
        views = main_v + red_v
        if views.sum() == 0:
            series.append({**base, "status": "no recorded views"})
            continue
        extra = {
            "redirects_counted": len(rd),
            "redirect_share": round(float(red_v.sum() / views.sum()), 3),
            "project_avg_daily_views": round(float(totals[lang][-365:].mean()), 0),
            "project_trend": stats.trend(list(stats.monthly_mean(days, totals[lang]).values())),
            "edition_devices_per_month": (editions[lang] or {}).get("devices_per_month"),
        }
        m = _series_metrics(days, views, totals[lang], extra)
        daily.append((r["label"], lang, main_v, red_v, totals[lang], m.pop("_capped_daily")))
        series.append({**base, "status": "ok", **m})

    label = ", ".join(r["label"] for r in resolved)
    params = {"topics": topics, "langs": langs, "start": start_d.isoformat(), "end": end_d.isoformat(),
              "months": len(stats.monthly_mean(days, np.zeros(len(days)))),
              "max_redirects": max_redirects, "agent": "user", "access": "all-access", "relative": relative}
    # data_hash fingerprints the downloaded inputs; run_id = what was asked + what was downloaded.
    # Same run_id = same numbers; a different one means every statistic must be taken from this run.
    data_hash = _hash([sorted((f"{k[0]}|{k[1] or ''}", v) for k, v in fetched.items()), devices])
    run_id = _hash([{k: params[k] for k in ("topics", "langs", "start", "end", "max_redirects")}, data_hash])[:10]
    result = {
        "run_id": run_id,
        "data_hash": data_hash,
        "generated": datetime.now().isoformat(timespec="seconds"),
        "question": question,
        "params": params,
        "topics": [{"spec": r["spec"], "label": r["label"], "missing_langs": r["missing_langs"],
                    "warnings": r.get("warnings", []), "items": r["items"]} for r in resolved],
        "editions": editions,
        "series": series,
    }
    if parent is not None:
        from .followup import diff
        result["followup"] = diff(parent, result)
    # the run id in the default folder name: a follow-up never overwrites the run it refines
    out = Path(out_dir) if out_dir else Path("wikitrend-out") / (_slug(
        f"{label}-{'-'.join(langs)}-{start_d:%Y%m}-{end_d:%Y%m}") + f"-{run_id[:6]}")
    if parent is not None and parent.get("_path"):  # a follow-up never overwrites the run it refines
        prev = Path(parent["_path"]).parent.resolve()
        if out_dir and out.resolve() == prev:
            raise ValueError(f"--out {out_dir} is the folder of the run given in --from; use another folder")
        base, n = out.name, 2
        while out.resolve() == prev:  # same run id (only --question/--relative changed)
            out, n = out.with_name(f"{base}-{n}"), n + 1
    out.mkdir(parents=True, exist_ok=True)
    result["out_dir"] = out.as_posix()
    for f in ("chart.png", "compare.png"):  # never leave a chart of another run next to this analysis
        (out / f).unlink(missing_ok=True)
    (out / "analysis.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    _write_csv(out / "monthly.csv", series)
    _write_raw(out / "raw", run_id, days, plans, redirect_of, resolved, fetched, devices, daily)
    from .charts import compare_chart, trend_chart
    result["chart"] = trend_chart(result, out / "chart.png")
    result["compare_chart"] = compare_chart(result, out / "compare.png")
    (out / "analysis.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def _write_raw(raw: Path, run_id, days, plans, redirect_of, resolved, fetched, devices, daily) -> None:
    """Unaggregated inputs exactly as downloaded, plus the daily series the statistics were computed from."""
    raw.mkdir(exist_ok=True)
    rows = lambda name, head, it: _rows(raw / name, ["run_id", *head], ([run_id, *r] for r in it))  # noqa: E731
    rows("articles.csv", ["topic", "lang", "article", "role", "redirect_of"],
         [(resolved[ti]["label"], lang, t, role, of) for ti, lang, mains in plans
          for t, role, of in [(t, "main", "") for t in mains]
          + [(x, "redirect", m) for m in mains for x in redirect_of[(lang, m)]]])
    rows("daily_views.csv", ["date", "lang", "article", "views"],
         [(d, lang, title or "(whole edition)", v) for (lang, title), vals in fetched.items()
          for d, v in sorted(vals.items())])
    rows("unique_devices.csv", ["lang", "month", "devices"],
         [(lang, mo, n) for lang, vals in devices.items() if vals for mo, n in vals.items()])
    rows("series_daily.csv", ["date", "topic", "lang", "article_views", "redirect_views", "views",
                              "edition_views", "per_million", "views_spike_capped"],
         [(d.isoformat(), topic, lang, int(a), int(b), int(a + b), int(e),
           round((a + b) / e * 1e6, 4) if e else "", round(float(c), 1))
          for topic, lang, mv, rv, tot, cap in daily for d, a, b, e, c in zip(days, mv, rv, tot, cap)])


def _rows(path: Path, head: list[str], rows) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(head)
        w.writerows(rows)


def _write_csv(path: Path, series: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["topic", "lang", "month", "avg_daily_views", "per_million_project_views", "spike_capped"])
        for s in series:
            if s["status"] != "ok":
                continue
            m = s["monthly"]
            for row in zip(m["months"], m["avg_daily_views"], m["per_million"], m["spike_capped"]):
                w.writerow([s["topic"], s["lang"], *row])


def fmt_pct(v) -> str:
    return "n/a" if v is None else f"{v:+.0f}%"


CHECK_WORDS = {
    "enough_volume": "enough volume", "full_coverage": "full data coverage",
    "statistically_significant": "statistically significant", "period_change_agrees": "year-over-year change agrees",
    "not_driven_by_spikes": "not driven by spikes", "seasonality_handled": "window covers 2+ seasonal cycles",
    "precise_estimate": "precise estimate", "recent_level_consistent": "recent months agree",
}


def _trust(v: dict) -> str:
    good = [CHECK_WORDS[c["check"]] for c in v["checks"] if c["ok"]]
    bad = [f"{CHECK_WORDS[c['check']]} FAILED ({c['note']})" for c in v["checks"] if not c["ok"]]
    out = f"{v['confidence']} confidence"
    if good:
        out += "; supported by: " + ", ".join(good)
    if bad:
        out += "; weakened by: " + "; ".join(bad)
    return out


def reading(s: dict) -> str:
    """Plain-language interpretation of one series, so small models do not have to infer it."""
    a, tr, tn = s["assessment"], s["trend"], s["trend_normalized"]
    ab, sh = a["absolute"], a["share"]
    wiki = f"{s['lang']}.wikipedia overall ({fmt_pct(s['project_trend']['pct_per_year'])} per year)"
    out = [f"Views {ab['verdict']}: {fmt_pct(tr['pct_per_year'])} per year (95% CI {fmt_pct(tr['ci_low'])}.."
           f"{fmt_pct(tr['ci_high'])}), {_trust(ab)}."]
    if s.get("stats_from"):
        out.insert(0, f"No views before {s['stats_from']} (article created or renamed then): statistics use "
                      f"{s['stats_from']} onwards only, {s['n_months']} months.")
    if sh["verdict"] == "rising":
        rel = f"gaining share - growing faster than {wiki}"
    elif sh["verdict"] == "falling":
        rel = f"losing share - doing worse than {wiki}"
    elif sh["verdict"] == "no clear trend" and sh["confidence"] == "high":
        rel = f"holding a steady share - moving with {wiki}"
    elif sh["verdict"] == "no clear trend":
        rel = f"no clear change in share - the data cannot tell if it does better or worse than {wiki}; do not call it stable"
    else:
        rel = "too little data to compare with the whole edition"
    out.append(f"Share of the edition's traffic: {rel}. Share trend {fmt_pct(tn['pct_per_year'])} per year "
               f"(95% CI {fmt_pct(tn['ci_low'])}..{fmt_pct(tn['ci_high'])}), {_trust(sh)}.")
    sp = s["spikes"]["share_of_views"]
    out.append("No unusual spikes." if sp < 0.02 else f"{sp * 100:.0f}% of views came from {s['spikes']['n_events']} spike episode(s).")
    an = s.get("anomalies") or {}
    flags = []
    if an.get("dips"):
        flags.append(f"{len(an['dips'])} sharp dip episode(s) (first {an['dips'][0]['start']})")
    if an.get("gaps"):
        flags.append(f"{len(an['gaps'])} data gap(s) with zero views, {an['gap_days']} days in total "
                     f"(first {an['gaps'][0]['start']}; outage, page move or deletion?)")
    if flags:
        out.append("Flagged, not removed: " + "; ".join(flags) + " - check them before concluding.")
    if len(s.get("titles", [])) > 1:
        out.append(f"This series sums {len(s['titles'])} articles ({' + '.join(s['titles'])}).")
    if s["seasonality"] and s["seasonality"]["strong"]:
        out.append(f"Regular yearly cycle: high in {s['seasonality']['peak_month']}, low in "
                   f"{s['seasonality']['low_month']} - these peaks are seasonal, not news spikes.")
    return " ".join(out)


def rankings(res: dict) -> list[str]:
    """Pre-sorted orderings with both ends named, so 'largest'/'smallest'/'fastest' claims need no comparison."""
    ok = [s for s in res["series"] if s["status"] == "ok"]
    if len(ok) < 2:
        return []
    tag = (lambda s: s["lang"]) if len(res["topics"]) == 1 else (lambda s: f"{s['topic']} [{s['lang']}]")
    # (name, sort key, value text, word for the top, word for the bottom)
    rows = [
        ("Audience size, views/day", lambda s: s["avg_daily_views"], lambda s: f"{s['avg_daily_views']:,.0f}",
         "largest", "smallest"),
        ("Prominence, views per 1M views of the edition", lambda s: s["per_million_views"],
         lambda s: f"{s['per_million_views']:.1f}", "most prominent", "least prominent"),
        ("Share trend, %/year", lambda s: s["trend_normalized"]["pct_per_year"] or -1e9,
         lambda s: f"{fmt_pct(s['trend_normalized']['pct_per_year'])} {s['assessment']['share']['verdict']}",
         "best", "worst"),
    ]
    out = ["Rankings (highest first; '≈' = tie at the shown precision; copy these for any 'largest', "
           "'smallest', 'fastest', 'weakest' claims):"]
    for name, key, val, top, bottom in rows:
        order = sorted(ok, key=key, reverse=True)
        line = tag(order[0]) + " " + val(order[0])
        for a, b in zip(order, order[1:]):
            line += (" ≈ " if val(a) == val(b) else " > ") + f"{tag(b)} {val(b)}"
        ends = lambda i: [tag(s) for s in order if val(s) == val(order[i])]  # noqa: E731
        first, last = ends(0), ends(-1)
        if len(first) == len(order):
            out.append(f"- {name}: {line} (all tied)")
        else:
            out.append(f"- {name}: {line} ({top}: {' = '.join(first)}; {bottom}: {' = '.join(last)})")
    eds = sorted(((l, e) for l, e in (res.get("editions") or {}).items() if e), key=lambda x: -x[1]["devices_per_month"])
    if len(eds) >= 2:
        out.append("- Edition readers, unique devices/month: " + " > ".join(
            f"{l} {_millions(e['devices_per_month'])}" for l, e in eds) + f" (largest: {eds[0][0]}; smallest: {eds[-1][0]})")
    return out


def _millions(n: int) -> str:
    return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.0f}k"


def summary_text(res: dict) -> str:
    """Compact, model-friendly summary of an analysis (what the CLI prints)."""
    p = res["params"]
    lines = []
    if res.get("followup"):
        from .followup import diff_text
        lines += diff_text(res["followup"]) + [""]
    lines.append(f"Run {res.get('run_id', '?')} - cite this id; numbers from any other run do not apply to this one.")
    lines.append(f"Window: {p['start']} .. {p['end']} (whole months, human traffic only, redirects included up to {p['max_redirects']})")
    for t in res["topics"]:
        for it in t["items"]:
            titles = "; ".join(f"{l}: {x}" for l, x in it["titles"].items())
            lines.append(f"Topic '{t['label']}' -> {it['qid']} ({it['description'] or 'no description'}) | {titles}")
            for w in it["warnings"]:
                lines.append(f"  WARNING: {w}")
            if it["other_candidates"]:
                alts = "; ".join(f"{c['qid']} {c['label']} - {c['description']}" for c in it["other_candidates"])
                lines.append(f"  other candidates: {alts}")
        for w in t.get("warnings", []):
            lines.append(f"  WARNING (topic '{t['label']}'): {w}")
    lines.append("")
    lines.append("abs = absolute views; share = views per million views of that language edition "
                 "(removes whole-Wikipedia traffic shifts). Trends are %/year with 95% CI.")
    lines.append("| topic | lang | views/day | per 1M | abs trend (CI) | abs verdict | share trend (CI) | share verdict | wiki overall trend |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    ci = lambda t: f"{fmt_pct(t['pct_per_year'])} ({fmt_pct(t['ci_low'])}..{fmt_pct(t['ci_high'])})"  # noqa: E731
    for s in res["series"]:
        if s["status"] != "ok":
            lines.append(f"| {s['topic']} | {s['lang']} | - | - | - | {s['status']} | - | - | - |")
            continue
        a = s["assessment"]
        lines.append(
            f"| {s['topic']} | {s['lang']} | {s['avg_daily_views']:.0f} | {s['per_million_views']:.1f} | "
            f"{ci(s['trend'])} | {a['absolute']['verdict']} ({a['absolute']['confidence']}) | "
            f"{ci(s['trend_normalized'])} | {a['share']['verdict']} ({a['share']['confidence']}) | "
            f"{fmt_pct(s['project_trend']['pct_per_year'])} |")
    lines.append("")
    lines += stats_table(res)
    lines.append("")
    lines.append("Reading (use these statements; do not infer spikes or trends from the chart by eye):")
    for s in res["series"]:
        if s["status"] == "ok":
            lines.append(f"- {s['topic']} [{s['lang']}]: {reading(s)}")
    eds = {l: e for l, e in (res.get("editions") or {}).items() if e}
    if eds:
        lines.append("")
        lines.append("Edition readers (unique devices per month, average of the last 12 months; a device is not a person):")
        for l, e in eds.items():
            tr = e["trend"]
            lines.append(f"- {l}.wikipedia: {_millions(e['devices_per_month'])} devices/month"
                         + (f", {fmt_pct(tr['pct_per_year'])} per year" if tr else "")
                         + f" (data through {e['last_month']})")
    lines.append("")
    if rankings(res):
        lines += rankings(res) + [""]
    lines.append("Failed checks and notes (read before concluding):")
    notes = []
    for s in res["series"]:
        if s["status"] != "ok":
            continue
        tag = f"{s['topic']} [{s['lang']}]"
        seen = set()
        for kind in ("absolute", "share"):
            for c in s["assessment"][kind]["checks"]:
                if not c["ok"] and (c["check"], c["note"]) not in seen:
                    seen.add((c["check"], c["note"]))
                    notes.append(f"- {tag} {kind}: {c['check']} failed - {c['note']}")
        if s["seasonality"] and s["seasonality"]["strong"]:
            notes.append(f"- {tag} note: seasonal, peak in {s['seasonality']['peak_month']}, "
                         f"low in {s['seasonality']['low_month']} - compare year-over-year, not month-to-month")
        for e in s["spikes"]["events"][:2]:
            if e["excess_views"] > 0.05 * s["total_views"]:
                notes.append(f"- {tag} note: spike around {e['peak_day']} "
                             f"({e['x_baseline']}x baseline, {e['excess_views']} extra views)")
        if s["redirect_share"] > 0.1:
            notes.append(f"- {tag} note: {s['redirect_share'] * 100:.0f}% of views came via redirects")
        an = s.get("anomalies") or {}
        for d in an.get("dips", [])[:2]:
            notes.append(f"- {tag} note: dip from {d['start']} ({d['days']} day(s), down to {d['min_views']} "
                         f"views vs ~{d['baseline']:.0f} usual) - flagged, not removed")
        for g in an.get("gaps", [])[:2]:
            notes.append(f"- {tag} note: {g['days']} days with zero views from {g['start']} "
                         f"(~{g['baseline']:.0f}/day usual) - possible data gap, page move or deletion")
    lines.extend(notes or ["- none"])
    missing = sorted({s["lang"] for s in res["series"] if s["status"] == "no article in this language"})
    if missing:
        lines.append("")
        lines.append(f"No linked article in: {', '.join(missing)}. Either report that absence (it is a finding: "
                     f"less local content), or look for a local article with: wt search --lang <code> \"<words in that language>\"")
        if any(it.get("pinned_lang") for t in res["topics"] for it in t["items"]):
            lines.append("A topic with 'lang:Title' parts counts each such part only in its language: to cover "
                         "a new language, add '+<code>:<Title>' to the topic.")
    lines.append("")
    lines.append("Business framing (signal -> interpretation -> hypothesis -> next check). These are HYPOTHESES "
                 "to validate, not decisions; pageviews are curiosity, not market size or willingness to pay:")
    lines += [f"- {b}" for b in business_frame(res)]
    lines.append("")
    lines.append(f"Files: {res['out_dir']}/analysis.json, monthly.csv, chart.png, compare.png; raw inputs in "
                 f"{res['out_dir']}/raw/ (daily_views, unique_devices, articles, series_daily)")
    lines.append(f"Follow-up (other topic, language or period): wt analyze --from {res['out_dir']} + only the changed options")
    return "\n".join(lines)


def stats_table(res: dict) -> list[str]:
    """Volume, stability and same-month comparisons per series."""
    ok = [s for s in res["series"] if s["status"] == "ok"]
    if not ok:
        return []
    out = ["Volume & stability (monthly average views/day; anomalies are flagged, never removed):",
           "| topic | lang | median | peak (month) | min (month) | last month | same month a year earlier -> change "
           "| last 12 m vs previous (views/day) | month-to-month volatility | trend fit R² | same-month pairs up |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in ok:
        d, y, pc = s["describe"], s.get("same_month_yoy"), s["period_change"]
        px = s.get("period_change_ex_spikes") or {}
        wo = (f"; {fmt_pct(pc['pct'])}, {fmt_pct(px['pct'])} without spikes"
              if pc["pct"] is not None and px.get("pct") is not None and abs(pc["pct"] - px["pct"]) >= 10 else "")
        sm = (f"{y['vs_month']} -> {fmt_pct(y['pct'])}" if y else "n/a (<13 months)")
        up = f"{y['months_up']}/{y['months_compared']}" if y else "n/a"
        kind = "12 vs 12" if pc["kind"] == "yoy" else "half vs half"
        out.append(
            f"| {s['topic']} | {s['lang']} | {d['median']:,.1f} | {d['peak']['value']:,.1f} ({d['peak']['month']}) | "
            f"{d['min']['value']:,.1f} ({d['min']['month']}) | {d['last_month']['value']:,.1f} ({d['last_month']['month']}) | "
            f"{sm} | {pc['before_avg_daily']:,.1f} -> {pc['after_avg_daily']:,.1f} ({pc['abs_change']:+,.1f}, {kind}{wo}) | "
            f"{d['mom_volatility_pct']:.0f}% ({d['stability']}) | {s['trend']['r2']} | {up} |")
    return out


MONTHS_UK = ["січні", "лютому", "березні", "квітні", "травні", "червні", "липні", "серпні",
             "вересні", "жовтні", "листопаді", "грудні"]

FRAME = {
    "en": {
        "rising": ("Signal: {x} gain(s) share of the edition ({p}/yr, {c} confidence). Interpretation: attention to "
                   "the topic grows faster than the edition overall. Hypothesis: demand is growing in {top}. "
                   "Next check: search-volume trends and a small landing-page or ad test there."),
        "falling": ("Signal: no series gains share with confidence; {x} lose(s) share. Interpretation: Wikipedia shows "
                    "no growing curiosity. Hypothesis: interest is flat or moving to other channels (search, AI "
                    "answers, other editions). Next check: search trends and peer topics before betting on growth."),
        "unclear": ("Signal: no share trend is clear. Interpretation: the data cannot tell growth from decline. "
                    "Next check: a longer window (--months 60) or another data source."),
        "size": ("Signal: largest audience {top} ({n}/day, {pm} per 1M views). Interpretation: the most readers are "
                 "curious there now; this is audience size, not market size or willingness to pay. Hypothesis: "
                 "{top} is the biggest reachable audience for this topic. Next check: willingness-to-pay survey or "
                 "pre-order test."),
        "season": ("Signal: yearly cycle in {x} (high in {m}). Hypothesis: campaigns timed just before {m} reach "
                   "more interested people. Next check: last year's sign-ups or sales by month."),
        "similar": ("Signal: the largest audiences are similar in size ({x} views/day, within 20%). "
                    "Interpretation: Wikipedia does not separate these markets by size; decide on other evidence."),
        "low": "Caution: {x} under 30 views/day - treat as anecdotal.",
        "missing": ("No article in {x}: less local content. Hypothesis: a content gap (opportunity) or a smaller "
                    "audience. Next check: local search data."),
        "months": stats.MONTH_NAMES,
        "confs": {"high": "high", "medium": "medium", "low": "low"},
    },
    "uk": {
        "rising": ("Сигнал: {x} збільшує частку трафіку розділу ({p}/рік, впевненість {c}). Інтерпретація: увага до "
                   "теми росте швидше, ніж розділ загалом. Гіпотеза: попит зростає в {top}. Наступна перевірка: "
                   "динаміка пошукових запитів і невеликий тест лендингу або реклами там."),
        "falling": ("Сигнал: жоден ряд упевнено не збільшує частку; {x} втрачає частку. Інтерпретація: Вікіпедія не "
                    "показує зростання цікавості. Гіпотеза: інтерес стабільний або переходить в інші канали (пошук, "
                    "AI-відповіді, інші мовні розділи). Наступна перевірка: пошукові тренди й суміжні теми, перш ніж "
                    "робити ставку на зростання."),
        "unclear": ("Сигнал: тренд частки неясний. Інтерпретація: дані не відрізняють зростання від спаду. Наступна "
                    "перевірка: довше вікно (--months 60) або інше джерело даних."),
        "size": ("Сигнал: найбільша аудиторія - {top} ({n}/день, {pm} на 1 млн переглядів). Інтерпретація: зараз там "
                 "найбільше цікавих читачів; це розмір аудиторії, а не ринку чи готовності платити. Гіпотеза: {top} - "
                 "найбільша досяжна аудиторія для теми. Наступна перевірка: опитування щодо готовності платити або "
                 "тест передзамовлення."),
        "season": ("Сигнал: річний цикл у {x} (пік у {m}). Гіпотеза: кампанії перед піком охоплять більше "
                   "зацікавлених. Наступна перевірка: торішні реєстрації чи продажі за місяцями."),
        "similar": ("Сигнал: найбільші аудиторії схожі за розміром ({x} переглядів/день, різниця до 20%). "
                    "Інтерпретація: Вікіпедія не розрізняє ці ринки за розміром; рішення - на інших даних."),
        "low": "Обережно: {x} - менше 30 переглядів/день, це лише анекдотичний сигнал.",
        "missing": ("Немає статті в {x}: менше локального контенту. Гіпотеза: прогалина в контенті (можливість) або "
                    "менша аудиторія. Наступна перевірка: локальні пошукові дані."),
        "months": MONTHS_UK,
        "confs": {"high": "висока", "medium": "середня", "low": "низька"},
    },
}


def business_frame(res: dict, lang: str = "en", tag=None) -> list[str]:
    """Deterministic signal -> interpretation -> hypothesis -> next check bullets (max 4).

    Every claim is derived from verdicts and Rankings-style orderings, so a small model
    (or the PDF without agent text) never jumps from pageviews straight to 'launch'."""
    t = FRAME.get(lang, FRAME["en"])
    ok = [s for s in res["series"] if s["status"] == "ok"]
    if not ok:
        return []
    more = " (+{n} more)" if lang != "uk" else " (+ще {n})"

    def names(items: list, k: int = 3) -> str:  # never drop names silently
        return ", ".join(tag(s) for s in items[:k]) + (more.format(n=len(items) - k) if len(items) > k else "")

    if tag is None:
        tag = (lambda s: s["lang"]) if len(res["topics"]) == 1 else (lambda s: f"{s['topic']} [{s['lang']}]")
    good = lambda s, v: s["assessment"]["share"]["verdict"] == v and s["assessment"]["share"]["confidence"] != "low"  # noqa: E731
    share = lambda s: s["trend_normalized"]["pct_per_year"] or 0  # noqa: E731
    out = []
    rising = sorted((s for s in ok if good(s, "rising")), key=share, reverse=True)
    falling = sorted((s for s in ok if good(s, "falling")), key=share)
    if rising:
        top = rising[0]
        out.append(t["rising"].format(x=names(rising), p=fmt_pct(share(top)),
                                      c=t["confs"][top["assessment"]["share"]["confidence"]], top=tag(top)))
    elif falling:
        out.append(t["falling"].format(x=names(falling)))
    else:
        out.append(t["unclear"])
    by_size = sorted(ok, key=lambda s: -s["avg_daily_views"])
    big = by_size[0]
    if len(ok) >= 2 and big["avg_daily_views"] >= 1.2 * by_size[1]["avg_daily_views"]:
        out.append(t["size"].format(top=tag(big), n=f"{big['avg_daily_views']:,.0f}", pm=f"{big['per_million_views']:.1f}"))
    elif len(ok) >= 2:
        near = [s for s in by_size if s["avg_daily_views"] >= big["avg_daily_views"] / 1.2]
        out.append(t["similar"].format(x=", ".join(f"{tag(s)} {s['avg_daily_views']:,.0f}" for s in near)))
    seas = [s for s in ok if s["seasonality"] and s["seasonality"]["strong"]]
    if seas:
        m = t["months"][stats.MONTH_NAMES.index(seas[0]["seasonality"]["peak_month"])]
        out.append(t["season"].format(x=names(seas), m=m))
    cautions = []
    low = [s for s in ok if s["avg_daily_views"] < 30]
    if low:
        cautions.append(t["low"].format(x=names(low)))
    missing = [s for s in res["series"] if s["status"] != "ok"]
    if missing:
        cautions.append(t["missing"].format(x=names(missing, 2)))
    if cautions:
        out.append(" ".join(cautions))
    return out[:4]
