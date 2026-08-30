# Hardening pass — bugs and checklist

Date: 2026-08-30. Adversarial QA on Projects / Schema / Extract / Review.
Tests: `tests/test_claims_hardening.py` (plus existing schema/engine/models tests).
Real backend: Ollama `hf.co/unsloth/Qwen3-0.6B-GGUF:BF16` on a 3-paper scratch corpus
copied from `~/Library/Metadata/qc4hep` (two full-text files + one abstract-only).
Scratch projects lived under `/tmp/citehop-hardening-qa/` — not in `~/Library/Metadata/_projects`.

This file records **observed** behavior, **deliberate lifecycle decisions**, and every
bug found. “Pass” means the checklist item was exercised and the behavior is now
intentional and tested, not that the product is bug-free.

Current commands and UI: [USAGE.md](USAGE.md). Yield caveats:
[KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md).

## After the hardening pass (same day)

These are later, tested engine/UI changes — not items from the original
checklist:

1. **`locate_span` no longer uses an 80-character prefix fallback.** Exact /
   strip / whitespace-collapse only. Test:
   `HardeningTests.test_locate_span_rejects_unmatched_prefix`. Existing claim
   rows are not rewritten; `scripts/audit_grounding.py` classifies them.
2. **`resume_run` requeues `done` papers** whose `text/<file_id>.txt` is newer
   than extract, and deletes that run’s claims for them. `start_run` warns
   (does not refuse) when fetch is still open. Tests:
   `test_resume_requeues_done_when_text_file_newer`,
   `test_start_run_warns_when_fetch_still_open`.
3. **UI coverage:** Analyze/Corpus KPIs are cited-by-seed, citing-the-seed, and
   with-full-text. Provider lists at resolve sit on the coverage line. Review
   copy says match is two samples of the same model, not a validity check.
4. **Read-only scripts:** `audit_corpus.py`, `audit_grounding.py`,
   `audit_extract_drift.py`, plus `evidence_table.py` for confirmed-claim
   markdown.

## Lifecycle decisions (were undefined; now explicit)

1. **Zero-field claim types are allowed.** `structured_fields: []` is valid. The type
   still extracts `claim_text` + `quoted_source_span`. Rejection would have been an
   extra constraint the schema author did not ask for.
2. **Schema edit after claims exist:** block removing/renaming a `type_id` that any
   claim in the project still references; block changing the JSON type of a field key
   on such a type. Adding types/fields and editing display names is allowed. No
   automatic migrate/version. Start a new project if you need a breaking schema.
3. **Re-run after completed:** `start_run` creates a **new `run_id` and re-extracts
   every paper**. Review shows the latest run. Prior claims stay in SQLite under the
   old `run_id`. Same-run duplicates are a bug (fixed via atomic paper commit).
   Starting while paused is refused (resume instead). This is replace-the-active-view,
   not skip-already-extracted (schema may have changed) and not silent duplication.
4. **Crash / killed worker:** papers in flight are `extracting`. `resume_run` requeues
   them to `pending`. `process_available` will not mark a run `completed` while
   `extracting` rows exist. Stale `extracting` older than 600s
   (`CITEHOP_EXTRACT_LEASE_SECONDS`) is requeued automatically.
5. **Backend unreachable mid-run:** pause the run with a readable error; leave the
   current paper `pending` (not `error`). Resume retries it. Unparseable JSON for one
   paper is `error` on that paper only; the run continues.

---

## Checklist

### Backend / extraction correctness

- [x] **Real Ollama + real paper text (span-proximity merge)** — see observation
      below and the first completed run vs the retry run.
- [x] **Short / long text** — abstract-only (~1.5k chars) extracted; 22k and 32k
      full-text papers overflowed a 4096-token context until retry-with-shorter-clip
      (fixed). Fixture test covers >60k char files with `MAX_PAPER_CHARS` clip.
- [x] **Backend unreachable mid-run** — simulated: first paper completes, next
      `complete()` raises `BackendUnavailable`. Run pauses; completed claims kept;
      in-flight paper returns to `pending`. Test:
      `test_backend_dies_after_first_paper_keeps_progress`.
- [x] **Malformed JSON for one paper** — that paper `error` with a JSON message; the
      other paper completes. Test: `test_malformed_output_fails_one_paper_not_the_run`.

### Schema lifecycle

- [x] **Zero-field claim type** — allowed (decision 1). Test:
      `test_zero_field_claim_type_is_allowed_and_extracts`.
- [x] **Edit/remove a type after claims exist** — blocked with a `SchemaError` naming
      the type (decision 2). Adding a type is allowed. Tests:
      `test_cannot_remove_claim_type_that_has_claims`,
      `test_can_add_type_after_claims_exist`.
- [x] **Same `type_id`, different fields, two projects** — no leakage. Test:
      `test_same_type_id_does_not_leak_across_projects`.

### Run lifecycle & idempotency

- [x] **Re-run with existing claims** — new run, new claim ids, review lists latest
      only; old rows remain in DB (decision 3). Test:
      `test_rerun_creates_new_run_and_review_shows_latest_only`.
- [x] **Pause / resume / pause** — status ends `paused`; `pause_requested` stays
      set until resume so an in-flight generation can abort. Resume clears the
      flag and retries the pending paper. Test:
      `test_pause_resume_pause_leaves_consistent_status`,
      `test_pause_aborts_in_flight_paper_and_resume_retries`.
- [x] **Two projects concurrent** — separate `extraction.db`; claim ids and token
      counters do not mix. Test:
      `test_two_projects_concurrent_do_not_share_token_counts`.
- [x] **Kill mid-write** — claims are SQLite rows, not claim files. `complete_paper`
      writes claims + paper status + token bump in one `BEGIN IMMEDIATE` transaction.
      A failed insert rolls back: paper stays `extracting`, zero claims, tokens
      unchanged. Resume requeues. Test:
      `test_complete_paper_is_atomic_on_insert_failure`,
      `test_resume_requeues_extracting_after_simulated_crash`.
      Two workers on one project take papers with `claim_next_paper` (pending →
      extracting); they do not double-extract. Test:
      `test_two_workers_same_project_do_not_duplicate_a_paper`.

### Review UI

- [x] **Confirm / reject / edit persist** including `human_edit` audit — API round-trip
      with a second `ClaimsAPI` instance. Live Qt reload was not click-tested (see
      limitations). Test: `test_review_persists_and_rejects_string_in_number_field`.
- [x] **String into a `number` field** — `SchemaError` (“must be a number”), not
      coerced. Same test.
- [x] **Filter with zero matches** — `list_claims(..., agreement="disagreement")`
      returns `[]`. Review page shows “No claims with agreement=…”. Test:
      `test_list_claims_empty_filter_is_empty_list`.
- [x] **Out-of-range `source_char_offset`** — `clamp_span` does not raise; Review
      notes that the highlight was skipped. Test: `test_clamp_span_out_of_range`.

### General UX

- [x] **Empty states** — copy is in Projects / Schema / Extract / Review / Corpus
      pages (not a blank table with no explanation). Test:
      `test_empty_state_copy_is_present_in_pages`. Qt was not driven.
- [x] **CLI + UI on the same project** — two `process_available` workers (the CLI/UI
      shared path) do not duplicate papers. A live `citehop extract start` against a
      running Extract tab was not launched. If a worker died leaving `extracting`
      rows, `citehop extract run` prints a resume hint instead of spinning.

---

## Span-proximity merge on real Ollama output

**This was the highest-priority open question.** Fixture LLM is engineered so pass A/B
quotes sit inside 120 characters; real models are not.

Model: `hf.co/unsloth/Qwen3-0.6B-GGUF:BF16` (format=json). Schema: one type
`reported_finding` with a string `topic`. Corpus: IOP full text ~32k chars, arXiv
full text ~22k chars, PRL abstract ~1.5k chars.

**First run (before context retry):** two full-text papers failed with Ollama HTTP 400
`exceed_context_size_error` (prompt ~7289 tokens, model window 4096). Abstract-only
paper produced **4 claims: 1 `match`, 3 `single_pass_only`** (25% / 75%). Review
confirm → reject → edit succeeded on those rows.

**Second run (after truncating and retrying on context overflow):** all 3 papers
`done`, 0 `error`. **6 grounded claims:**

| agreement         | n | share |
| ----------------- | - | ----- |
| match             | 0 | 0%    |
| partial_match     | 0 | 0%    |
| disagreement      | 1 | 17%   |
| single_pass_only  | 5 | 83%   |

By paper: PRL abstract 5 claims; arXiv full text 1 claim; IOP full text 0 grounded
claims (the model returned JSON, but quotes did not locate in stored text — counted
`done`, not `error`). `topic` was usually `null`.

**Conclusion:** on this small real-model sample, dual-pass alignment is **mostly
`single_pass_only`**. Pass A and pass B rarely quote spans within 120 characters with
the same `claim_type`. Do not read a high `match` rate from fixture tests as a
property of live extraction. A larger/instruct-tuned model may pair more often; that
was not measured in this pass.

---

## Bugs

## [SEVERITY: critical] Claim insert and paper `done` were separate commits
- Repro: extract a paper; crash after `INSERT` claims and before `UPDATE run_papers`.
- Observed: claims on disk, paper still `pending` → resume extracts again → duplicate
  claims in the same run.
- Expected: claims and paper status commit together, or neither.
- Root cause: `insert_claims()` committed, then `mark_paper()` committed.
- Fix: `ClaimStore.complete_paper` uses one `BEGIN IMMEDIATE` transaction for claims,
  token bump, and paper status. Crash recovery: `extracting` + resume requeue.
- Regression test: `tests/test_claims_hardening.py::HardeningTests.test_complete_paper_is_atomic_on_insert_failure`

## [SEVERITY: critical] Run could be marked completed while papers were still `extracting`
- Repro: crash after claiming a paper (`extracting`) with no remaining `pending` rows;
  call `process_available`.
- Observed: `next_pending_paper` was `None` → status `completed`, in-flight paper lost.
- Expected: do not complete while `extracting` rows exist; resume requeues them.
- Root cause: completion checked only `pending`.
- Fix: `claim_next_paper`; if none pending and `extracting` > 0, return without
  completing. `resume_run` requeues all `extracting`.
- Regression test: `test_resume_requeues_extracting_after_simulated_crash`

## [SEVERITY: high] Backend down marked the current paper `error` or left status `running`
- Repro: stop Ollama (or raise connection error) during `process_available`.
- Observed: (1) `LLMError` from `_ready_llm` aborted the worker while the run stayed
  `running` (Start disabled, Resume disabled). (2) Connection error inside
  `extract_paper` marked that paper `error` so resume skipped it.
- Expected: pause with “Ollama is not reachable…”, paper stays pending, resume retries.
- Root cause: transport failures were the same class as bad JSON; `_ready_llm` was not
  caught in `process_available`.
- Fix: `BackendUnavailable` subclass; pause + `release_paper`; unparseable JSON stays
  per-paper `error`. Extract UI Resume works when status is `running` but the worker
  is dead. Worker `_on_fail` pauses the run.
- Regression test: `test_backend_unavailable_pauses_and_keeps_paper_pending`,
  `test_backend_dies_after_first_paper_keeps_progress`

## [SEVERITY: high] Full-text papers failed a 4096-token model with an opaque HTTP 400
- Repro: extract a ~32k-char paper with Qwen3-0.6B (n_ctx=4096). `MAX_PAPER_CHARS` is
  60_000.
- Observed: paper `error`, message was nested JSON
  `Ollama HTTP 400: {"error":"{\"error\":{... exceed_context_size_error ...}}"}`.
  Abstract-only paper succeeded.
- Expected: truncate and retry; if it still cannot fit, a sentence naming token counts.
- Root cause: char clip is not a token budget; 400 was treated as a hard paper failure.
- Fix: `ContextTooLong`; `extract_paper` halves the paper clip down to 1500 chars and
  retries. `_ollama_client_error` unpacks nested JSON.
- Regression test: `test_context_overflow_retries_with_shorter_clip`,
  `test_ollama_context_error_is_legible`

## [SEVERITY: high] Schema save could drop types that existing claims still use
- Repro: extract, then delete that claim type in the schema editor and save.
- Observed: save succeeded; claims still in DB with an orphan `claim_type`.
- Expected: refuse the save with a named error (decision 2).
- Root cause: `save_schema` only ran `validate_schema`.
- Fix: `check_schema_edit` against `referenced_type_ids` in `extraction.db`.
- Regression test: `test_cannot_remove_claim_type_that_has_claims`,
  `test_cannot_change_field_type_while_claims_exist`

## [SEVERITY: high] Review edit accepted a string for a number field
- Repro: `review_claim(..., "edit", edit={"structured_fields": {"minutes": "nope"}})`.
- Observed: stored as a string (or coerced if it looked numeric).
- Expected: reject with “Field 'minutes' must be a number”.
- Root cause: `apply_review` wrote JSON without schema types. Extraction correctly
  coerces LLM strings; human edits must not.
- Fix: `coerce_fields(..., strict=True)` on the review path.
- Regression test: `test_review_persists_and_rejects_string_in_number_field`,
  `test_coerce_fields_strict_rejects_string_in_number_field`

## [SEVERITY: medium] Two workers could extract the same pending paper
- Repro: CLI `process_available` and UI worker on the same run.
- Observed: both `SELECT ... status='pending'`, both insert claims.
- Expected: one owner per paper.
- Root cause: no lease; SQLite `busy_timeout` was default.
- Fix: `claim_next_paper` (`pending` → `extracting`) under `BEGIN IMMEDIATE`;
  `PRAGMA busy_timeout=30000`.
- Regression test: `test_two_workers_same_project_do_not_duplicate_a_paper`

## [SEVERITY: medium] Resume after pause-while-paused was a no-op
- Repro: pause, pause again (`pause_requested=1` left set), resume, `process_available`.
- Observed: resume set `running` but did not clear `pause_requested` → immediately
  paused again.
- Expected: resume actually runs.
- Root cause: `set_run_status` only cleared `pause_requested` when entering `paused`.
- Fix: clear it on `running` as well; idempotent pause when already paused.
- Regression test: `test_pause_resume_pause_leaves_consistent_status`

## [SEVERITY: medium] First Ollama load timed out / 180s generate timeout too short
- Repro: load Qwen3.5-2B with `num_gpu` set; urllib timeout 180s. Empty `num_gpu` load
  used a 12s timeout.
- Observed: `TimeoutError` / “Ollama load failed” while the model was still loading.
- Expected: wait in the same ballpark as FreeToken (600s).
- Root cause: hardcoded 12s/180s.
- Fix: Ollama load and chat timeouts are 600s. No silent `llama3.1` default if no model
  is selected.
- Regression test: none that sleeps 600s — covered by the timeout constants and
  `test_backend_unavailable_*` for the failure path. Deferred: live load-timeout test.

## [SEVERITY: medium] Empty pages were blank tables
- Repro: open Projects / Schema / Review with nothing selected / no rows.
- Observed: empty table, no designed empty copy (Corpus already had “No corpora yet”).
- Expected: an explicit empty state.
- Fix: copy on Projects, Schema banner, Review empty label (including zero-hit filters).
- Regression test: `test_empty_state_copy_is_present_in_pages`

## [SEVERITY: low] Provenance highlight on stale offsets
- Repro: claim offsets past the end of current stored text.
- Observed: Qt clamp avoided a crash (already); no explanation.
- Expected: no crash + a note that offsets do not match current text.
- Root cause: silent clamp.
- Fix: `clamp_span` returns an out-of-range flag; Review appends a line.
- Regression test: `test_clamp_span_out_of_range`

## [SEVERITY: low] Machina `num_gpu=99` can 500 Ollama (deferred)
- Repro: POST `/api/chat` with `options.num_gpu: 99` on Qwen3-0.6B.
- Observed: HTTP 500. The same model works without that option (~22s JSON `{ok:true}`).
- Expected: a reachable error, or omit an invalid layer count.
- Root cause: not in the default path for this 0.6B tag (no Machina cache). Tags that
  *do* have a cache still send `num_gpu`.
- Fix: deferred — do not invent a layer cap in this pass; record it. Use Models-tab
  values that Machina actually measured.
- Regression test: none (needs a live Ollama 500).

## [SEVERITY: low] IOP full-text completed with zero grounded claims (deferred)
- Repro: 0.6B + truncated 32k-char paper after context retry.
- Observed: `status=done`, tokens used, `list_claims` empty for that paper. Quotes did
  not `locate_span` in stored text (paraphrase).
- Expected: still `done` (JSON was valid); review has nothing to show. A “0 claims
  grounded” paper log would help but is not corruption.
- Root cause: small-model quote quality vs verbatim locate. Not a write bug.
- Fix: deferred — fuzzy locate would change provenance guarantees.
- Regression test: none; recorded as a limitation of live 0.6B output.
