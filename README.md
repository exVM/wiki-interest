# wiki-interest - an Agent Skill for Wikipedia interest research

Helps B2C founders decide which topics to build and which language markets to
enter, using public Wikimedia pageview data. The agent turns a question into a
reproducible analysis: trends with confidence levels, seasonality and spike
checks, a chart, and a one-page PDF to share.

Format: [Agent Skills](https://agentskills.io/specification). The agent reads
[SKILL.md](SKILL.md). This README is for humans.

## Setup

Only [uv](https://docs.astral.sh/uv/) and internet access are needed. Python and
all dependencies are pinned in `uv.lock` and installed on first run:

```bash
uv run --project wiki-interest wt analyze --topic astronomy --langs uk,pl --months 36
```

Install as a skill: copy the folder to `.claude/skills/wiki-interest/` (project
scope) or `~/.claude/skills/wiki-interest/` (user scope). Tests: `uv run pytest`.

Optional environment variables:
- `WIKITREND_CONTACT`: your email or URL for the Wikimedia User-Agent (recommended by Wikimedia policy).
- `WIKITREND_CACHE`: cache location (default `.cache/wikitrend.sqlite` inside the skill).
- `UV_PROJECT_ENVIRONMENT`: a short venv path (needed on Windows when the skill path is very long).

## Example questions

- "Compare the growth of interest in intermittent fasting in the pl and cs editions over the last 2 years"
- "Is interest in astronomy growing in the uk edition, and how much can we trust that growth?"
- "Our app teaches English. Compare interest in English across pl, cs, uk, es, tr and vi, recommend audiences to explore next, and make a PDF"
- Follow-ups: "add id and de", "use 4 years instead", "the same for Spanish", "make the report in Ukrainian"

## Design

```
SKILL.md                 workflow + interpretation rules (what the agent reads)
references/methodology.md  statistics details, loaded only on demand
scripts/wikitrend/
  api.py      HTTP (retry/backoff/Retry-After) + SQLite cache: daily rows + coverage ranges,
              only missing date edges are downloaded; Wikidata/MediaWiki metadata cached 30 days;
              monthly unique devices per edition (cached 1 day)
  resolve.py  topic -> Wikidata item -> article per language (+ warnings: no exact match,
              disambiguation/list pages, alternatives); redirects; local full-text search
  stats.py    Theil-Sen trend + CI + R² (unit-invariant log offset), Mann-Kendall, YoY and
              same-month YoY, robust spike/dip/zero-gap detection, volatility and other
              descriptive metrics, detrended seasonality, confidence checks
  analyze.py  orchestration, per-series metrics, "Volume & stability" table, plain-language
              "Reading" + "Rankings" (ties shown), deterministic "Business framing" (en/uk)
  charts.py   chart.png: views (absolute or --relative index) | share of edition traffic,
              index base = first 12 (or 3) months; compare.png: audience / change / volatility bars
  report.py   one-page A4 PDF (en/uk labels), agent-written or auto findings (with seasonality),
              business framing, data notes; drops the bar panel, then shrinks charts, to fit
  cli.py      `wt resolve | search | analyze | report | cache`
assets/examples/  two PDFs produced by Claude Haiku 4.5 with this skill (scenario 3 + follow-up)
tests/        offline tests: synthetic series with known answers + mocked end-to-end pipeline
```

Key decisions:
- **The code does all numeric work; the model does language work.** The model picks
  articles, runs commands and phrases the answer. Verdicts, confidence, rankings and
  plain-language readings come from the code. This is what makes a small model reliable.
- **Two verdicts per series.** Views in absolute terms, and share of the edition's
  traffic. Whole-Wikipedia traffic falls 5-30%/year in many editions, so an absolute
  decline alone says little about relative interest.
- **Trust is explicit.** Confidence is high, medium or low, based on named checks:
  - volume
  - coverage
  - significance
  - year-over-year agreement
  - robustness to spikes
  - seasonal coverage
  - CI width

  The failed checks are printed.
- **Demand and market size together.** Pageviews show demand. Unique devices per edition give the size of each edition's audience (context, not market size). Edits are not analyzed.
- **Pageviews to business, never skipped.** Every run ends with signal -> interpretation ->
  hypothesis -> next check bullets built by code, so neither a small model nor the PDF
  without agent text jumps from "views fell" to "launch" or "don't launch".
- **Composite topics stay like-for-like.** A `lang:Title` part counts only in its language;
  a warning appears when languages would sum different numbers of articles.
- **Consistent follow-ups.** `wt analyze --from <previous run> --add-langs uk` (or `--months 36`,
  `--drop-topic ...`) keeps every other parameter, recomputes everything, and prints what
  changed, what was kept and each series previous -> now. After a change of period or data it
  says outright that the old numbers are invalid; `wt report` rejects findings whose
  percentages are not in that run. The cache means a follow-up only downloads the new data.
- **Raw data kept.** Each run folder has `raw/` with the downloaded daily views and
  devices, and the daily series the statistics were computed from; `analysis.json` has the run
  id and a fingerprint of the data.

## How the AI-generated work was verified

The code and texts were written with an AI assistant, then checked in four ways:

1. **Unit tests with known answers.**
   - Synthetic series: +30%/yr growth recovered; flat series gives "no clear trend".
   - Robustness: a 40x spike does not fake growth; a steady decline is not reported as seasonality.
   - Normalization: a platform-wide decline disappears in share.
   - Confidence: low volume lowers it.
   - Caching: only missing days are downloaded.
   - A mocked end-to-end run through the PDF.
2. **Cross-checks with the live API.** Daily sums were compared with the API's monthly
   endpoint (uk "Астрономія"). Findings were checked by hand, e.g. the uk astronomy
   decline is real and seasonal (school year), not an artifact of bot filtering. Unique
   devices were checked against the API directly.
3. **Rendered outputs were inspected.** Every PDF variant (en/uk, 1-8 series) was checked.
   This found and fixed: clipped text, a legend covering data, overflowing Ukrainian
   labels, series silently dropped from auto findings, and long "topic · lang" labels that
   ran into the next table column (now shortened, keeping the language).
4. **End-to-end runs on Claude Haiku 4.5** (`claude -p --model claude-haiku-4-5-20251001`)
   for all three scenarios plus a follow-up. Each run took 5-8 turns and cost $0.03-0.07.
   Transcript review found model errors, and each became a code or instruction fix:

   | Haiku error | Fix |
   |---|---|
   | Called seasonal September peaks "news spikes" | "Reading" line per series says spikes vs seasonal explicitly |
   | Read "-24%/yr" as "-24% over 3 years" | wording "per year" everywhere |
   | Said a medium-confidence decline "can't be trusted" | SKILL.md answer structure + "the decline is reliable" guidance |
   | Wrong orderings ("vi has the largest audience" when es is 2x larger) | pre-sorted **Rankings** lines; SKILL.md: copy orderings, don't compare |
   | Called "no clear trend" "stable" | Reading says "the data cannot tell ... do not call it stable" unless confidence is high |
   | Read raw `--json` and skipped the interpretations | `--json` made compact and includes Readings/Rankings; SKILL.md says answer from text |
   | Misstated peak month (a false 12-month "cycle" on a declining series) | seasonality is now computed after detrending; month names instead of numbers |
   | Read "the uk Wikipedia" as the United Kingdom and analyzed `en` | description + SKILL.md: `uk` = Ukrainian, never the UK |
   | Explained a falling *share* as "the general Wikipedia decline" | SKILL.md rule: falling share = faster than the whole edition |
   | Wrong bottom-end claims ("smallest audience", "falling fastest") | each Rankings line names both ends ("largest: pl; smallest: cs") |

   After the fixes, every number and ordering in the final Haiku answers matched the tool
   output. Remaining weakness: the model sometimes adds unsupported causal stories
   ("because of New Year's resolutions"). SKILL.md asks it to quote only the output.
   In one run it still once called uk (18 views/day) the smallest audience,
   next to cs (17), despite the ranking line; near-ties are where this happens.

## Known limits

- Pageviews measure curiosity, not purchase intent or the number of speakers.
- Readers of one language often use other editions, English above all.
- A single article is a proxy for a topic; for broad concepts compare 2-3 articles.
- Some concepts have no article in some editions (e.g. pl has no "intermittent fasting").
  The skill reports this and helps find a local proxy.
- Human traffic only; the Wikimedia agent filter is imperfect, and spike checks mitigate this.
- Chart titles are in English even in Ukrainian reports (the PDF says so under the charts).
- Wikidata lookup of a bare word can pick the wrong item (e.g. `Tea` -> a given name); the
  alternatives are printed and SKILL.md tells the agent to check them.
- Dips and zero-view gaps are flagged, not corrected; trends still include them (a gap lowers coverage, and a gap in the months compared can fail `recent_level_consistent`).

## Evolving the skill for harder research and more data

The layering stays the same: deterministic code for data + statistics, SKILL.md for
workflow and interpretation, and evals to catch regressions on a small model. Grow
each layer step by step:

1. **Evaluation first.** Turn the Haiku runs above into an eval set: questions, the
   expected facts from the tool output, and forbidden claims. Grade automatically
   (numbers, orderings and verdicts must match `analysis.json`) and re-run it on every
   change to SKILL.md or the output format. Add a question to the set whenever a new
   failure is seen.
2. **Topic clusters instead of single articles.** Build topics from Wikidata subclass
   trees (P279/P31) or categories, e.g. "all diet articles", then aggregate and rank.
   Add a `wt rank` command: "which of these 50 topics is growing fastest in cs",
   with multiple-testing control such as Benjamini-Hochberg.
3. **Market sizing.** Join per-edition numbers with external denominators, such as speaker
   and internet-user populations, and correct for readers in other editions using the
   `top-by-country/{project}/{access}/{year}/{month}` endpoint (views of each edition by
   country, countries with >100 views only). That gives "interest per internet user" across countries.
4. **Better statistics for long histories.**
   - Autocorrelation-corrected Mann-Kendall (Hamed-Rao).
   - Structural-break detection (article moves, platform changes, AI-answer adoption).
   - STL decomposition for seasonality.
   - Forecasts with intervals.
5. **Larger data volumes.**
   - The REST API is per-article. For thousands of articles, switch to the monthly
     pageview dumps (dumps.wikimedia.org/other/pageview_complete) loaded into DuckDB or
     Parquet. Keep the same `daily_views()` interface, so stats and reports do not change.
   - Parallelize with a rate limiter that respects Wikimedia limits.
   - Move the cache to one file per project, or to DuckDB.
6. **Richer deliverables.** Multi-page reports with an appendix per market, localized
   chart titles, CSV/Sheets export, and an interactive HTML version.
7. **Context budget.** As features grow, keep SKILL.md short: move rarely-needed guidance
   to `references/` and keep command output compact (Readings, Rankings, failed checks),
   so the model sees conclusions rather than raw arrays.
