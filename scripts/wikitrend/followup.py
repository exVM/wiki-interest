"""Follow-up runs: reuse the previous run's parameters, change only what the user changed,
recompute everything, and say exactly what differs from the previous run.

A follow-up never patches old numbers: it is a full new run. The previous analysis.json is
read only for its parameters and, afterwards, to show what changed."""

from __future__ import annotations

import json
from pathlib import Path

from .analyze import fmt_pct


def load_parent(path: str) -> dict:
    p = Path(path)
    if p.is_dir():
        p = p / "analysis.json"
    if not p.is_file():
        raise ValueError(f"--from: no analysis.json at {path}")
    res = json.loads(p.read_text(encoding="utf-8"))
    res["_path"] = str(p)
    return res


def _months_between(start: str, end: str) -> int:
    return (int(end[:4]) - int(start[:4])) * 12 + int(end[5:7]) - int(start[5:7]) + 1


def merge_params(parent: dict, a) -> dict:
    """Parent parameters with the explicitly given flags applied; unchanged ones are kept as they were."""
    pp = parent["params"]
    topics = list(a.topic) if a.topic else list(pp["topics"])
    topics += [t for t in (a.add_topic or []) if t not in topics]
    drop = set(a.drop_topic or [])
    labels = {t["spec"]: t["label"] for t in parent.get("topics", [])}
    topics = [t for t in topics if t not in drop and labels.get(t) not in drop]
    langs = list(a.langs) if a.langs else list(pp["langs"])
    langs += [l for l in (a.add_langs or []) if l not in langs]
    langs = [l for l in langs if l not in set(a.drop_langs or [])]
    if not topics or not langs:
        raise ValueError("the follow-up leaves no topic or no language")

    # Period: keep the exact previous window unless the period is changed. Changing only the
    # length keeps the previous end; changing only the end keeps the previous length.
    p_start, p_end = pp["start"][:7], pp["end"][:7]
    months, start, end = a.months, a.start, a.end
    if end == "latest":
        end = None
    elif end is None and a.end is None:
        end = p_end
    if months is None and start is None:
        if a.end is None:
            start = p_start
        else:
            months = _months_between(p_start, p_end)
    return {"topics": topics, "langs": langs, "months": months, "start": start, "end": end,
            "max_redirects": a.max_redirects if a.max_redirects is not None else pp["max_redirects"],
            "question": a.question if a.question is not None else parent.get("question"),
            "relative": a.relative if a.relative is not None else bool(pp.get("relative"))}


IMPACT = {
    "window": ("every trend, CI, p-value, confidence, seasonality, year-over-year change, spike and "
               "anomaly flag, ranking and business-framing bullet is recomputed for the new window"),
    "topics": "rankings and business framing are recomputed for the new set of topics",
    "langs": "rankings, edition readers and business framing are recomputed for the new set of languages",
    "max_redirects": "views of every series change (more or fewer redirects are counted)",
    "relative": "only the chart changes (index instead of views/day); no statistic changes",
    "question": "no number changes; only the stored question",
}


def _key(s: dict) -> tuple:
    return (s["topic"], s["lang"])


def _short(s: dict, kind: str) -> str:
    if s.get("status") != "ok":
        return s.get("status", "n/a")
    t = s["trend"] if kind == "absolute" else s["trend_normalized"]
    a = s["assessment"][kind]
    return f"{fmt_pct(t['pct_per_year'])}/yr {a['verdict']} ({a['confidence']})"


def diff(parent: dict, res: dict) -> dict:
    """What changed between the parent run and this one, and what that invalidates."""
    pp, p = parent["params"], res["params"]
    changes = []
    if (pp["start"], pp["end"]) != (p["start"], p["end"]):
        changes.append({"param": "window", "old": f"{pp['start']} .. {pp['end']}", "new": f"{p['start']} .. {p['end']}"})
    for k in ("topics", "langs", "max_redirects", "relative"):
        if pp.get(k) != p.get(k):
            changes.append({"param": k, "old": pp.get(k), "new": p.get(k)})
    if parent.get("question") != res.get("question"):
        changes.append({"param": "question", "old": parent.get("question"), "new": res.get("question")})
    changed = {c["param"] for c in changes}
    kept = [k for k in ("window", "topics", "langs", "max_redirects") if k not in changed]

    warnings = []
    old_items = {t["spec"]: [it["qid"] for it in t["items"]] for t in parent.get("topics", [])}
    for t in res["topics"]:
        old = old_items.get(t["spec"])
        new = [it["qid"] for it in t["items"]]
        if old is not None and old != new:
            warnings.append(f"topic '{t['spec']}' now resolves to {', '.join(new)} (previous run: {', '.join(old)}) - "
                            f"pin the article to keep it comparable")
    same_basis = not changed & {"window", "max_redirects"}
    if not parent.get("data_hash"):
        warnings.append("the previous run has no data fingerprint (made by an older version): "
                        "compare it by the series lines below only")
    elif not changes and parent["data_hash"] != res.get("data_hash"):
        warnings.append("same parameters but different source data (Wikimedia revised counts or the cache was "
                        "refreshed) - numbers below replace the previous ones")

    old_series = {_key(s): s for s in parent.get("series", [])}
    series = []
    for s in res["series"]:
        o = old_series.get(_key(s))
        if o is None:
            continue
        row: dict = {"series": f"{s['topic']} [{s['lang']}]"}
        for kind in ("absolute", "share"):
            row[kind] = (_short(o, kind), _short(s, kind))
        row["same"] = row["absolute"][0] == row["absolute"][1] and row["share"][0] == row["share"][1]
        series.append(row)
    if same_basis and any(not r["same"] for r in series):
        warnings.append("some series differ although window and redirects are the same: the source data changed")
    return {"parent_run": parent.get("run_id"), "parent_analysis": parent.get("_path"), "changes": changes,
            "kept": kept, "impact": [IMPACT[c["param"]] for c in changes], "warnings": warnings,
            "series": series, "same_basis": same_basis}


def diff_text(d: dict) -> list[str]:
    parent = d["parent_run"] or "(unknown)"
    head = f"FOLLOW-UP of run {parent}"
    if not d["changes"]:
        lines = [head + ": nothing changed - same parameters, recomputed from the current data."]
    else:
        lines = [head + ". Changed: " + "; ".join(
            f"{c['param']} {_fmt(c['old'])} -> {_fmt(c['new'])}" for c in d["changes"])
            + (f". Kept: {', '.join(d['kept'])}." if d["kept"] else ".")]
        lines += [f"- Recomputed: {x}." for x in d["impact"]]
    if d["changes"] and not d["same_basis"]:
        lines.append(f"- Do NOT reuse numbers, verdicts or rankings from run {parent}: they belong to a "
                     "different window or data set. Answer only from the output below.")
    lines += [f"- WARNING: {w}" for w in d["warnings"]]
    if d["series"]:
        lines.append("- Series in both runs (previous -> now; abs | share):")
        for r in d["series"]:
            mark = ("unchanged" if s_ok(r) else f"{r['absolute'][1]} in both runs") if r["same"] else (
                f"abs {r['absolute'][0]} -> {r['absolute'][1]} | share {r['share'][0]} -> {r['share'][1]}")
            lines.append(f"  {r['series']}: {mark}")
    return lines


def s_ok(r: dict) -> bool:
    return "/yr" in r["absolute"][1]


def _fmt(v) -> str:
    if isinstance(v, list):
        return ",".join(str(x) for x in v)
    return "none" if v is None else str(v)
