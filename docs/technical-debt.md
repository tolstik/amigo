# Technical debt

## TD-002 — Dense one-page laboratory reports exhaust extraction timeout

- **Status:** implemented; production verification pending
- **Priority:** high
- **Discovered:** 2026-08-23
- **Affected production release:** `8d1266a7aedb94b8df3b415facbdae0ced5e49d8`
- **User-visible symptom:** five intact PDF laboratory documents stop at 40%
  with the sanitized `timeout` code after all three bounded job attempts.

### Confirmed cause

All five failures occur in the first `extract-labs` inference call after local
parsing. They are one-page PDFs with 5,400–5,577 OCR characters; the old
80,000-character extraction limit therefore sent each dense result sheet as a
single request. A representative 2,966-character half completed against the
production gateway in 31.6 seconds and returned 12 rows, while the whole-page
request repeatedly exceeded the fixed 75-second Codex deadline. Parser,
original storage, database integrity, and ingest are not involved.

### Implemented correction

1. Base extraction chunks are capped at 3,000 characters, retaining page
   bounds and deterministic source ordering.
2. Only a sanitized `timeout` can split the current chunk again, at most twice
   and never below the configured minimum. Gateway and whole-document retries
   remain finite.
3. Persistence still begins only after all chunks validate; a failed attempt
   cannot publish a partial report.
4. `python -m app.cli lab-retry-extraction-timeouts` selects only the exact
   terminal signature (`timeout`, extraction progress 40, three attempts),
   verifies original size/SHA-256, and requeues only intact matches.
5. Regression tests require dense input to split, exercise timeout subdivision,
   reach 100%, and prove unrelated/corrupted failed jobs remain untouched.

### Acceptance criteria

- All five production timeout documents complete at 100% and the laboratory
  queue drains without `timeout` or `internal` terminal jobs.
- Extraction requests and retries remain bounded, and no OCR, filename,
  document identifier, payload, or model output enters operational logs.
- All seven production services remain healthy after recovery.

## TD-001 — Laboratory imports fail while resolving missing analyte guides

- **Status:** resolved in production
- **Priority:** high
- **Discovered:** 2026-08-21
- **Affected production release:** `a46ddd7f2a30d580a423a2e62ed67549acfb5a7d`
- **User-visible symptom:** a newly uploaded laboratory document reaches about
  85% and then shows `Ошибка обработки`. The worker retries the document three
  times before leaving it in `failed` state with the sanitized `internal` code.

### Confirmed cause

`missing_analyte_guides()` selects complete `LabAnalyte` rows through a join to
`LabResult` and applies SQL `DISTINCT`. `LabAnalyte.aliases` is a PostgreSQL
`json` column, so PostgreSQL attempts to compare JSON values and rejects the
query with:

```text
could not identify an equality operator for type json
```

The failing query is executed after laboratory extraction and persistence, just
before the new automatic guide generation step. The surrounding transaction is
rolled back, so the UI receives only the sanitized `internal` error. This is a
database-query regression in analyte-guide discovery, not a DNS, parser,
storage, CPU, or memory problem.

Extraction `504`/`timeout` failures observed in the same upload batch are a
separate bounded Codex timeout and must not be treated as evidence that this SQL
fix failed.

### Implemented correction

1. `missing_analyte_guides()` now uses a correlated `EXISTS` predicate. It no
   longer applies `DISTINCT` to complete `LabAnalyte` rows or asks PostgreSQL to
   compare JSON aliases.
2. Filtering remains limited to non-deleted results for the requested document;
   deterministic catalog guides and current persisted guides still suppress
   generation.
3. CI now starts PostgreSQL 17 and runs the regression with JSON aliases and
   repeated results. The same query is still covered by the SQLite suite.
4. The worker flow test covers one known and one repeated unknown analyte,
   verifies one bounded guide request and one persisted guide, and requires the
   document to reach `complete` at 100%.
5. `python -m app.cli lab-retry-guide-regression` selects only the exact TD-001
   terminal signature (`internal`, document progress 85, three job attempts),
   verifies every saved original through the existing size/SHA-256 checks, and
   requeues only intact matches. Other `internal` and `timeout` failures remain
   untouched.

### Production verification

Release `8d1266a7aedb94b8df3b415facbdae0ced5e49d8` was deployed and fully verified
on 2026-08-23. The integrity-checked recovery selected 14 exact TD-001 jobs,
requeued all 14, and left the four pre-existing extraction-timeout jobs
untouched. Thirteen recovered documents completed at 100%; one independently
exhausted its bounded extraction attempts with `timeout` at 40%. The queue
drained with no new PostgreSQL JSON equality error and no recurrence of the
`internal` failure at 85%.

### Acceptance criteria

- Several documents can be uploaded together and documents with missing guide
  articles finish at 100% instead of failing at 85%.
- Repeated occurrences of one analyte in a document produce one guide lookup
  and at most one persisted guide row for the active contract.
- Catalog and previously generated guides remain unchanged and are not sent for
  regeneration.
- Affected failed documents can be retried from their saved originals and
  finish successfully; unrelated timeout behavior remains independently
  observable through its existing sanitized code.
- Queue retries remain bounded and all seven production services stay healthy.
