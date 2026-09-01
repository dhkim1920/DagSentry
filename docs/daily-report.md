# Daily Report v1

DagSentry creates one report for each UTC date and environment. The report always contains the
versioned `DailyStatistics` snapshot and a complete deterministic rule-based section. AI is
optional and may add narrative only.

## Generation boundary

The flow is fixed:

```text
PostgreSQL SQL aggregate
→ frozen DailyStatistics
→ deterministic RuleBasedDailyReport
→ optional DailyReportAISummary
→ idempotent Webhook payload
```

`DailyReportAISummary` contains only `key_changes` and `priorities`. Its strict schema forbids a
`statistics` field, and the delivered numeric section is reconstructed from the immutable stored
Statistics snapshot rather than an AI response. An invalid, unavailable, or schema-breaking AI
response is discarded; the rule-based report is still stored and delivered.

The `daily_reports` table has a unique `(report_date, environment)` constraint. It stores the
Statistics, rule report, optional AI prose, stable delivery key, and delivery attempt state. A
repeated successful run performs no second Webhook call. A failed delivery retries the stored
payload without recalculating Statistics or calling AI again. Receivers also get the same stable
`Idempotency-Key` on every attempt.

## Configuration and manual execution

Apply migration `0017` and configure at least:

```text
DAGSENTRY_DATABASE_URL=postgresql+psycopg://...
DAGSENTRY_ENVIRONMENT=production
DAGSENTRY_WEBHOOK_URL=https://...
```

Generate yesterday's UTC report, or select a date explicitly:

```shell
uv run dagsentry-daily-report
uv run dagsentry-daily-report --date 2026-08-12
```

Leave `DAGSENTRY_LLM_PROVIDER` unset for rule-only operation. To add OpenAI prose, configure
`DAGSENTRY_LLM_PROVIDER=openai`, `DAGSENTRY_LLM_MODEL`, `DAGSENTRY_OPENAI_API_KEY`, and optionally
`DAGSENTRY_DAILY_REPORT_PROMPT_VERSION`.

## APScheduler automation

Run `dagsentry-scheduler` as a separate long-running process. Daily execution settings are stored in
DagSentry and managed from the Daily Reports UI; the scheduler polls those settings and registers
one daily job per enabled environment. The default schedule is `00:10 UTC`.

The execution timezone controls when the job fires, while every report still covers the preceding
completed UTC date. Scheduler runs and manual requests use the same idempotent report service.
