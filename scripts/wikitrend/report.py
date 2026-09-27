"""One-page PDF report from an analysis.json (+ optional agent-written findings)."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import matplotlib
from fpdf import FPDF

from .analyze import business_frame, fmt_pct
from .charts import index_span, series_label
from .stats import MONTH_NAMES

FONT_DIR = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
MAX_ROWS = 12
MAX_BULLETS = 6

L10N = {
    "en": {
        "findings": "Key findings", "recommendation": "Recommendation", "method": "Method, assumptions & limits",
        "business": "What it may mean for the business (hypotheses to validate)",
        "seasonal": " Yearly cycle: high in {m}, low in {l} - compare year-over-year.",
        "months": MONTH_NAMES,
        "notes": "Data notes: {x}.", "sum_note": "{s} = sum of {n} articles ({t})",
        "flag_note": "{s}: {x} flagged, not removed", "dips": "{n} dip(s)", "gaps": "{n} zero-view gap(s), {d} days",
        "index_note": ("Index = {m}-month average at the start of the window = 100 (not the first month). "
                       "{b}"),
        "bars_note": "Bars: change over {p}, and typical month-to-month change.",
        "p_yoy": "the last 12 months vs the previous 12", "p_half": "the 2nd half vs the 1st half of the window",
        "series": "Series", "views": "Views/day", "per_m": "Per 1M", "abs_t": "Views %/yr (CI)",
        "abs_v": "Views", "sh_t": "Share %/yr (CI)", "sh_v": "Share",
        "readers": "Edition readers (unique devices/month, avg. of last 12 months): {x}.",
        "window": "Window", "langs": "Languages", "generated": "Generated",
        "verdicts": {"rising": "rising", "falling": "falling", "no clear trend": "no clear trend",
                     "insufficient data": "too little data"},
        "confs": {"high": "high", "medium": "medium", "low": "low"},
        "group": "Share of the wiki's traffic {v}: {x}.", "per_year": "/yr",
        "title": "Wikipedia interest: {x}",
        "biggest": "Largest audiences (views/day): {x}.",
        "auto": "**{s}**: views {v} ({t}/yr, {c} confidence); share of the wiki's traffic {sv} ({st}/yr, {sc} confidence).",
        "missing": "No article in: {x}.",
        "method_text": (
            "Data: Wikimedia Pageviews API (CC0), human (user-agent) traffic, all platforms, daily data aggregated to "
            "average daily views per month; views of up to {r} redirects per article are included. Trend: Theil-Sen "
            "slope on log views with 95% CI and Mann-Kendall test (monthly autocorrelation makes p-values "
            "optimistic, so YoY change and spike-robustness are checked too). 'Per 1M' = views per million views "
            "of the whole language edition, which removes overall Wikipedia traffic shifts. Edition "
            "readers = Wikimedia unique devices (not people). Page views measure "
            "curiosity, not willingness to pay; edition size differs from speaker population; a single article is "
            "a proxy for a topic. Use as a signal for further validation."),
    },
    "uk": {
        "findings": "Ключові висновки", "recommendation": "Рекомендація", "method": "Метод, припущення та обмеження",
        "business": "Що це може означати для бізнесу (гіпотези для перевірки)",
        "seasonal": " Річний цикл: пік у {m}, спад у {l} - порівнюйте рік до року.",
        "months": ["січні", "лютому", "березні", "квітні", "травні", "червні", "липні", "серпні",
                   "вересні", "жовтні", "листопаді", "грудні"],
        "notes": "Примітки до даних: {x}.", "sum_note": "{s} = сума {n} статей ({t})",
        "flag_note": "{s}: {x} позначено, не видалено", "dips": "провалів: {n}",
        "gaps": "проміжків без переглядів: {n}, {d} дн.",
        "index_note": ("Індекс = середнє перших {m} місяців вікна = 100 (не перший місяць). {b}Підписи графіків "
                       "англійською."),
        "bars_note": "Стовпчики: зміна за {p} і типова зміна від місяця до місяця. ",
        "p_yoy": "останні 12 місяців проти попередніх 12", "p_half": "другу половину вікна проти першої",
        "series": "Ряд", "views": "Перегл./д", "per_m": "/1 млн", "abs_t": "Перегл. %/рік (ДІ)",
        "abs_v": "Перегляди", "sh_t": "Частка %/рік (ДІ)", "sh_v": "Частка",
        "readers": "Читачі розділу (унікальні пристрої/місяць, середнє за 12 міс.): {x}.",
        "window": "Період", "langs": "Мови", "generated": "Створено",
        "verdicts": {"rising": "зростає", "falling": "спадає", "no clear trend": "без тренду",
                     "insufficient data": "замало даних"},
        "confs": {"high": "висока", "medium": "середня", "low": "низька"},
        "group": "Частка трафіку розділу - {v}: {x}.", "per_year": "/рік",
        "title": "Інтерес у Вікіпедії: {x}",
        "biggest": "Найбільші аудиторії (переглядів/день): {x}.",
        "auto": "**{s}**: перегляди - {v} ({t}/рік, впевненість {c}); частка трафіку розділу - {sv} ({st}/рік, впевненість {sc}).",
        "missing": "Немає статті в: {x}.",
        "method_text": (
            "Дані: Wikimedia Pageviews API (CC0), лише людський трафік (agent=user), усі платформи; денні дані зведено до "
            "середніх переглядів на день за місяць; враховано перегляди до {r} перенаправлень на статтю. Тренд: "
            "нахил Тейла-Сена на логарифмі переглядів з 95% ДІ та тест Манна-Кендалла (автокореляція місячних даних "
            "робить p-значення оптимістичними, тому додатково перевіряються зміна рік до року та стійкість до "
            "сплесків). «На 1 млн» - перегляди на мільйон переглядів усього мовного розділу, що прибирає загальні "
            "зміни трафіку Вікіпедії. Читачі розділу - унікальні пристрої Wikimedia (не люди). Перегляди показують цікавість, а не готовність платити; розмір розділу не "
            "дорівнює кількості носіїв; одна стаття - лише наближення теми. Це сигнал для подальшої перевірки."),
    },
}


def _fit(pdf: FPDF, text: str, width: float) -> str:
    """Shorten text with an ellipsis so it never runs into the next table column."""
    if pdf.get_string_width(text) <= width:
        return text
    head, sep, tail = text.rpartition(" · ")  # "topic · lang": keep the language, shorten the topic
    if not sep:
        head, tail = text, ""
    tail = sep + tail
    while head and pdf.get_string_width(head + "…" + tail) > width:
        head = head[:-1]
    return head.rstrip() + "…" + tail


def _read_bullets(path: str | None) -> list[str]:
    if not path:
        return []
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(("- ", "* ")):
            out.append(line[2:].strip())
        elif line and not line.startswith("#"):
            out.append(line)
    return out


def _seasonal(s: dict, t: dict) -> str:
    se = s.get("seasonality")
    if not (se and se["strong"]):
        return ""
    m = lambda name: t["months"][MONTH_NAMES.index(name)]  # noqa: E731
    return t["seasonal"].format(m=m(se["peak_month"]), l=m(se["low_month"]))


def _data_notes(res: dict, t: dict) -> str | None:
    notes = []
    for s in res["series"]:
        if s["status"] == "ok" and len(s["titles"]) > 1:
            notes.append(t["sum_note"].format(s=series_label(s, res), n=len(s["titles"]), t=" + ".join(s["titles"])))
    for s in res["series"]:
        an = s.get("anomalies") or {}
        x = []
        if an.get("dips"):
            x.append(t["dips"].format(n=len(an["dips"])))
        if an.get("gaps"):
            x.append(t["gaps"].format(n=len(an["gaps"]), d=an["gap_days"]))
        if x:
            notes.append(t["flag_note"].format(s=series_label(s, res), x=", ".join(x)))
    return t["notes"].format(x="; ".join(notes)) if notes else None


def _auto_findings(res: dict, t: dict) -> list[str]:
    ok = [s for s in res["series"] if s["status"] == "ok"]
    out = []
    if len(ok) > MAX_BULLETS - 1:  # too many series for one bullet each: group them
        groups: dict[str, list[str]] = {}
        for s in sorted(ok, key=lambda s: -(s["trend_normalized"]["pct_per_year"] or -1e9)):
            sh = s["assessment"]["share"]
            groups.setdefault(sh["verdict"], []).append(
                f"{series_label(s, res)} {fmt_pct(s['trend_normalized']['pct_per_year'])}{t['per_year']} ({t['confs'][sh['confidence']]})")
        for v in ("rising", "no clear trend", "falling", "insufficient data"):
            if v in groups:
                out.append(t["group"].format(v=t["verdicts"][v], x=", ".join(groups[v])))
        top = sorted(ok, key=lambda s: -s["avg_daily_views"])[:5]
        out.append(t["biggest"].format(x=", ".join(f"{series_label(s, res)} {s['avg_daily_views']:,.0f}" for s in top)))
        ok = []
    for s in ok:
        ab, sh = s["assessment"]["absolute"], s["assessment"]["share"]
        out.append(t["auto"].format(
            s=series_label(s, res), v=t["verdicts"][ab["verdict"]], t=fmt_pct(s["trend"]["pct_per_year"]),
            c=t["confs"][ab["confidence"]], sv=t["verdicts"][sh["verdict"]],
            st=fmt_pct(s["trend_normalized"]["pct_per_year"]), sc=t["confs"][sh["confidence"]])
            + _seasonal(s, t))
    missing = [f"{s['topic']} [{s['lang']}]" for s in res["series"] if s["status"] != "ok"]
    if missing:
        out.append(t["missing"].format(x=", ".join(missing)))
    return out


_PCT = re.compile(r"([+\-−]?)(\d+(?:[.,]\d+)?)\s?%")


def _pcts(text: str) -> set[tuple[str, int]]:
    """(sign, rounded value) of every percentage; the unicode minus counts as '-'."""
    return {(sg.replace("−", "-"), round(float(v.replace(",", ".")))) for sg, v in _PCT.findall(text)}


_RUN_ID = re.compile(r"\b[0-9a-f]{10}\b")


def _allowed(res: dict) -> set[tuple[str, int]]:
    from .analyze import business_frame, summary_text
    own = {k: v for k, v in res.items() if k != "followup"}  # the follow-up block quotes the previous run
    return _pcts(summary_text(own) + " ".join(business_frame(own, "uk")))


def _other_runs(analysis_path: str | None) -> dict[str, Path]:
    """run_id -> analysis.json of the runs next to this one (wikitrend-out/*/analysis.json)."""
    if not analysis_path:
        return {}
    out = {}
    for p in Path(analysis_path).resolve().parent.parent.glob("*/analysis.json"):
        try:
            rid = json.loads(p.read_text(encoding="utf-8")).get("run_id")
        except (OSError, ValueError):
            continue
        if rid:
            out[rid] = p
    return out


def stale_numbers(res: dict, texts: list[str], analysis_path: str | None = None) -> list[str]:
    """Percentages in agent-written text that this analysis does not contain (e.g. left over from an
    earlier run with another window or data set). A signed number must match with its sign.

    A bullet that names another run's id ("2 years, run 2805bc4acd: -15%") may quote that run's numbers:
    an explicit, traceable comparison, not a stale number."""
    own = _allowed(res)
    runs = None
    bad = []
    for t in texts:
        allowed = own
        ids = set(_RUN_ID.findall(t)) - {res.get("run_id")}
        if ids:
            runs = _other_runs(analysis_path) if runs is None else runs
            for rid in ids & set(runs):
                allowed = allowed | _allowed(json.loads(runs[rid].read_text(encoding="utf-8")))
        unsigned = {v for sg, v in allowed if not sg}
        values = {v for _, v in allowed}
        for m in _PCT.finditer(t):
            (sg, v), = _pcts(m.group(0))
            if not (v in unsigned or ((sg, v) in allowed if sg else v in values)):
                bad.append(m.group(0).strip())
    return sorted(set(bad))


def build_report(analysis_path: str, out_pdf: str, title: str | None = None, findings: str | None = None,
                 recommendation: str | None = None, lang: str = "en", check_numbers: bool = True) -> str:
    """Lay the page out; if it does not fit, shrink the charts before giving up."""
    if check_numbers and (findings or recommendation):
        res = json.loads(Path(analysis_path).read_text(encoding="utf-8"))
        rec = _read_bullets(recommendation) if recommendation and Path(recommendation).is_file() else [recommendation or ""]
        bad = stale_numbers(res, _read_bullets(findings) + rec, analysis_path)
        if bad:
            raise RuntimeError(
                f"findings/recommendation quote {', '.join(bad)}, which run {res.get('run_id', '?')} does not "
                "contain - probably numbers from an earlier run (another period or data set). Take every number "
                "from this run's analyze output. To compare with an earlier run, name its run id in that "
                "bullet (e.g. '2 years, run 2805bc4acd: -15%/yr'). --allow-other-numbers only if the number "
                "is not a statistic.")
    # keep the main chart readable: drop the bar panel before shrinking below 72%
    for scale, bars in ((1.0, True), (0.85, True), (0.72, True), (0.85, False), (0.72, False), (0.6, False)):
        pdf = _layout(analysis_path, title, findings, recommendation, lang, scale, bars)
        if pdf is not None:
            pdf.output(out_pdf)
            return out_pdf
    raise RuntimeError("report does not fit on one page - shorten findings/recommendation")


def _layout(analysis_path: str, title: str | None, findings: str | None, recommendation: str | None,
            lang: str, scale: float, bars: bool = True) -> FPDF | None:
    res = json.loads(Path(analysis_path).read_text(encoding="utf-8"))
    t = L10N.get(lang, L10N["en"])
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(False)
    pdf.set_margins(12, 11, 12)
    pdf.add_font("dv", "", str(FONT_DIR / "DejaVuSans.ttf"))
    pdf.add_font("dv", "B", str(FONT_DIR / "DejaVuSans-Bold.ttf"))
    pdf.add_font("dv", "I", str(FONT_DIR / "DejaVuSans-Oblique.ttf"))
    pdf.add_page()
    w = pdf.epw
    p = res["params"]

    pdf.set_font("dv", "B", 15)
    pdf.multi_cell(w, 7, title or t["title"].format(x=", ".join(x["label"] for x in res["topics"])),
                   new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("dv", "", 8)
    pdf.set_text_color(82, 81, 78)
    pdf.multi_cell(w, 4, f"{t['window']}: {p['start']} - {p['end']}   |   {t['langs']}: {', '.join(p['langs'])}   |   "
                         f"{t['generated']}: {date.today().isoformat()}   |   run {res.get('run_id', '?')}",
                   new_x="LMARGIN", new_y="NEXT")
    if res.get("question"):
        pdf.set_font("dv", "I", 8)
        pdf.multi_cell(w, 4, res["question"][:300], new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(11, 11, 11)
    pdf.ln(2)

    def heading(text: str) -> None:
        pdf.set_font("dv", "B", 10.5)
        pdf.cell(w, 6, text, new_x="LMARGIN", new_y="NEXT")

    def bullets(items: list[str], size: float = 8.5, md: bool = True) -> None:
        pdf.set_font("dv", "", size)
        lh = size * 0.5
        for b in items[:MAX_BULLETS]:
            pdf.set_x(pdf.l_margin)
            pdf.cell(4, lh, "•")
            pdf.multi_cell(w - 4, lh, b[:600], markdown=md, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(0.5)

    heading(t["findings"])
    bullets(_read_bullets(findings) or _auto_findings(res, t))
    rec = _read_bullets(recommendation) if recommendation and Path(recommendation).is_file() else (
        [recommendation] if recommendation else [])
    if rec:
        heading(t["recommendation"])
        bullets(rec)
    frame = business_frame(res, lang, tag=lambda s: series_label(s, res))
    if frame:
        heading(t["business"])
        bullets(frame, 7.8, md=False)  # plain text: '--months' must not become underline

    for key in ("chart", "compare_chart") if bars else ("chart",):
        img = res.get(key)
        if img and Path(img).is_file():
            from PIL import Image
            with Image.open(img) as im:
                ratio = im.height / im.width
            pdf.ln(1)
            iw = w * scale
            pdf.image(img, x=pdf.l_margin + (w - iw) / 2, y=pdf.get_y(), w=iw, h=iw * ratio)
            pdf.set_y(pdf.get_y() + iw * ratio + 1)
    n_months = len(next((s["monthly"]["months"] for s in res["series"] if s["status"] == "ok"), []))
    if n_months:
        ok0 = next(s for s in res["series"] if s["status"] == "ok")
        pdf.set_font("dv", "I", 6.8)
        pdf.set_text_color(82, 81, 78)
        per = t["p_yoy"] if ok0["period_change"]["kind"] == "yoy" else t["p_half"]
        b = t["bars_note"].format(p=per) if bars and res.get("compare_chart") else ""
        pdf.multi_cell(w, 3.3, t["index_note"].format(m=index_span(n_months), b=b).strip(),
            new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(11, 11, 11)
        pdf.ln(1)

    # Metrics table
    cols = [(t["series"], 0.18), (t["views"], 0.085), (t["per_m"], 0.065), (t["abs_t"], 0.19),
            (t["abs_v"], 0.145), (t["sh_t"], 0.19), (t["sh_v"], 0.145)]
    pdf.set_font("dv", "B", 7.5)
    pdf.set_fill_color(240, 239, 236)
    for name, frac in cols:
        pdf.cell(w * frac, 5, name, border=0, fill=True)
    pdf.ln()
    pdf.set_font("dv", "", 7.5)
    rows = [s for s in res["series"] if s["status"] == "ok"]
    for s in rows[:MAX_ROWS]:
        ab, sh = s["assessment"]["absolute"], s["assessment"]["share"]
        ci = lambda x: f"{fmt_pct(x['pct_per_year'])} ({fmt_pct(x['ci_low'])}..{fmt_pct(x['ci_high'])})"  # noqa: E731
        vals = [series_label(s, res), f"{s['avg_daily_views']:,.0f}", f"{s['per_million_views']:.1f}",
                ci(s["trend"]), f"{t['verdicts'][ab['verdict']]} · {t['confs'][ab['confidence']]}",
                ci(s["trend_normalized"]), f"{t['verdicts'][sh['verdict']]} · {t['confs'][sh['confidence']]}"]
        for (_, frac), v in zip(cols, vals):
            pdf.cell(w * frac, 4.6, _fit(pdf, v, w * frac - 1))
        pdf.ln()
    if len(rows) > MAX_ROWS:
        pdf.set_font("dv", "I", 7)
        pdf.cell(w, 4, f"+{len(rows) - MAX_ROWS} more in analysis.json", new_x="LMARGIN", new_y="NEXT")
    eds = sorted(((l, e) for l, e in (res.get("editions") or {}).items() if e), key=lambda x: -x[1]["devices_per_month"])
    if eds:
        pdf.set_font("dv", "", 7.5)
        pdf.set_text_color(82, 81, 78)
        pdf.multi_cell(w, 4.2, t["readers"].format(x=", ".join(
            f"{l} {e['devices_per_month'] / 1e6:.1f}M" for l, e in eds)), new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(11, 11, 11)
    notes = _data_notes(res, t)
    if notes:
        pdf.set_font("dv", "", 7.5)
        pdf.set_text_color(82, 81, 78)
        pdf.multi_cell(w, 4.2, notes, new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(11, 11, 11)

    pdf.ln(2)
    heading(t["method"])
    pdf.set_font("dv", "", 6.8)
    pdf.set_text_color(82, 81, 78)
    pdf.multi_cell(w, 3.3, t["method_text"].format(r=p["max_redirects"]), new_x="LMARGIN", new_y="NEXT")
    if pdf.get_y() > pdf.h - 8 or pdf.page_no() > 1:
        return None
    return pdf
