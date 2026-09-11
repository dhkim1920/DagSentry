# Daily Report v2

DagSentry creates one report for each local date and environment. The report always contains the
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

Apply migration `0020` and configure at least:

```text
DAGSENTRY_DATABASE_URL=postgresql+psycopg://...
DAGSENTRY_ENVIRONMENT=production
DAGSENTRY_WEBHOOK_URL=https://...
```

Generate yesterday's Asia/Seoul report, or select a date and `--timezone UTC` explicitly:

```shell
uv run dagsentry-daily-report
uv run dagsentry-daily-report --date 2026-08-12
```

Leave `DAGSENTRY_LLM_PROVIDER` unset for rule-only operation. To add OpenAI prose, configure
`DAGSENTRY_LLM_PROVIDER=openai`, `DAGSENTRY_LLM_MODEL`, `DAGSENTRY_OPENAI_API_KEY`, and optionally
`DAGSENTRY_DAILY_REPORT_PROMPT_VERSION`.

Anthropic and Bedrock report summaries use the selected diagnosis Provider's connection/model
settings as well. All three report adapters share `daily-report-ko-v1`, serialize only Statistics
and the rule report, and validate narrative output. HTTP retry handling is shared; Bedrock keeps
its SDK error policy and disables SDK retries to avoid multiplying attempts. No automatic LLM
fallback chain is configured. Provider failures preserve the rule report.

## APScheduler automation

Run `dagsentry-scheduler` as a separate long-running process. Daily execution settings are stored in
DagSentry and managed from the Daily Reports UI; the scheduler polls those settings and registers
one daily job per enabled environment. New UI schedules default to `09:00 Asia/Seoul`.

The execution timezone defines both the firing time and the completed local day being reported.
CLI uses DAGSENTRY_REPORT_TIMEZONE (default Asia/Seoul). Scheduler reuses its session factory.
Stored reports are not recalculated when diagnoses finish later or schedule timezones change.

Migration 0020 preserves v1 UTC periods and adds empty top_failures. An empty migrated list does
not mean no failures. Non-UTC reports prevent downgrade to v1: restore a backup and matching image.
