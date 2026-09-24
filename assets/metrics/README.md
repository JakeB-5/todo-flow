# Anonymous workflow activity measurements

These aggregates describe predecessor workflow operations, including an earlier baseline period. They are **not a benchmark of the current TODO Flow package or evidence that this package produced historical changes**. They show how recorded activity changed while the operating tools evolved.

No source-project names, repository addresses, local paths, authors, commit IDs/messages or original source are included. Raw histories are not public, so outside readers cannot independently verify the original Git records. The public JSON, CSV and formulas support aggregate consistency checks and chart reproduction.

## Observation window

- Snapshot: September 24, 2026, with local references fixed across five related repositories. Discovery found no missing repositories.
- Window: **January 1, 2026 00:00 inclusive to September 24, 2026 00:00 exclusive**, Asia/Seoul. January–August are full months; September covers days 1–23. No month-end projection or incomplete September 24 is included.
- Monthly assignment uses Git committer timestamps consistently for commits and source changes; author timestamps are not mixed into the calculation.
- Collection follows the DAILY, LANDED, LAYERS and LEDGER definitions of `commit-metrics` v1.0.0, plus the source exclusions below.
- This is a separate fixed-snapshot aggregation, not an overwrite of previously published figures. Later ref deletion, squash or rebase can change a fresh all-refs count. The comparable July published total of 2,526 commits matched.

## Metrics

| Metric | Calculation | Interpretation |
|---|---|---|
| All-ref commits | Sum of repository `--all` commits, including merges | Activity, including unlanded work, rebased copies and ledger updates; not labor output |
| Integrated non-merge commits | Non-merge commits reachable from each integration branch at the snapshot | Integration status of commits timestamped in that month, not necessarily the month they landed |
| Source changes | Text source additions + deletions in those non-merge commits | Churn, not net code size or unique changed lines; repeated edits count again |
| Integration merges | Merge commits on the integration branch's first-parent path | Integration frequency, not a remotely verified PR count |
| Completion transitions | Sum of positive additions minus deletions of `done` lines in ledger-changing commits | Not unique features; repeated completion can count again. January–June are unavailable, not zero performance |

The first recorded registration/execution tooling appears in July. Shading after July marks that record, not a proven first use date or sole cause. June is the preceding month, with January, July and August comparisons also disclosed.

## Source adjustments

1. Collector exclusions: dependency lockfiles, `node_modules`, `dist`, `dist-lib`, TypeScript build information, documentation extensions and directories. LANDED uses rename detection.
2. Additional exclusions: `vendor`, `build`, `coverage`, `generated`, `__generated__`, `__snapshots__`, and files outside the source-extension allowlist. Product and test source are included. Exact extensions are in [measurements.json](measurements.json), `method.source_extensions`.
3. Known bulk initial input on **January 5** and cross-repository movement on **July 2** are excluded from source metrics: 122,897 and 308,857 unadjusted code lines respectively. This conservatively excludes ordinary changes on those days too. Commit, merge and completion aggregates remain unchanged.
4. Daily averages use observed calendar days, not only active days. Source averages additionally exclude those two dates: 30 days in January, 30 in July, otherwise the observed month length.
5. Path/extension filters cannot prove manual authorship. Binary changes do not count as text lines.

`raw_code_add + raw_code_delete = source_add + source_delete + total source_excluded` holds in every month. Unclassified content in the collector's separate four-layer classification was below 2% each month. Design/derived-document proportions affected by ledger format changes are not used as the growth claim.

## Monthly observations

| Month | Observed / source days | All-ref commits | Source additions | Source deletions | Adjusted source changes | Integration merges | Completion transitions |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2026-01 | 31 / 30 | 289 | 318,619 | 52,785 | 371,404 | 4 | Unavailable |
| 2026-02 | 28 / 28 | 201 | 98,348 | 27,912 | 126,260 | 4 | Unavailable |
| 2026-03 | 31 / 31 | 189 | 51,926 | 7,822 | 59,748 | 3 | Unavailable |
| 2026-04 | 30 / 30 | 143 | 10,583 | 1,428 | 12,011 | 1 | Unavailable |
| 2026-05 | 31 / 31 | 383 | 59,927 | 18,510 | 78,437 | 9 | Unavailable |
| 2026-06 | 30 / 30 | 682 | 76,931 | 12,708 | 89,639 | 38 | Unavailable |
| 2026-07 | 31 / 30 | 2,526 | 148,750 | 44,644 | 193,394 | 309 | 236 |
| 2026-08 | 31 / 31 | 8,086 | 315,397 | 45,041 | 360,438 | 769 | 628 |
| 2026-09 (1–23) | 23 / 23 | 7,275 | 276,157 | 34,679 | 310,836 | 514 | 402 |

These are observations, not projections. Monthly totals are not used directly as growth multipliers because month lengths differ and September is partial.

## Daily-average comparisons

| Comparison | All-ref commits | Adjusted source changes | Completion transitions |
|---|---:|---:|---:|
| January → September | 33.93× | 1.09× | Unavailable |
| June → September | 13.91× | 4.52× | Unavailable |
| July → September | 3.88× | 2.10× | 2.30× |
| August → September | 1.21× | 1.16× | 0.86× |

- June → September: 22.73 → 316.30 commits/day and 2,987.97 → 13,514.61 adjusted source lines/day.
- January → September: commits rose 33.93×, while source changes rose 1.09×. The metrics are not interchangeable.
- July → September: 7.61 → 17.48 completion transitions/day, 2.30×. No comparable January–June completion ledger was available.
- August → September: commits/day rose 21.3% and source changes/day rose 16.2%, while completion transitions/day **fell 13.7%**. Integration merges/day also declined.
- In September, 78.7% of integrated non-merge commits touched documentation/ledger paths only, not code paths. Commit count is therefore not a proxy for code output.
- Within the same adjusted source set, source changes per source-touching commit were approximately 235.3 lines in June and 280.5 in September. All commits are not the denominator for this calculation.

Staffing, hours, task difficulty, models and product phase were not controlled. These observations do not establish a causal multiplier of labor productivity. The supported claim is growth in recorded activity during the workflow's evolution, with distinct changes in source churn and completion activity.

## Charts and reproduction

[Summary PNG](workflow-impact.png) · [SVG](workflow-impact.svg) · [Growth PNG](workflow-growth.png) · [Source changes PNG](source-changes.png) · [JSON](measurements.json) · [CSV](monthly.csv)

From the repository root:

```sh
uv run --no-project --with matplotlib==3.11.2 python scripts/render_metrics.py
```

The renderer reads only public aggregates, without source-repository access or credentials. Future updates must preserve anonymity and explain changes in definitions, observation windows or exclusions before replacing figures.
