# Agent prompt: Review loads file-backed claims (behavior-preserving)

Copy everything below the line into a new agent session on `/home/h-livv/opt/citehop`.

---

## Final goal

External `citehop.claim.v1` files under a project’s `claims/` folder **appear in Review** and can be **Confirmed / Rejected / Edited** like extracted claims.

The **Review screen must look and act the same** (same tabs, filters, buttons, provenance highlight, empty-state copy, sort). **Export and on-disk results must be the same format** as today (`citehop.claim.v1` per file, `citehop.claims.v1` export, evidence table still defaulting to `human_confirmed`).

You are **not** replacing SQLite with a JSON app. You are making Review (and `list_claims`) **see files that are not yet in the latest run**, then using the **existing** review + export path so flags and JSON stay in sync.

## Why this is needed

Today Review calls `ClaimsAPI.list_claims` → `extraction.db` **latest `run_id` only**. `claims/*.json` is a **mirror** written after insert/review. **Open JSON** only opens a file for a row already in that table. Files dropped in by another agent never show up, and Confirm cannot run because there is no DB row.

## Stay on target

- Do this ingest/union in the **API/store/files** layer so CLI `extract claims` matches the UI. Do **not** reimplement listing inside `review.py` widgets.
- Do **not** restyle Review, add tabs, change filter widgets, change button labels, or change confirm/reject/edit semantics (`unverified_by_human` → `human_confirmed` / `human_rejected` / `human_edited`; Edit still stores `{original, edited}`).
- Do **not** delete claims on reject. Do **not** change extract, schema engine, corpus pipeline, Models, WAL, or `locate_span`.
- Do **not** treat `review_output/` or export bundles as Review input. Input is **only** `_projects/<id>/claims/` (`*.json` plus optional `index.json`).
- If a file is malformed, skip it (log/count); do not crash Review.
- Unsure ⇒ out of scope.

## Required behavior

### Load

When listing claims for a project (Review refresh, `list_claims` without a special flag):

1. Ensure there is a run to attach to: use **latest run** if one exists; if there is **no** run, `create_run` with papers inferred from the files’ `paper_canonical_id`s (empty paper list is OK if needed). Tag `llm_backend` / `llm_model` as `file-import` (or similar) so it is obvious this run was not a local LLM extract.
2. Discover claim files: if `claims/index.json` exists and parses as `citehop.claims.index.v1`, use its `file` names as a **hint list**; **always also glob** `claims/*.json` excluding `index.json` (index can be stale). Load **full objects** from each claim file, not from the index rows (index has no quote/offsets).
3. Accept a file if `format` is `citehop.claim.v1` (or missing format but required fields are present — be liberal on old mirrors). Must have `claim_id`, `claim_type`, `claim_text`, `quoted_source_span`.
4. **Upsert into SQLite** any `claim_id` **not already in the latest run**. Fill `project_id` with the open project. Set `run_id` to that latest/import run. Defaults if omitted: `verification_status=unverified_by_human`, `agreement=single_pass_only`, pass flags false/true as needed, `confidence_self_reported=medium`, `human_edit=null`, `structured_fields={}`, `source_char_offset` from the file or `[0,0]` if absent (highlight may no-op; do not invent a locate unless the quote exists in stored text — optional `locate_span` only when offset missing **and** paper text is available).
5. If `claim_id` **already exists** in the latest run: **SQLite wins**. Do not overwrite reviewed flags or extract output with a stale file. (Review already rewrites the JSON file after Confirm.)
6. Then `query_claims` as today (same filters, same `REVIEW_PRIORITY` sort). UI table/detail/go-to-paper/Open JSON unchanged.

Refresh Review after ingest so new files appear without restarting the app (existing Refresh / `on_show` is enough if `list_claims` syncs each time). Sync should be **idempotent** and cheap when nothing new is on disk (e.g. skip insert if all file ids are already in the run).

### Review actions

Keep `review_claim` → `apply_review` → update DB → `_persist_claim_files`. After ingest, Confirm/Reject/Edit work on file-imported rows **exactly** as on extracted rows. JSON on disk after Confirm must match what extract-path claims look like today (`verification_status` updated, same payload keys).

### Export

`extract export` / Evidence table still read **SQLite latest run** (now including ingested file claims). Default export = all claims in that run; evidence table default = `human_confirmed`. No new export format. `handoff` text stays.

## Files to touch (prefer this set)

- `src/citehop/claims/files.py` — read helpers for one claim file + discover paths (index + glob)
- `src/citehop/claims/api.py` — `list_claims` (and maybe a small `sync_claim_files`) 
- `src/citehop/claims/store.py` — insert imported rows; **do not** weaken `complete_paper` / `apply_review`
- Tests: `tests/test_claims_hardening.py` and/or `test_claims_engine.py`

Do **not** rewrite `ui/pages/review.py` except a one-line comment if needed. No new tabs, no new buttons.

## Tests (must add)

- Claim JSON on disk, **not** in DB → `list_claims` returns it; then `review_claim(..., "confirm")` → DB + file show `human_confirmed`.
- Claim already in DB with `human_rejected` + a file that still says `unverified_by_human` → list still shows **rejected** (DB wins).
- `index.json` stale (missing a file that exists) → glob still picks up the file.
- Garbage JSON in `claims/` → skipped; other claims still list.
- Export after confirm includes the imported claim as `human_confirmed` in `citehop.claims.v1`.

Run existing hardening/engine/schema tests; do not delete them.

## Out of scope

Watching the folder live (Refresh/`on_show` is enough). Importing `exports/claims-*.json` or `review_output/`. Changing extract workers. Schema auto-add of unknown `claim_type`s (unknown types: skip file or list with existing engine ignore rules — do not crash; Edit may refuse types not in schema, same as today). WAL, UI restyle, QC4HEP corpus extraction.

## Process

1. Read `list_claims`, `apply_review`, `files.py`, Review `_reload_claims` / `_review` only.
2. Implement sync-on-list + tests. Stop.
3. Summarize: how import run is created, conflict rule (DB wins), tests added.

## Done when

- Dropping a valid `citehop.claim.v1` into `_projects/<id>/claims/` and opening Review (or Refresh) shows the claim.
- Confirm/Reject/Edit work; JSON file and export reflect the same status as an extracted claim would.
- Review UI unchanged to the user. No extra features.
---
