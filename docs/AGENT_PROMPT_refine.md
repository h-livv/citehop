# Agent prompt: refine CiteHop (focused workflow)

---

## Final goal

CiteHop’s **job stays the same**: schema → dual-pass local LLM → verbatim quote in stored text → pause/resume → Review latest run → export JSON. You are not building a new product.

You **are** making that loop usable on a real corpus:

1. **Claims are evidence.** Each row snapshots paper title, DOI/arXiv if known, year/venue, full-text vs abstract, and the character range the model actually saw.
2. **Long papers are not silently truncated.** Text longer than the clip is extracted in overlapping windows, then de-duplicated. Review can see which window a claim came from. Do not pretend one dual-pass read the whole file.
3. **Review is how you work.** Title in the table; fields as labeled rows; search/filter; DOI/arXiv links; clip range; confirm/reject/edit / go-to-paper / Open JSON unchanged in role. Do not load every paper’s full text just to fill the table.
4. **Extract can be left running.** Last paper error is visible (retryable vs not). A second extract worker cannot start on the same project while one is running.
5. **Schema edits reach the model.** Type/field descriptions are obviously model-facing; save errors from the API are shown, not swallowed.
6. **Fewer bogus disagreements.** Hyphen/whitespace noise does not create `partial_match`; enum/boolean fights still do. Trailing chatter around JSON does not fail a whole paper if a valid `{"claims":[...]}` object is present.

If a change does not serve (1)–(6), **do not do it**.

## Stay on target

- One pass at this goal, then stop.
- Do not restyle the app, add tabs, rewrite pipeline/clients/Models/Machina, add templates, parse figures/tables, add cloud LLMs, 2-hop, schema versioning, new structured-field JSON types, WAL, or an installer.
- Do not empty `KNOWN_LIMITATIONS.md`. Do not rewrite INVESTIGATION.md, README, or module names.
- Unrelated bugs: list them at the end. Do not fix them here.
- Unsure ⇒ out of scope.

## Non-negotiable

General 1-hop tool (QC4HEP is a named seed only). Local LLM only. No paywall scrape. No domain branches in the engine. `locate_span` = exact / strip / whitespace-collapse; **no** prefix fallback. Dual pass = two samples of the **same** model; Review still says `match` is not validity. `docs/BUGS.md` lifecycle stays (Start = new run, Review = latest, schema edit guards, `complete_paper` one transaction). Code in this repo; data on Vault. **No WAL.** Old `extraction.db` and old `citehop.claim.v1` must load (additive columns/keys only).

Read for contracts: `docs/BUGS.md`, `docs/CROSS_SCHEMA_VALIDATION.md`, `.cursor/rules/citehop.mdc`, files you edit. Do not tour the repo.

## In scope (closed list)

### 1. Stored claim record (additive)

`claims/store.py`, `claims/files.py`, `claims/engine.py`, export in `claims/api.py`:

- `paper_title`, `doi`, `arxiv_id`, `year`, `venue` (nullable). Join key remains `paper_canonical_id`.
- `full_text_used`: `full_text` | `abstract_only` | `unknown`.
- `prompt_char_range`: `[start, end)` of the slice sent for **that** claim.
- Optional `schema_id` on the claim; skip if the migrate gets messy.

`ALTER TABLE` with defaults. `claim_payload` ↔ `row_to_claim`. Export includes new keys. Keep `format: citehop.claim.v1`. No nested `structured_fields`, no PDF page numbers.

### 2. Windowed extraction (long papers)

`claims/engine.py` (+ align for de-dupe):

- Keep `MAX_PAPER_CHARS` and halve-on-overflow **per window**.
- If `len(text) > MAX_PAPER_CHARS`, run dual-pass on **overlapping character windows** (fixed overlap, e.g. 10–20% of window). Cap the number of windows so a huge file cannot run forever (document the cap). Short files: one window, same as today.
- Dual-pass **inside each window**, then merge claims across windows by same `claim_type` + span proximity (reuse `align.py` ideas). One claim in the DB per merged finding, with `prompt_char_range` from the window that produced it (if two windows match, pick one range and note in `disagreement_notes` only if the fields actually differed).
- Offsets stay into **full** stored text. Quotes still must `locate_span`.
- Do **not** parse PDF sections, headings, or pages. Character windows only.
- Keep stripping `[abstract_only]` before the LLM; set `full_text_used`.

### 3. Alignment, prompt, JSON parse

- `claims/align.py`: normalize Unicode hyphen + whitespace for **string** compares; enums/booleans strict. Never fold `partial_match` into `match`.
- `claims/prompt.py`: stricter generic rules (verbatim quote, schema types/fields only). No domain taxonomy, no science few-shots.
- `claims/llm.py`: **only** `parse_claims_json` — if the model wraps or trails extra text, take the first JSON object with a `claims` array. Still reject ungrounded / unknown types in `normalize_raw_claim`. No other llm.py work (timeouts, FreeToken abort, Ollama).

### 4. Review + Extract + Schema UI (workflow only)

**Review** (`ui/pages/review.py`; `paper_viewer.py` only if go-to-paper breaks):

- Table column: paper title. Do **not** call `paper_source` / load full text for every row — use claim snapshot fields and/or cheap metadata.
- Detail: labeled structured fields; title; DOI/arXiv; year; full-text vs abstract; `prompt_char_range`; pass notes.
- Search (claim text / quote) and paper filter. Keep type / agreement / verification filters and Confirm / Reject / Edit / Go to paper / Open JSON.
- Keep “match is not a validity check” copy (update tests if the string changes).

**Extract** (`ui/pages/extract.py`):

- Show last paper error; distinguish retryable (`model is still loading`, backend down) vs hard error.
- Disable Start/Resume when a worker for that project is already running. No new thread model, no ETA redesign.

**Schema** (`ui/pages/schema.py`):

- Label descriptions as text the extractor model sees.
- On save/clone failure, show the `SchemaError` / `ProjectError` message. No Schema-page redesign, no new templates.

Do not change Analyze, Corpus, Models, Projects except a regression you caused.

### 5. Tests and docs (minimum)

- Tests for: hyphen align, window+de-dupe (fixture LLM), old-row migrate, payload keys, JSON-with-trailing-text parse, Review empty-state string if changed, extract “already running” if you can test it without Qt.
- USAGE and/or KNOWN_LIMITATIONS: new keys, windowed extract, hyphen normalize. No README/INVESTIGATION rewrite.
- Existing audit scripts: don’t crash on extra claim keys. No new scripts.

## Out of scope

Corpus builder, HTTP clients, Models/Machina/`num_gpu`, new schema templates or field types, `locate_span` redesign, FreeToken/Ollama hardening beyond parse, time-budget UI, installer, Qt→web, figure/table extraction, drive-by refactors, QC4HEP bulk extract.

## Process

1. Open only in-scope files.
2. Write a plan of **at most 8 lines** mapped to final-goal (1)–(6). Implement only that.
3. Run `tests/test_claims_engine.py`, `test_claims_hardening.py`, `test_claims_schema.py`, plus tests you add.
4. Stop. Summarize files, new keys, window cap, and what you refused.

## Done when

- Goal (1)–(6) hold.
- Start → extract → review → export still works; old DBs open.
- Tests you ran are green.
- Diff has no tourist edits.
---
