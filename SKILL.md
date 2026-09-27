---
name: wiki-interest
description: Measures and compares public interest in topics across Wikipedia language editions using Wikimedia pageview and unique-device data - trends with confidence levels, seasonality, spikes, edition audience size, charts, and one-page PDF reports. Use when a founder or product team asks which topics, courses, or languages/markets to invest in next, whether interest in a topic is growing in some language, how much to trust that growth, or wants a shareable report on Wikipedia interest. Language codes are Wikipedia editions - uk = Ukrainian (not the United Kingdom), pl = Polish, cs = Czech.
compatibility: Requires uv (https://docs.astral.sh/uv/) and internet access to wikimedia.org and wikidata.org. Python deps are pinned in uv.lock and installed automatically on first run.
metadata:
  version: "0.1.0"
allowed-tools: Bash(uv:*) Read Write
---

# Wikipedia interest research

The code does the data work; your job is to pick the right articles, run the
commands, read the checks, and explain the result honestly.

## How to run

Every command is:

```
uv run --project <SKILL_DIR> wt <command> [options]
```

`<SKILL_DIR>` = the folder containing this SKILL.md. Run from the user's working
folder: results are written to `./wikitrend-out/<name>/`. First run installs
dependencies (~30 s). Downloads are cached, so repeated or extended queries are fast.

## Workflow

1. **Translate the request** into topics + Wikipedia language codes + window.
   - Languages are edition codes: pl, cs, uk, de, es, pt, tr, vi, id, ja, ... ("Polish" -> pl, "Czech" -> cs, "Ukrainian" -> uk).
     `uk` is always the Ukrainian edition, never the United Kingdom: "the uk Wikipedia" = Ukrainian. British readers use `en`.
   - "last two years" -> `--months 24` (default). Trust questions: use `--months 36` or more so trends span several seasonal cycles.
   - Topic formats for `--topic` (repeatable):
     - `"intermittent fasting"` - English words, looked up on Wikidata (best default)
     - `Q333` - a Wikidata id
     - `"uk:Астрономія"` - exact article title in one edition; other languages get the articles linked to it on Wikidata (a WARNING lists them)
     - `"Learning English=Q1860+Q...."` - a label, then several items whose views are summed
     - `"Fasting=pl:Głodówka lecznicza+cs:Přerušovaný půst"` - one pinned title per language; a
       `lang:Title` part counts **only in its language**. When you add a language to such a topic,
       add `+<code>:<Title>` too, or that language shows "no article". A WARNING "not like-for-like"
       means languages sum different numbers of articles - say so or pin one title per language.

2. **Check the article match** (skip only for obvious topics):
   ```
   uv run --project <SKILL_DIR> wt resolve --topic "intermittent fasting" --langs pl,cs
   ```
   Confirm the description matches the user's meaning. If a WARNING says
   disambiguation/list, or the description is wrong, re-run with a QID from the
   alternatives. If a language shows `(no article)`:
   ```
   uv run --project <SKILL_DIR> wt search --lang pl "post przerywany"
   ```
   (search words in that language). Use a hit as `--topic "pl:<Title>"` only if it
   is the same concept; if it is only related, call it a **proxy** in your answer.
   No article at all is itself a finding (less local content / smaller audience).

3. **Analyze** (also draws the chart):
   ```
   uv run --project <SKILL_DIR> wt analyze --topic "intermittent fasting" --langs pl,cs --months 24 --question "<user's question>"
   ```
   It prints a table, a **Volume & stability** table (median, peak, min, last month,
   same month a year earlier, last 12 m vs previous in views/day, volatility, trend-fit R²),
   a **Reading** line per series (plain-language interpretation), edition readers (unique
   devices), **Rankings** (pre-sorted orderings; `≈` = tie), failed checks/notes, and
   **Business framing** bullets. Files: `chart.png` (trends) and `compare.png` (bars:
   audience, change, volatility).

4. **Answer** from the Reading lines and the rules below. Quote numbers from the
   output; never invent them. Do not judge spikes or trends by looking at the chart
   - the Reading line already says whether peaks are spikes or seasonal. Structure:
   1. direct answer (rising / falling / no clear trend, and relative to the wiki overall)
   2. key numbers (views/day, %/year with CI)
   3. how far to trust it: confidence + what supports / weakens it
   4. limits of the data (one sentence)
   5. what it may mean for the business: copy the **Business framing** bullets
      (signal -> interpretation -> hypothesis -> next check) and call them hypotheses,
      never decisions

   For "largest / smallest / fastest / worst / weakest" claims copy the named ends from **Rankings** (each line ends with e.g. "largest: pl; smallest: cs") -
   do not compare numbers yourself. A confident *falling* result is trustworthy information too - say "the decline
   is reliable", not "the data can't be trusted". Trends are **per year**.
   Do not invent causes (news, holidays, competitors); if you suggest one, label it a hypothesis to check.

5. **Report** (when asked for something shareable, or for multi-market comparisons).
   Write 3-6 findings to a file as `- ` bullets (plain language, numbers included,
   `**bold**` allowed). Every number and every ordering in a bullet must appear in
   the analyze output **of the run passed to `--analysis`** - not of a parallel or earlier
   run (e.g. a number of another topic). To compare with an earlier run, name its run
   id in that bullet ("2 years, run 2805bc4acd: -15%/yr"). Then:
   ```
   uv run --project <SKILL_DIR> wt report --analysis wikitrend-out/<name>/analysis.json --out report.pdf --findings findings.md --recommendation "<one sentence>" --lang en
   ```
   `--lang uk` gives Ukrainian labels; write findings in the user's language. Without
   `--findings` the report uses auto-generated findings. The report is one A4 page;
   if it says it does not fit, shorten the bullets.

## How to interpret the output

- **Two verdicts per series.**
  - `abs` = absolute views ("are more people reading this?").
  - `share` = views per million views of that language edition ("is the topic gaining attention relative to everything else?").
  - Whole-Wikipedia traffic is falling in many languages (see `wiki overall trend`), so `abs` often falls even when interest holds up. **For comparing topics or markets, lead with `share`**; mention `abs` for audience size.
  - If `share` is `falling`, the topic declines *faster* than the whole edition - do not explain it away as "the general Wikipedia decline". Only a `share` of `no clear trend` with high confidence means "moves with the wiki".
- **Growth claims.**
  - Say "growing" only if the verdict is `rising`, and state its confidence.
  - `no clear trend` means the data cannot tell. It does not mean "stable", unless confidence is high.
- **Confidence** comes from the checks: volume ≥30 views/day, full coverage, significance, year-over-year agrees, not driven by spikes, seasonality covered, recent months agree.
  - `recent_level_consistent`: last 3 months vs the same months a year earlier (spikes capped). A move of 20%+ that the verdict does not show fails it - say "a recent shift, not yet a trend".
  - Article created mid-window: the Reading line starts with "No views before <month>"; statistics use only the months from then on, so a new article never looks like growth.
  - high = all checks pass; medium = one fails; low = two or more fail.
  - Name the failed checks when you explain trust.
- **Spikes** (news, TV, Main Page features) inflate short windows. Check whether the trend without spikes agrees.
- **Dips and zero-view gaps** are flagged ("Flagged, not removed" in Reading, notes). A gap may be an
  outage, a page move or a deletion - mention it; coverage already excludes gap days.
- **Seasonal topics** (school subjects peak in Sep/Oct; diets peak in January) must be compared year-over-year. A window under 24 months fails `seasonality_handled`.
- **Audience size**:
  - `views/day` = absolute audience in that edition.
  - `per 1M` = how prominent the topic is in that edition, which makes languages comparable.
- **Edits are not analyzed.** If asked about content quality or how actively an article is edited, say that this skill measures reading interest only; suggest reading the article to check.
- **Edition readers** (unique devices/month) = the size of the whole language edition's audience - a market-size context.
  - A device is not a person (one person uses several devices; shared devices count once). Use it as an approximate order of magnitude.
  - Compare editions with it; do not add it to topic views.
- **Always state the limits.** Pageviews show curiosity, not willingness to pay. One article is a proxy for a topic. People often read other editions (many Ukrainians read English or Russian Wikipedia). Human traffic only.

## Follow-up questions

When the user refines an earlier question (another topic, language or period), run a
follow-up of the run it refines. Pass **only what changed**; everything else is kept:
```
uv run --project <SKILL_DIR> wt analyze --from wikitrend-out/<previous run> --add-langs uk
```
- Language: `--add-langs uk`, `--drop-langs cs`, or `--langs de,pl` (replaces).
- Topic: `--add-topic "..."`, `--drop-topic "<label>"`, or `--topic ...` (replaces).
- Period: `--months 36` (same end, longer), `--end 2025-12` (same length, other end),
  `--end latest` (move to the last full month), or `--start 2023-01 --end 2025-12`.
  Data starts from 2015-07. Without a period option the exact previous window is kept.
- `--relative` (views chart as an index; base = average of the first 12 months, or 3 if
  under 24 months; `--no-relative` turns it off), `--max-redirects`, `--question` are kept unless given.

The output starts with a **FOLLOW-UP** block: what changed (old -> new), what was kept, what
was recomputed, and every series of both runs (previous -> now). Rules:
- Every number is recomputed; nothing is patched. After a change of **period** or **data**,
  numbers, verdicts, confidence and rankings of the earlier run are invalid: never quote
  them as current, never mix them with the new ones. Quote the new run only.
- When the user asks "what changed", use the previous -> now lines of the FOLLOW-UP block.
- Each run has a `Run <id>` line. Name the run id when you cite numbers from more than one run.
- A WARNING that a topic "now resolves to" other items means the article changed: pin it
  (`Q...` or `lang:Title`) and re-run, or say that the runs are not comparable.
- `wt report` refuses findings with a percentage that is not in that run (a number left over
  from an earlier run), unless the bullet names the run id it comes from. Fix the bullet from
  the current output; use `--allow-other-numbers` only for a percentage that is not a statistic.

Other follow-up notes:
- One question = one `analyze` run: put every topic and language of it in the same call
  (repeat `--topic`), not in separate runs - otherwise facts of different runs get mixed.
- A broad concept ("interest in learning English") → try 2-3 proxy articles (e.g. the language itself and "English as a second or foreign language") and check whether they agree. Use a separate `--topic` for each, so a missing article does not hide the others.
- Each run writes to its own folder (the run id is in the name), so earlier runs stay intact.
- Answer from the default text output. `--json` is only for scripts or for checking one detail (spike events, every check); monthly values are in `monthly.csv`.
- `analysis.json` stores the parameters, the run id and the data fingerprint of each run.
  `raw/` keeps the downloaded inputs unaggregated (daily views of every article, redirect and
  whole edition; unique devices) and `series_daily.csv`, the daily series the
  statistics were computed from. Use them to recheck a number; never edit them.

## Troubleshooting

- `ERROR: HTTP 429`: rate limited. Wait about a minute and retry; cached parts are not downloaded again.
- Windows "failed to persist temporary file" / path not found during install: the path is too long. Set `UV_PROJECT_ENVIRONMENT` to a short folder (e.g. `%TEMP%\wt-venv`) and retry.
- Method details: [references/methodology.md](references/methodology.md).
- Example reports: [assets/examples/](assets/examples/). Code: `scripts/wikitrend/` (the `wt` CLI).
