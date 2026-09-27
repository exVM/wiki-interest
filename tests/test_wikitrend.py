"""Offline tests: synthetic series with known answers + a full pipeline run with the API mocked."""

import re
from datetime import date, timedelta

import numpy as np
import pytest

from wikitrend import analyze, api, stats
from wikitrend.resolve import parse_topic

RNG = np.random.default_rng(7)
START = date(2023, 1, 1)
DAYS = stats.day_range(START, date(2024, 12, 31))


def synth(growth_per_year=0.0, base=500.0, noise=0.1, season=0.0, spike_at=None, spike_x=20):
    t = np.arange(len(DAYS)) / 365.0
    v = base * (1 + growth_per_year) ** t
    v *= 1 + season * np.sin(2 * np.pi * t)
    v *= np.exp(RNG.normal(0, noise, len(DAYS)))
    if spike_at is not None:
        v[spike_at:spike_at + 5] *= spike_x
    return np.round(v)


def metrics(views, totals=None):
    totals = totals if totals is not None else np.full(len(DAYS), 1e6)
    extra = {"redirects_counted": 0, "redirect_share": 0.0, "project_avg_daily_views": 1e6,
             "project_trend": stats.trend([1e6] * 24)}
    return analyze._series_metrics(DAYS, views, totals, extra)


def test_known_growth_is_recovered():
    m = metrics(synth(growth_per_year=0.30))
    assert 20 < m["trend"]["pct_per_year"] < 40
    assert m["trend"]["ci_low"] > 0
    a = m["assessment"]["absolute"]
    assert a["verdict"] == "rising" and a["confidence"] == "high"


def test_flat_series_has_no_trend():
    m = metrics(synth(growth_per_year=0.0))
    assert m["assessment"]["absolute"]["verdict"] == "no clear trend"


def test_spike_is_detected_and_does_not_fake_growth():
    # a big 5-day event late in a flat series
    m = metrics(synth(spike_at=len(DAYS) - 60, spike_x=40))
    assert m["spikes"]["n_events"] >= 1
    assert m["spikes"]["share_of_views"] > 0.05
    ex = m["trend_ex_spikes"]
    assert ex["ci_low"] <= 0 <= ex["ci_high"]


def test_share_removes_platform_decline():
    # topic falls 20%/yr but the whole wiki falls just as fast -> share flat
    decline = (0.8) ** (np.arange(len(DAYS)) / 365.0)
    views = np.round(500 * decline * np.exp(RNG.normal(0, 0.05, len(DAYS))))
    m = metrics(views, totals=1e6 * decline)
    assert m["assessment"]["absolute"]["verdict"] == "falling"
    assert m["assessment"]["share"]["verdict"] == "no clear trend"


def test_seasonality_detected():
    m = metrics(synth(season=0.5, noise=0.05))
    assert m["seasonality"]["strong"]


def test_steady_decline_is_not_seasonal():
    m = metrics(synth(growth_per_year=-0.5, noise=0.05))
    assert not m["seasonality"]["strong"]


def test_low_volume_lowers_confidence():
    m = metrics(synth(growth_per_year=0.5, base=8, noise=0.3))
    assert m["assessment"]["absolute"]["confidence"] != "high"


def test_period_change_yoy():
    pc = stats.period_change([10.0] * 12 + [12.0] * 12)
    assert pc["kind"] == "yoy" and pc["pct"] == 20.0


def test_month_window_whole_months():
    s, e = analyze.month_window(24, None, "2026-08")
    assert s == date(2024, 9, 1) and e == date(2026, 8, 31)
    s, e = analyze.month_window(None, "2025-01", "2025-03")
    assert (s, e) == (date(2025, 1, 1), date(2025, 3, 31))


def test_parse_topic():
    assert parse_topic("Learning English=Q1860+en:English as a second or foreign language") == (
        "Learning English", ["Q1860", "en:English as a second or foreign language"])
    assert parse_topic("astronomy") == (None, ["astronomy"])


@pytest.fixture
def tmp_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("WIKITREND_CACHE", str(tmp_path / "c.sqlite"))
    api._local.__dict__.clear()
    yield tmp_path
    api._local.__dict__.clear()


def test_cache_downloads_only_missing_days(tmp_cache, monkeypatch):
    calls = []

    def fake(project, article, agent, a, b):
        calls.append((a, b))
        return {(a + timedelta(days=i)).isoformat(): 5 for i in range((b - a).days + 1)}

    monkeypatch.setattr(api, "_download", fake)
    api.daily_views("xx.wikipedia.org", "T", date(2024, 1, 1), date(2024, 1, 31))
    api.daily_views("xx.wikipedia.org", "T", date(2024, 1, 10), date(2024, 1, 20))  # fully cached
    got = api.daily_views("xx.wikipedia.org", "T", date(2023, 12, 1), date(2024, 2, 29))
    assert calls == [(date(2024, 1, 1), date(2024, 1, 31)),
                     (date(2023, 12, 1), date(2023, 12, 31)), (date(2024, 2, 1), date(2024, 2, 29))]
    assert len(got) == 91


def test_full_pipeline_offline(tmp_cache, monkeypatch, tmp_path):
    """resolve -> fetch -> metrics -> chart -> PDF, with the network mocked."""
    from wikitrend import report

    def fake_resolve(spec, langs):
        return {"spec": spec, "label": "Astronomy", "missing_langs": ["xx"],
                "items": [{"qid": "Q333", "label": "Astronomy", "description": "science",
                           "titles": {l: "Astro" for l in langs if l != "xx"},
                           "warnings": [], "other_candidates": []}]}

    def fake_views(project, article, start, end, agent="user"):
        days = stats.day_range(start, end)
        base = 1e6 if article is None else (300 if project.startswith("uk") else 900)
        growth = 0.0 if article is None else (0.25 if project.startswith("uk") else -0.1)
        return {d.isoformat(): int(base * (1 + growth) ** (i / 365) * (1 + 0.05 * np.sin(i)))
                for i, d in enumerate(days)}

    monkeypatch.setattr(analyze, "resolve_topic", fake_resolve)
    monkeypatch.setattr(analyze, "redirects_to", lambda *a: [])
    monkeypatch.setattr(analyze, "daily_views", fake_views)
    monkeypatch.setattr(analyze, "monthly_devices", lambda p, a, b: {k: 13_000_000 for k in _month_keys(a, b)})
    res = analyze.run(["astronomy"], ["uk", "pl", "xx"], end="2026-08", out_dir=str(tmp_path / "run"))
    by_lang = {s["lang"]: s for s in res["series"]}
    assert by_lang["uk"]["assessment"]["share"]["verdict"] == "rising"
    assert by_lang["pl"]["assessment"]["share"]["verdict"] == "falling"
    assert by_lang["xx"]["status"] == "no article in this language"
    text = analyze.summary_text(res)
    assert "No linked article in: xx" in text
    assert "13.0M devices/month" in text and not re.search(r"edits?|per human edit", text, re.I)
    pdf = report.build_report(str(tmp_path / "run" / "analysis.json"), str(tmp_path / "r.pdf"), lang="uk")
    data = open(pdf, "rb").read()
    assert data.startswith(b"%PDF") and data.count(b"/Type /Page\n") + data.count(b"/Type /Page ") <= 1


# --- audit fixes -----------------------------------------------------------------

def test_trend_is_unit_invariant_for_small_values():
    """The old log(x+1) flattened small per-million series; safe_log must not."""
    y = [0.05 * 0.6 ** (i / 12) for i in range(24)]  # -40%/yr at ~0.05 per million
    small, big = stats.trend(y), stats.trend([v * 1000 for v in y])
    assert small["pct_per_year"] == pytest.approx(big["pct_per_year"], abs=0.5)
    assert -45 < small["pct_per_year"] < -35
    assert small["r2"] > 0.99


def test_describe_and_same_month_yoy():
    months = [f"{2023 + i // 12}-{i % 12 + 1:02d}" for i in range(24)]
    vals = [100.0 + i for i in range(24)]
    vals[5] = 300.0
    d = stats.describe(months, vals)
    assert d["peak"] == {"month": "2023-06", "value": 300.0}
    assert d["min"]["month"] == "2023-01" and d["last_month"]["month"] == "2024-12"
    y = stats.same_month_yoy(months, vals)
    assert y["vs_month"] == "2023-12" and y["pct"] == pytest.approx(12 / 111 * 100, abs=0.1)
    assert y["months_compared"] == 12 and y["months_up"] == 11
    assert stats.same_month_yoy(months[:12], vals[:12]) is None
    pc = stats.period_change(vals)
    assert pc["abs_change"] == pytest.approx(pc["after_avg_daily"] - pc["before_avg_daily"], abs=0.1)


def test_dips_and_gaps_are_flagged_and_reduce_coverage():
    v = synth(noise=0.05)
    v[300:303] = 20       # sharp dip
    v[500:510] = 0        # outage / page move
    m = metrics(v)
    an = m["anomalies"]
    assert an["dips"] and an["dips"][0]["start"] == DAYS[300].isoformat()
    assert an["gaps"] and an["gap_days"] == 10
    assert m["coverage"] == pytest.approx((len(DAYS) - 10) / len(DAYS), abs=1e-3)
    assert "Flagged, not removed" in analyze.reading({"topic": "t", "lang": "uk", "titles": ["A"], **m})


def test_long_zero_gap_is_flagged():
    v = synth(noise=0.05)
    v[400:440] = 0        # 40 days: the rolling median inside the run is 0 itself
    an = metrics(v)["anomalies"]
    assert an["gap_days"] == 40 and an["gaps"][0]["baseline"] > 300


def test_article_created_mid_window_is_not_growth():
    v = synth(noise=0.05)
    v[:250] = 0           # created in September 2023
    m = metrics(v)
    assert m["stats_from"] == "2023-10" and m["n_months"] == 15
    assert m["assessment"]["absolute"]["verdict"] != "rising"
    assert len(m["monthly"]["months"]) == 24  # charts keep the whole window
    assert "No views before 2023-10" in analyze.reading({"topic": "t", "lang": "uk", "titles": ["A"], **m})


def test_recent_level_shift_lowers_confidence():
    v = synth(noise=0.0)
    v[-92:] *= 1.6        # a jump in the last 3 months that a 24-month slope does not show
    m = metrics(v)
    ab = m["assessment"]["absolute"]
    failed = [c["check"] for c in ab["checks"] if not c["ok"]]
    assert "recent_level_consistent" in failed and ab["confidence"] != "high"
    assert "recent_level_consistent" not in [c["check"] for c in metrics(synth(noise=0.0))["assessment"]
                                             ["absolute"]["checks"] if not c["ok"]]


def test_composite_pins_are_language_scoped(monkeypatch):
    from wikitrend import resolve

    def fake_part(part, langs):
        if part.startswith("pl:"):
            return {"qid": "Q1", "label": "A", "titles": {"pl": "A-pl", "uk": "A-uk"}, "warnings": [],
                    "other_candidates": []}
        if part == "Q9":
            return {"qid": "Q9", "label": "C", "titles": {"uk": "C-uk"}, "warnings": [], "other_candidates": []}
        return {"qid": "Q2", "label": "B", "titles": {"cs": "B-cs", "uk": "B-uk"}, "warnings": [],
                "other_candidates": []}
    monkeypatch.setattr(resolve, "resolve_part", fake_part)
    r = resolve.resolve_topic("X=pl:A-pl+cs:B-cs", ["pl", "cs", "uk"])
    assert [it["titles"] for it in r["items"]] == [{"pl": "A-pl"}, {"cs": "B-cs"}]
    assert r["missing_langs"] == ["uk"] and not r["warnings"]
    r = resolve.resolve_topic("X=pl:A-pl+Q2", ["pl", "cs", "uk"])  # unpinned part adds to uk only
    assert any("not like-for-like" in w for w in r["warnings"]) is False  # every language sums 1
    r = resolve.resolve_topic("X=Q9+Q2", ["pl", "cs", "uk"])  # uk sums two articles, others one
    assert any("not like-for-like" in w for w in r["warnings"])


def test_business_frame_and_compare_chart(tmp_cache, monkeypatch, tmp_path):
    from wikitrend import charts

    def fake_resolve(spec, langs):
        return {"spec": spec, "label": "T", "missing_langs": [], "warnings": [],
                "items": [{"qid": "Q1", "label": "T", "description": "", "titles": {l: "T" for l in langs},
                           "warnings": [], "other_candidates": []}]}

    def fake_views(project, article, start, end, agent="user"):
        days = stats.day_range(start, end)
        base = 1e6 if article is None else (3000 if project.startswith("de") else 200)
        g = 0.0 if article is None else (0.4 if project.startswith("de") else 0.0)
        return {d.isoformat(): int(base * (1 + g) ** (i / 365) * (1 + 0.03 * np.sin(i))) for i, d in enumerate(days)}

    monkeypatch.setattr(analyze, "resolve_topic", fake_resolve)
    monkeypatch.setattr(analyze, "redirects_to", lambda *a: [])
    monkeypatch.setattr(analyze, "daily_views", fake_views)
    monkeypatch.setattr(analyze, "monthly_devices", lambda p, a, b: {k: 1e7 for k in _month_keys(a, b)})
    res = analyze.run(["t"], ["de", "pl"], end="2026-08", out_dir=str(tmp_path / "r"), relative=True)
    assert res["compare_chart"] and (tmp_path / "r" / "compare.png").is_file()
    en, uk = analyze.business_frame(res), analyze.business_frame(res, "uk")
    assert en[0].startswith("Signal: de gain(s) share") and "Hypothesis" in en[0]
    assert "Signal: largest audience de" in en[1]
    assert uk[0].startswith("Сигнал: de") and "Гіпотеза" in uk[0]
    assert charts.index_base_text(24) == "average of the first 12 months = 100"
    assert charts.index_base_text(12) == "average of the first 3 months = 100"
    assert "Volume & stability" in analyze.summary_text(res)


# --- follow-ups -------------------------------------------------------------------

def _mock_pipeline(monkeypatch, growth=None):
    growth = growth or {"de": 0.4}

    def fake_resolve(spec, langs):
        return {"spec": spec, "label": spec.title(), "missing_langs": [], "warnings": [],
                "items": [{"qid": "Q" + str(len(spec)), "label": spec, "description": "",
                           "titles": {l: spec.title() for l in langs}, "warnings": [], "other_candidates": []}]}

    def fake_views(project, article, start, end, agent="user"):
        days = stats.day_range(start, end)
        g = 0.0 if article is None else growth.get(project[:2], 0.0)
        base = 1e6 if article is None else 400
        t0 = (start - date(2020, 1, 1)).days  # the same day always has the same value, whatever the window
        return {d.isoformat(): int(base * (1 + g) ** ((t0 + i) / 365) * (1 + 0.03 * np.sin(t0 + i)))
                for i, d in enumerate(days)}

    monkeypatch.setattr(analyze, "resolve_topic", fake_resolve)
    monkeypatch.setattr(analyze, "redirects_to", lambda *a: [])
    monkeypatch.setattr(analyze, "daily_views", fake_views)
    monkeypatch.setattr(analyze, "monthly_devices", lambda p, a, b: {k: 1e7 for k in _month_keys(a, b)})


def _args(**kw):
    from argparse import Namespace
    base = dict(topic=None, add_topic=None, drop_topic=None, langs=None, add_langs=None, drop_langs=None,
                months=None, start=None, end=None, max_redirects=None, question=None, relative=None)
    return Namespace(**base | kw)


def test_followup_changes_only_the_given_parameter():
    from wikitrend.followup import merge_params
    parent = {"params": {"topics": ["a", "b"], "langs": ["pl", "cs"], "start": "2024-09-01", "end": "2026-08-31",
                         "max_redirects": 10, "relative": True},
              "question": "q", "topics": [{"spec": "a", "label": "A"}, {"spec": "b", "label": "B"}]}
    m = merge_params(parent, _args(add_langs=["uk"]))
    assert m == {"topics": ["a", "b"], "langs": ["pl", "cs", "uk"], "months": None, "start": "2024-09",
                 "end": "2026-08", "max_redirects": 10, "question": "q", "relative": True}
    m = merge_params(parent, _args(months=36))  # longer history, same end
    assert (m["months"], m["start"], m["end"], m["langs"]) == (36, None, "2026-08", ["pl", "cs"])
    m = merge_params(parent, _args(end="2025-12"))  # other end, same length
    assert (m["months"], m["start"], m["end"]) == (24, None, "2025-12")
    assert merge_params(parent, _args(drop_topic=["B"], langs=["de"]))["topics"] == ["a"]
    assert merge_params(parent, _args(relative=False))["relative"] is False
    with pytest.raises(ValueError):
        merge_params(parent, _args(drop_langs=["pl", "cs"]))


def test_followup_recomputes_and_reports_what_changed(tmp_cache, monkeypatch, tmp_path):
    from wikitrend import cli
    _mock_pipeline(monkeypatch)
    first = analyze.run(["t"], ["de", "pl"], end="2026-08", out_dir=str(tmp_path / "a"))
    raw = tmp_path / "a" / "raw"
    assert {p.name for p in raw.iterdir()} == {"articles.csv", "daily_views.csv",
                                              "unique_devices.csv", "series_daily.csv"}
    assert sum(1 for _ in open(raw / "series_daily.csv", encoding="utf-8")) == 1 + 2 * 730
    # same parameters again: same run id, every series unchanged
    same = analyze.run(["t"], ["de", "pl"], end="2026-08", out_dir=str(tmp_path / "a2"), parent=first)
    assert same["run_id"] == first["run_id"] and all(r["same"] for r in same["followup"]["series"])
    # a longer window: new run id, the window change is named and old numbers are declared invalid
    assert cli.main(["analyze", "--from", str(tmp_path / "a"), "--months", "36", "--out", str(tmp_path / "b")]) == 0
    import json
    res = json.loads((tmp_path / "b" / "analysis.json").read_text(encoding="utf-8"))
    fu = res["followup"]
    assert res["params"]["months"] == 36 and res["params"]["end"] == first["params"]["end"]
    assert [c["param"] for c in fu["changes"]] == ["window"] and fu["kept"] == ["topics", "langs", "max_redirects"]
    assert res["run_id"] != first["run_id"] and not fu["same_basis"]
    text = analyze.summary_text(res)
    assert text.startswith("FOLLOW-UP of run " + first["run_id"]) and "Do NOT reuse numbers" in text
    # adding a language keeps the window: the kept series must be identical
    assert cli.main(["analyze", "--from", str(tmp_path / "a"), "--add-langs", "uk", "--out", str(tmp_path / "c")]) == 0
    res = json.loads((tmp_path / "c" / "analysis.json").read_text(encoding="utf-8"))
    assert res["params"]["langs"] == ["de", "pl", "uk"] and res["params"]["start"] == first["params"]["start"]
    assert res["followup"]["same_basis"] and all(r["same"] for r in res["followup"]["series"])
    assert not res["followup"]["warnings"]
    # a parent made by an older version (no fingerprint): no false "data changed" claim
    old = {k: v for k, v in first.items() if k not in ("run_id", "data_hash")}
    fu = analyze.run(["t"], ["de", "pl"], end="2026-08", out_dir=str(tmp_path / "d"), parent=old)["followup"]
    assert fu["warnings"] == [fu["warnings"][0]] and "older version" in fu["warnings"][0]
    with pytest.raises(ValueError, match="folder of the run"):
        analyze.run(["t"], ["de"], end="2026-08", out_dir=str(tmp_path / "a"), parent=first | {"_path": str(
            tmp_path / "a" / "analysis.json")})
    assert "run (unknown)" in "\n".join(__import__("wikitrend.followup").followup.diff_text(fu))


def test_report_rejects_numbers_from_another_run(tmp_cache, monkeypatch, tmp_path):
    from wikitrend import report
    _mock_pipeline(monkeypatch)
    res = analyze.run(["t"], ["de", "pl"], end="2026-08", out_dir=str(tmp_path / "a"))
    de = next(s for s in res["series"] if s["lang"] == "de")
    good = f"- de share {fmt(de['trend_normalized']['pct_per_year'])} per year (95% CI).\n"
    f = tmp_path / "f.md"
    f.write_text(good, encoding="utf-8")
    js = str(tmp_path / "a" / "analysis.json")
    assert report.build_report(js, str(tmp_path / "ok.pdf"), findings=str(f))
    f.write_text(good + "- pl grew +987% last year.\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="987%"):
        report.build_report(js, str(tmp_path / "bad.pdf"), findings=str(f))
    assert report.build_report(js, str(tmp_path / "ok2.pdf"), findings=str(f), check_numbers=False)
    # numbers of another run: rejected, unless the bullet names that run's id
    _mock_pipeline(monkeypatch, {"de": 1.5})  # the data changed as well
    longer = analyze.run(["t"], ["de", "pl"], months=36, end="2026-08", out_dir=str(tmp_path / "b"), parent=res)
    only36 = sorted(report._allowed(longer) - report._allowed(res))
    own_values = {x for _, x in report._allowed(res)}
    sg, v = next((sg, v) for sg, v in only36 if v not in own_values)
    num = f"{sg}{v}%"
    assert report.stale_numbers(res, [f"3 years: {num}"], js) == [num]
    assert report.stale_numbers(res, [f"3 years (run {longer['run_id']}): {num}"], js) == []
    assert report.stale_numbers(res, [f"3 years (run 0123456789): {num}"], js) == [num]


def fmt(v):
    return analyze.fmt_pct(v)


def _month_keys(start, end):
    return [f"{y:04d}-{m:02d}" for y in range(start.year, end.year + 1) for m in range(1, 13)
            if (start.year, start.month) <= (y, m) <= (end.year, end.month)]
