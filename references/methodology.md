# Methodology

Read this when the user asks how a number was computed, or challenges a conclusion.

## Data
- **Source.** Wikimedia Analytics API, `per-article` and `aggregate` endpoints.
  - Traffic: `agent=user` (automated and spider traffic excluded), `access=all-access` (desktop + mobile web + app).
  - Data starts on 2015-07-01. Data license: CC0 1.0.
  - Reference: https://doc.wikimedia.org/generated-data-platform/aqs/analytics-api/reference/page-views.html
- **Daily data.** Everything is fetched per day and aggregated to *average daily views per calendar month*.
  - Days missing from the API mean zero views.
  - The newest two days are skipped, because they can still be incomplete.
- **Redirects.** Views of redirects are counted under the redirect's own title. Up to 25 redirects per article are added to the article (`--max-redirects`). The share of views that came via redirects is reported (`redirect_share`).
- **Unique devices.** `unique-devices/{project}/all-sites/monthly`: the estimated number of distinct devices that visited the edition per month (desktop + mobile web), averaged over the last 12 months, with a Theil-Sen trend. A device is not a person; the estimate is an undercount for devices that block cookies.
- **Language editions.** The same Wikidata item gives the matching article in every language. Some editions have no article: a missing sitelink means a missing article, or an article linked to a different item (use `wt search`).

## Normalisation
`per_million` = the article's views / all human views of that language edition × 10^6.
This removes platform-wide changes, such as search engines and AI answers
reducing Wikipedia visits, or an outage. It also makes small and large editions
comparable.

## Trend
- **Slope.** Theil-Sen slope of log(monthly average + c), converted to % per year. The offset
  c = 1% of the median positive month (`safe_log`), so the result does not depend on units:
  the old `+1` flattened small series such as views per million (a -36%/yr share read as -9%).
- **Fit.** `r2` = R² of the Theil-Sen line on the log series: how well a steady trend describes it.
- **Uncertainty.** 95% CI by Sen's method. Mann-Kendall test for monotonic trend.
  - Monthly series are autocorrelated, so p-values are optimistic.
  - Independent checks therefore guard the verdict:
    - year-over-year change (last 12 months vs the 12 before) must agree in sign and be ≥5%;
    - the trend recomputed with spike days capped must agree.
- **Verdict.** `rising` if the CI is fully above 0, `falling` if fully below 0, else `no clear trend`.

## Spikes
Robust z-score of log views against a 29-day rolling median. A spike day must be
>5 robust SD above the median, >2× the baseline, and >20 extra views.
- `share_of_views` = the excess views on spike days / all views.
- `events` = the largest episodes: start date, peak, multiple of the baseline.

## Dips and gaps (flagged, never removed)
- **Dip:** the mirror of a spike - robust z < -5, below 0.5× the baseline, >20 views below it.
- **Gap:** a run of ≥3 zero-view days after the article's first view, where the baseline is ≥3/day
  (outage, page move or deletion). `full_coverage` subtracts gap days.

## Descriptive metrics
Per series (monthly average views/day): median, peak and min month, last month, last-3-month
average, coefficient of variation, and **month-to-month volatility** = SD of the monthly log
change × 100 (<15% stable, <35% moderate, else volatile). **Same-month YoY** compares the last
month with the same month a year earlier and counts how many of the last 12 months beat their
year-earlier month (needs 13+ months). `period_change` also gives the absolute change in views/day.

## Charts
- `chart.png`: views per month (absolute, log scale if the range is >20×; or an index with
  `--relative`) and the share of the edition's traffic as an index. **Index base** = average of
  the first 12 months (3 if the window is under 24 months) = 100, not the first month, so one
  odd month does not distort it.
- `compare.png`: bars per series - audience (views/day), change vs the previous period (views
  solid, share hatched), and volatility. The PDF drops it before shrinking the main chart.

## Business framing
Deterministic bullets built from the verdicts and rankings: signal -> interpretation ->
hypothesis -> next check (rising/falling share, largest audience, yearly cycle, low volume or
missing articles). They are hypotheses to validate, not recommendations.

## Composite topics
`Label=A+B` sums items per language. A `lang:Title` part counts only in its own language. If
languages end up summing different numbers of articles, a "not like-for-like" warning is shown.

## Articles created mid-window
If an article has no views for the first 14+ days of the window, its statistics (trend, changes,
seasonality, descriptive metrics) start at the first full month with data; the zeros before it
would otherwise fake growth. `stats_from` records the month, and the Reading line says so. Charts
keep the whole window. full_coverage still fails, so confidence drops.

## Zero-view gaps
Runs of 3+ zero days after the first view are gaps when the usual level around them (the median of
the 29 days before the run, or after it if the run starts early) is 3+ views/day. The level is
taken around the run, not from the rolling median, so gaps of any length are caught.

## Change without spikes
`period_change_ex_spikes` (and its share variant) repeat the 12-vs-12 change on spike-capped
data. When it differs by 10+ points, the Volume & stability table and compare.png show both, so a
single viral week does not look like growth.

## Seasonality
Needs 24+ months. The last two years' month-of-year patterns are correlated.
"Strong" means r ≥ 0.6 and an amplitude ≥ 25%. The peak and low months are reported.

## Confidence
Checks per verdict:
- enough_volume (≥30 views/day)
- full_coverage (the article has views on ≥90% of the window's days)
- For rising or falling:
  - statistically_significant
  - period_change_agrees
  - not_driven_by_spikes
  - seasonality_handled
- For no clear trend: precise_estimate (CI width ≤20 pp)
- recent_level_consistent (15+ months): the average of the last 3 months vs the same 3 months a
  year earlier, spikes capped (seasonality-neutral). It fails on a move of 20%+ that the verdict
  does not show (no clear trend) or that goes against it: a recent shift the 24-month slope has not
  caught yet.

Confidence: 0 failed checks = high, 1 = medium, 2+ = low.

## Runs, follow-ups and raw data
- Every `analyze` call recomputes everything from the downloaded data. `--from <run>` only copies
  the previous parameters; the explicitly given options replace them.
- `data_hash` = SHA-1 of the downloaded inputs (daily views, devices). `run_id` = SHA-1 of
  topics, languages, window, max_redirects and `data_hash`. The same run id means the same numbers.
  Question and `--relative` do not change it (they change no statistic).
- The FOLLOW-UP block compares each series present in both runs (verdict, %/yr, confidence).
  With the same window and redirects, a kept series must be unchanged; if not, the source data
  changed (Wikimedia revised counts or the cache was refreshed) and a warning says so.
- `raw/` per run: `daily_views.csv` (every article, redirect and whole edition, per day, as
  downloaded), `unique_devices.csv`, `articles.csv` (which title is a main
  article or a redirect of which topic), `series_daily.csv` (article + redirect views, edition
  views, per million, spike-capped views: the inputs of the monthly means).
- Report check: every percentage in agent-written findings must occur (with its sign) in that
  run's summary. It catches numbers carried over from another run; a number that happens to
  occur in both runs passes. A bullet that names another run's id (a run folder next to this
  one) may also quote that run's percentages - an explicit comparison. Non-percentage facts
  (counts, rankings) are not checked.

## Known limits
- **Proxy.** Pageviews are a proxy for curiosity: not purchase intent, not the number of speakers, not unique people.
- **Readers in other editions.** Bilingual audiences read other editions (English above all). Interest in a small edition understates that language's population.
- **One article, one framing.** A single article captures one framing of a topic. Use 2-3 articles for broad concepts.
- **Moved articles.** Article moves or renames can create fake breaks. Low `coverage` or a flagged gap usually signals this.
- **Bots.** The agent filter misses some bots. Extreme single-day spikes are often automated.
