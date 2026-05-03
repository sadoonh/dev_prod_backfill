# FBX Data Ingestion Pipeline — Project Specification

**Version:** 1.0  
**Last Updated:** 2026-05-03  
**Status:** Ready for Implementation

---

## 1. Project Overview

Build a Python-based data ingestion pipeline that:

1. **Backfills** all FBX ticker data from 2016 to present as a one-time manual run via GitHub Actions `workflow_dispatch`
2. **Ingests** new daily data every day at 15:00 UTC via a scheduled GitHub Actions cron job
3. Persists data into a PostgreSQL database hosted on a company self-hosted GitHub Actions runner

---

## 2. API

### Endpoint

```
GET https://api.freightos.com/fd_external_apis/fbx/data/
```

**Used endpoint:** `/fbx/data/` only. The `/fbx/tickers/` endpoint (snapshot/current values only) is not required and will not be used.

### Authentication

| Header       | Value                          |
|--------------|-------------------------------|
| `apikey`     | Stored as GitHub Actions secret |
| `secret-key` | Stored as GitHub Actions secret |

### Request Parameters

| Parameter   | Value                                                  |
|-------------|--------------------------------------------------------|
| `from_date` | `YYYY-MM-DD`                                          |
| `to_date`   | `YYYY-MM-DD`                                          |
| `version`   | `daily`                                               |
| `tickers`   | Comma-separated list of all tickers (see section 3)   |

### All Tickers

```
FBX,FBX01,FBX02,FBX03,FBX04,FBX11,FBX12,FBX13,FBX14,FBX21,FBX22,FBX24,FBX26
```

13 tickers total. All are requested in a single API call per date range chunk.

---

## 3. API Response Contract

### Response Shape

```json
{
  "license": "...",
  "tradelanes": [...],
  "tickers": [...],
  "volatilities": {...},
  "version": "Daily",
  "index_points": [
    {
      "ticker": "FBX01",
      "indexDate": "2024-06-03",
      "value": 5027
    },
    {
      "ticker": "FBX02",
      "indexDate": "2024-06-03",
      "value": 683
    }
  ]
}
```

### Fields Used

Only `index_points` is consumed. All other top-level fields (`license`, `tradelanes`,
`tickers`, `volatilities`, `version`) are **discarded**.

| JSON Field          | Maps To DB Column | Notes                             |
|---------------------|-------------------|-----------------------------------|
| `index_points[].ticker`    | `ticker`  | String, e.g. `"FBX01"`           |
| `index_points[].indexDate` | `date`    | String `"YYYY-MM-DD"`, parsed to `date` |
| `index_points[].value`     | `rate`    | Numeric — can be int or float     |

### ⚠️ Known API Quirk: Duplicate Entries

The API response can contain **duplicate `(ticker, indexDate)` pairs** with identical
values within a single response (confirmed in sample data: FBX01 and FBX02 on
`2024-06-12` each appear twice). Deduplication **must happen in Python** before any
database write, not only at the DB constraint level.

**Dedup strategy:** Within each parsed response, keep the first occurrence of each
`(ticker, indexDate)` pair and discard subsequent duplicates. Log a warning with the
count of duplicates dropped per API call.

---

## 4. Database

### Connection

The PostgreSQL database is accessible directly from the self-hosted company runner.
The connection string is stored as a GitHub Actions secret (`DATABASE_URL`).

Format: `postgresql://user:password@host:port/dbname`

### DDL

```sql
CREATE TABLE fbx_rates (
    ticker       VARCHAR(10)     NOT NULL,
    date         DATE            NOT NULL,
    rate         NUMERIC(12, 4)  NOT NULL,
    ingested_at  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT fbx_rates_pkey PRIMARY KEY (ticker, date)
);
```

**Notes:**
- `NUMERIC(12, 4)` handles both integer values (e.g. `5027`) and floats (e.g. `5987.77`)
  without precision loss.
- `ingested_at` is auto-populated by the database on insert — the application layer
  never sets this column explicitly.
- The `PRIMARY KEY (ticker, date)` is the unique constraint that enforces idempotency
  at the database level.

### Upsert Strategy

```sql
INSERT INTO fbx_rates (ticker, date, rate)
VALUES (%s, %s, %s)
ON CONFLICT (ticker, date) DO NOTHING;
```

`DO NOTHING` is used because historical values are not revised by design.
`psycopg2`'s `executemany` is used for batch inserts within each chunk.

`ingested_at` is intentionally omitted from the INSERT column list so the DDL default
(`NOW()`) applies automatically.

---

## 5. Pydantic Models

```python
from pydantic import BaseModel, field_validator
from datetime import date
from typing import List

class IndexPoint(BaseModel):
    ticker: str
    indexDate: date          # Pydantic parses "YYYY-MM-DD" string to date object
    value: float             # Accept both int and float from API

class FbxApiResponse(BaseModel):
    index_points: List[IndexPoint]

    # All other top-level fields are ignored via model_config
    model_config = {"extra": "ignore"}
```

Validation will raise a `ValidationError` if:
- `index_points` is missing or not a list
- Any entry is missing `ticker`, `indexDate`, or `value`
- `indexDate` is not a parseable date string

A `ValidationError` is treated as a non-retryable error (logged and job fails).

---

## 6. Error Handling

### Retry Policy

Applies to HTTP errors only. Uses **exponential backoff** with a fixed random jitter.

| Attempt | Wait Before Retry |
|---------|-------------------|
| 1st     | 2 seconds         |
| 2nd     | 4 seconds         |
| 3rd     | 8 seconds         |
| After 3rd | Raise and fail |

### HTTP Error Classification

| HTTP Status | Category        | Action                                      |
|-------------|-----------------|---------------------------------------------|
| `429`       | Retryable       | Backoff and retry. Log warning with chunk info. |
| `500`       | Retryable       | Backoff and retry. Log warning with chunk info. |
| `502`       | Retryable       | Backoff and retry. Log warning with chunk info. |
| `503`       | Retryable       | Backoff and retry. Log warning with chunk info. |
| `504`       | Retryable       | Backoff and retry. Log warning with chunk info. |
| `400`       | Non-retryable   | Log error with full response body. Raise immediately. |
| `401`       | Non-retryable   | Log error (likely bad credentials). Raise immediately. |
| `403`       | Non-retryable   | Log error (access denied / subscription). Raise immediately. |
| `404`       | Non-retryable   | Log error. Raise immediately. |
| Network timeout | Retryable   | Treated same as 5xx. Backoff and retry. |
| `ValidationError` | Non-retryable | Log error with raw response snippet. Raise immediately. |

On any unrecoverable error, the job exits non-zero, which triggers GitHub Actions
failure notification via the email alerting already configured.

---

## 7. Logging Strategy

**Single layer: structured stdout logging captured by GitHub Actions.**

All log output goes to stdout using Python's built-in `logging` module configured
with a JSON formatter. GitHub Actions captures and retains all stdout/stderr output
for 90 days per workflow run. No external logging service is needed.

### Log Format

Each line is a JSON object:

```json
{
  "timestamp": "2026-05-03T15:01:23.456Z",
  "level": "INFO",
  "run_type": "daily",
  "message": "Chunk complete",
  "chunk_from": "2026-05-02",
  "chunk_to": "2026-05-02",
  "rows_inserted": 13,
  "rows_skipped": 0,
  "duplicates_dropped": 0
}
```

### Log Events

| Event                          | Level   | Key Fields Logged                                              |
|-------------------------------|---------|----------------------------------------------------------------|
| Job started                   | INFO    | `run_type`, `date_range_from`, `date_range_to`                |
| API request sent               | INFO    | `chunk_from`, `chunk_to`, `tickers`                           |
| Duplicate records dropped      | WARNING | `chunk_from`, `chunk_to`, `duplicates_dropped`                |
| Retryable error (with attempt) | WARNING | `status_code`, `attempt`, `chunk_from`, `chunk_to`            |
| Non-retryable API error        | ERROR   | `status_code`, `response_body_snippet`, `chunk_from`, `chunk_to` |
| Chunk complete                 | INFO    | `chunk_from`, `chunk_to`, `rows_inserted`, `rows_skipped`     |
| Job complete                   | INFO    | `total_rows_inserted`, `total_rows_skipped`, `elapsed_seconds`|
| Unhandled exception            | ERROR   | `exception_type`, `exception_message`, `traceback`            |

---

## 8. Backfill Strategy

### Chunking

- One API call per **calendar year** with all 13 tickers in a single request.
- Years: `2016` through the current year (determined at runtime).
- For the current year, `to_date` is set to today's date.
- For all prior years, `to_date` is December 31 of that year.
- The 2016 starting date is `2016-10-03` (earliest available per API docs).

**Example chunks:**

| `from_date`  | `to_date`    |
|--------------|--------------|
| `2016-10-03` | `2016-12-31` |
| `2017-01-01` | `2017-12-31` |
| `2018-01-01` | `2018-12-31` |
| ...          | ...          |
| `2026-01-01` | `2026-05-03` |

### Rate Limiting

Sleep **1.5 seconds** between each API call (between year-chunks).
This is a conservative industry-standard interval for APIs with no documented rate limit.

### Idempotency

The backfill script is safe to re-run at any point. Because of `ON CONFLICT DO NOTHING`,
re-processing an already-ingested chunk simply skips existing rows. The job can be
stopped mid-run and re-triggered without data issues.

---

## 9. Daily Cron Job Strategy

### Schedule

Runs daily at **15:00 UTC** (one hour after the Freightos 14:00 UTC data publication).

GitHub Actions cron expression: `0 15 * * *`

### Date Window

Fetches a **rolling 3-day lookback window** ending yesterday:

- `from_date` = today − 3 days
- `to_date` = yesterday

The 3-day window (rather than just yesterday) provides a safety buffer for any
Freightos publication delays without requiring manual reruns. Because of
`ON CONFLICT DO NOTHING`, re-fetching already-ingested dates is harmless.

---

## 10. Project Structure

```
fbx-ingestion/
├── .github/
│   └── workflows/
│       ├── backfill.yml          # workflow_dispatch (one-time manual run)
│       └── daily_ingest.yml      # scheduled cron job
├── src/
│   ├── __init__.py
│   ├── config.py                 # Env var loading (API key, DB URL, constants)
│   ├── models.py                 # Pydantic models (FbxApiResponse, IndexPoint)
│   ├── api_client.py             # HTTP fetch with retry/backoff logic
│   ├── db.py                     # psycopg2 connection, upsert logic
│   ├── pipeline.py               # Core: fetch → deduplicate → validate → upsert
│   ├── backfill.py               # Entry point: generates year chunks, calls pipeline
│   └── daily.py                  # Entry point: computes rolling window, calls pipeline
├── .python-version               # Pins Python version for uv (contains "3.12")
├── pyproject.toml                # Project metadata and dependencies (uv)
├── uv.lock                       # Auto-generated lockfile — committed to version control
└── README.md
```

### Module Responsibilities

**`config.py`** — Reads all environment variables. Defines constants: ticker list,
backfill start date, sleep interval, retry settings.

**`models.py`** — Pydantic models for API response validation.

**`api_client.py`** — Single `fetch_fbx_data(from_date, to_date)` function.
Handles authentication headers, retry loop with exponential backoff, raises on
non-retryable errors.

**`db.py`** — Single `upsert_records(conn, records)` function. Accepts a list of
`(ticker, date, rate)` tuples. Uses `executemany` with `ON CONFLICT DO NOTHING`.
Returns `(rows_inserted, rows_skipped)` counts.

**`pipeline.py`** — Orchestrates one chunk: calls `api_client`, parses with Pydantic,
deduplicates, calls `db.upsert_records`, logs results.

**`backfill.py`** — CLI entry point for backfill. Generates year chunks, iterates
with sleep, calls `pipeline` per chunk.

**`daily.py`** — CLI entry point for daily job. Computes rolling window, calls
`pipeline` once.

---

## 11. GitHub Actions Workflows

### Secrets Required

| Secret Name    | Description                             |
|----------------|-----------------------------------------|
| `FBX_API_KEY`  | Freightos `apikey` header value         |
| `FBX_SECRET_KEY` | Freightos `secret-key` header value   |
| `DATABASE_URL` | PostgreSQL connection string            |

### `backfill.yml` — One-Time Manual Backfill

```yaml
name: FBX Backfill

on:
  workflow_dispatch:   # Triggered manually from GitHub Actions UI

jobs:
  backfill:
    runs-on: self-hosted
    timeout-minutes: 120   # Safety ceiling for full ~10 year backfill

    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: uv sync --frozen

      - name: Run backfill
        env:
          FBX_API_KEY: ${{ secrets.FBX_API_KEY }}
          FBX_SECRET_KEY: ${{ secrets.FBX_SECRET_KEY }}
          DATABASE_URL: ${{ secrets.DATABASE_URL }}
        run: uv run python -m src.backfill
```

### `daily_ingest.yml` — Scheduled Daily Job

```yaml
name: FBX Daily Ingest

on:
  schedule:
    - cron: "0 15 * * *"   # Every day at 15:00 UTC

jobs:
  ingest:
    runs-on: self-hosted
    timeout-minutes: 15

    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: uv sync --frozen

      - name: Run daily ingest
        env:
          FBX_API_KEY: ${{ secrets.FBX_API_KEY }}
          FBX_SECRET_KEY: ${{ secrets.FBX_SECRET_KEY }}
          DATABASE_URL: ${{ secrets.DATABASE_URL }}
        run: uv run python -m src.daily
```

---

## 12. Dependencies

**`pyproject.toml`**

```toml
[project]
name = "fbx-ingestion"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "requests==2.32.3",
    "psycopg2-binary==2.9.9",
    "pydantic==2.7.1",
    "python-json-logger==2.0.7",
]
```

**`.python-version`**

```
3.12
```

UV reads `.python-version` to pin the interpreter both locally and in CI, ensuring the
same Python version is used everywhere without relying on whatever happens to be
installed on the runner.

**`uv.lock`** is auto-generated by UV on the first `uv sync` and must be **committed
to version control**. It pins every transitive dependency to an exact version and hash,
giving fully reproducible installs. The `--frozen` flag in CI (`uv sync --frozen`)
enforces that the lockfile is used exactly as committed — UV will error rather than
silently update it.

**Dependency notes:**
- `python-json-logger` provides the JSON log formatter with minimal overhead.
- `psycopg2-binary` is used (vs. source build) since no compilation step is needed
  in this context.
- `actions/setup-python` is not needed in the workflows — `astral-sh/setup-uv` handles
  Python installation directly via the `python-version` input.

---

## 13. Key Design Decisions Summary

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Endpoint | `/fbx/data/` only | Provides full historical + current data; `/fbx/tickers/` is snapshot-only |
| Granularity | Daily | Per requirements |
| Chunk size | 1 year, all 13 tickers | Balances API call count vs. payload size |
| Upsert strategy | `ON CONFLICT DO NOTHING` | Values are not revised; idempotency is required |
| Deduplication | Python-layer, before DB write | API response itself contains duplicate entries |
| Rate limiting | 1.5s sleep between chunks | Conservative default; no documented limit |
| Retry policy | 3 attempts, exponential backoff (2s/4s/8s) | Standard for transient HTTP errors |
| Logging | Structured JSON to stdout | GitHub Actions captures it natively; zero infrastructure |
| Backfill trigger | `workflow_dispatch` | One-time manual run; no local execution needed |
| Daily window | 3-day rolling lookback | Buffer for Freightos publication delays |
| DB column `ingested_at` | Set by DDL `DEFAULT NOW()` | App layer never touches it; always accurate UTC timestamp |
| Dependency management | UV (`pyproject.toml` + `uv.lock`) | Fast, reproducible installs; lockfile committed to VCS; `--frozen` in CI prevents silent updates |
