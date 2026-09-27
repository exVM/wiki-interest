"""Command line entry point: `wt resolve | analyze | report | cache`."""

from __future__ import annotations

import argparse
import json
import sys

from .api import ApiError


def _langs(s: str) -> list[str]:
    langs = [x.strip().lower() for x in s.split(",") if x.strip()]
    if not langs:
        raise argparse.ArgumentTypeError("give at least one language code, e.g. pl,cs")
    return langs


def cmd_resolve(a) -> int:
    from .resolve import resolve_topic
    for spec in a.topic:
        r = resolve_topic(spec, a.langs)
        if a.json:
            print(json.dumps(r, ensure_ascii=False, indent=1))
            continue
        print(f"Topic '{r['label']}'")
        for it in r["items"]:
            print(f"  {it['qid']}: {it['label']} - {it['description'] or 'no description'}")
            for lang in a.langs:
                print(f"    {lang}: {it['titles'].get(lang, '(no article)')}")
            for w in it["warnings"]:
                print(f"    WARNING: {w}")
            for c in it["other_candidates"]:
                print(f"    alternative: {c['qid']} {c['label']} - {c['description']}")
        for w in r.get("warnings", []):
            print(f"  WARNING: {w}")
    return 0


def cmd_search(a) -> int:
    from datetime import timedelta

    from .api import daily_views, last_complete_day
    from .resolve import search_wiki
    hits = search_wiki(a.lang, a.query, a.limit)
    if not hits:
        print(f"No {a.lang}.wikipedia articles match {a.query!r}. Try other wording in that language.")
        return 0
    end = last_complete_day()
    start = end - timedelta(days=59)
    print(f"{a.lang}.wikipedia search '{a.query}' (views/day = average of last 60 days):")
    for h in hits:
        v = daily_views(f"{a.lang}.wikipedia.org", h["title"], start, end)
        avg = sum(v.values()) / 60
        print(f"  {h['title']} | {h['qid'] or 'no Wikidata item'} | {h['description'] or '-'} | {avg:.0f} views/day")
    print(f"Use one in analyze as --topic \"{a.lang}:<Title>\" and call it a proxy if it is not the same concept.")
    return 0


def cmd_analyze(a) -> int:
    from .analyze import run, summary_text
    parent = None
    if a.from_:
        from .followup import load_parent, merge_params
        parent = load_parent(a.from_)
        kw = merge_params(parent, a)
    else:
        if not a.topic or not a.langs:
            raise ValueError("--topic and --langs are required (or --from <previous run> for a follow-up)")
        if a.add_topic or a.drop_topic or a.add_langs or a.drop_langs:
            raise ValueError("--add-/--drop- options need --from <previous run>")
        kw = {"topics": a.topic, "langs": a.langs, "months": a.months or (None if a.start else 24),
              "start": a.start, "end": None if a.end == "latest" else a.end,
              "max_redirects": 25 if a.max_redirects is None else a.max_redirects,
              "question": a.question, "relative": bool(a.relative)}
    res = run(**kw, out_dir=a.out, parent=parent)
    if a.json:  # compact: interpretations + numbers, no monthly arrays (those are in monthly.csv)
        from .analyze import business_frame, rankings, reading
        def slim(s):
            out = {k: v for k, v in s.items() if k != "monthly"}
            return out | ({"reading": reading(s)} if s["status"] == "ok" else {})
        print(json.dumps({"rankings": rankings(res), "business_frame": business_frame(res),
                          "series": [slim(s) for s in res["series"]],
                          "editions": res.get("editions"), "params": res["params"], "run_id": res["run_id"],
                          "followup": res.get("followup"), "out_dir": res["out_dir"]}, ensure_ascii=False, indent=1))
    else:
        print(summary_text(res))
    return 0


def cmd_report(a) -> int:
    from .report import build_report
    out = build_report(a.analysis, a.out, title=a.title, findings=a.findings,
                       recommendation=a.recommendation, lang=a.lang, check_numbers=not a.allow_other_numbers)
    print(f"Report written: {out}")
    return 0


def cmd_cache(a) -> int:
    from .api import cache_stats, clear_cache
    if a.clear:
        clear_cache()
    print(json.dumps(cache_stats(), indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to a legacy code page
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="wt", description="Wikipedia pageview interest analysis")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("resolve", help="find the article for a topic in each language (check before analyzing)")
    r.add_argument("--topic", action="append", required=True,
                   help="free text, QID, 'lang:Exact title', or 'Label=part1+part2'. Repeatable.")
    r.add_argument("--langs", type=_langs, required=True, help="comma-separated Wikipedia codes, e.g. pl,cs")
    r.add_argument("--json", action="store_true")
    r.set_defaults(fn=cmd_resolve)

    se = sub.add_parser("search", help="full-text search one language edition (when resolve finds no article)")
    se.add_argument("query", help="search words, ideally in that language")
    se.add_argument("--lang", required=True)
    se.add_argument("--limit", type=int, default=8)
    se.set_defaults(fn=cmd_search)

    an = sub.add_parser("analyze", help="fetch views, compute trends and trust checks, draw chart")
    an.add_argument("--from", dest="from_", metavar="RUN",
                    help="follow-up: previous run folder or analysis.json; its parameters are kept unless changed here")
    an.add_argument("--topic", action="append", help="same formats as resolve. Repeatable. With --from: replaces the topics")
    an.add_argument("--add-topic", action="append", help="with --from: add a topic")
    an.add_argument("--drop-topic", action="append", help="with --from: remove a topic (spec or label)")
    an.add_argument("--langs", type=_langs, help="comma-separated codes. With --from: replaces the languages")
    an.add_argument("--add-langs", type=_langs, help="with --from: add languages")
    an.add_argument("--drop-langs", type=_langs, help="with --from: remove languages")
    an.add_argument("--months", type=int, help="window length ending at the last full month (default 24)")
    an.add_argument("--start", help="YYYY-MM (overrides --months)")
    an.add_argument("--end", help="YYYY-MM (default: last full month; with --from: 'latest' = move to the last full month)")
    an.add_argument("--max-redirects", type=int, help="redirects counted per article (default 25; 0 = none)")
    an.add_argument("--question", help="the user's question, stored and shown in the report")
    an.add_argument("--relative", action=argparse.BooleanOptionalAction, default=None,
                    help="chart views as an index (relative growth) instead of absolute views/day; "
                         "--no-relative turns it off in a follow-up")
    an.add_argument("--out", help="output directory (default ./wikitrend-out/<slug>)")
    an.add_argument("--json", action="store_true", help="compact JSON (for scripts/debugging; answer from the default text output)")
    an.set_defaults(fn=cmd_analyze)

    rp = sub.add_parser("report", help="one-page PDF from analysis.json")
    rp.add_argument("--analysis", required=True, help="path to analysis.json")
    rp.add_argument("--out", required=True, help="output .pdf path")
    rp.add_argument("--title")
    rp.add_argument("--findings", help="text/markdown file with '- ' bullets (max 6); default: auto findings")
    rp.add_argument("--recommendation", help="a sentence or a file with bullets")
    rp.add_argument("--lang", default="en", choices=["en", "uk"], help="report labels language")
    rp.add_argument("--allow-other-numbers", action="store_true",
                    help="skip the check that every %% in findings/recommendation comes from this analysis")
    rp.set_defaults(fn=cmd_report)

    c = sub.add_parser("cache", help="show (or --clear) the local cache")
    c.add_argument("--clear", action="store_true")
    c.set_defaults(fn=cmd_cache)

    a = p.parse_args(argv)
    try:
        return a.fn(a)
    except (ApiError, ValueError, RuntimeError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
