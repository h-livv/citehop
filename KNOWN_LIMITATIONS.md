# Known limitations (v1.0.0, 2026-08-30)

This is the honest leftover list after the v1.0 close-out. If a path is named
here, assume it is unverified and check it before depending on the claims.

## Scale and yield

- **Full qc4hep extraction did not complete.** The 863-paper 1-hop neighborhood
  is listed in the manifest. Open-access fetch at close-out was **415 fetched /
  448 pending**. Dual-pass extract on that corpus was **not** re-run after the
  resume/requeue fixes, and **gpt-oss-20b was unloaded from VRAM** on purpose.
- **23 claims / 203 papers is not yield.** Those 203 rows are 18 `done` + 130
  `error` + 55 `skipped_no_text`. 122 of the errors were `model is still
  loading` (false errors; resume now requeues them). Among papers that actually
  finished, **14/18 produced claims**. See `INVESTIGATION.md`.
- **Yield at full scale is not independently verified** beyond that 18-paper
  `done` slice. After you reload a model and Resume, compare agreement and
  yield to the 8 match / 9 partial_match / 6 single_pass_only / 0 disagreement
  split on those 23 claims.
- **Corpora above ~1000 papers** — no lock, memory, or ETA testing.

## Models and backends

- **Gemini** (`CITEHOP_LLM=gemini`) — never given a live extraction run.
- **Ollama models other than the hardening-pass Qwen3-0.6B** — no agreement
  distribution. The 74% match+partial figure is **FreeToken gpt-oss-20b** + the
  3-type `quantum_computing_review` schema on 18 completed papers, not 0.6B.
- **Reloading gpt-oss-20b** (or any large instruct model) is a tens-of-hours
  job on the remaining ~700 pending papers. Citehop will not start that unless
  you load the model and press Resume.
- **Machina `num_gpu` on a cached Ollama tag** — still coupled for Ollama;
  FreeToken ignores it. See `BUGS.md`.

## Text, clip, and skips

- Prompt clip is still **60 000 characters**, halved on context overflow.
  Offsets resolve against full stored text. Every claim in the 23-claim slice
  had `source_start` ≤ 1370; clip bias on results/discussion is **possible**
  but was not the cause of the 23-claim cap. Watch long papers with 0 claims
  (Dalmonte/Montangero `10.1080/00107514.2016.1151199` is the named candidate).
- **`[abstract_only]` is stripped before the LLM** as of this close-out. It was
  **not** stripped during the 23-claim run. None of those 23 quotes contain the
  header; abstract-only yield on a later run may still differ.
- Resume requeues `skipped_no_text` when a text file (or abstract) has since
  appeared. 52 of 55 skips still had no file at audit time.

## Alignment

- `_pair()` can emit `disagreement`. The 0-disagreement count on 23 claims is a
  real empty bucket (notes populated on partial and single-pass). Do not treat
  that as a merge-logic bug. `partial_match` is mixed: hyphen noise and real
  field disagreements both occur. Keep it in the review queue.
- Verbatim `quoted_source_span` locate still drops paraphrases. That is
  intentional provenance, not a missed feature.

## Export and handoff

- `citehop export` (no `extract`) is **corpus** JSON/markdown only.
- `citehop extract export` is the claims handoff (`citehop.claims.v1`). It was
  validated with the fixture LLM in unit tests, not by feeding a 863-paper
  dump into an external analysis tool.

## UI / process

- Qt click-through of every Review control is not a v1.0 gate. Go-to-paper
  (PDF highlight or text) exists; fixture tests cover empty-state copy.
- `kill -9` mid-SQLite `COMMIT` was not performed.
- Time-budget pause exists; not exercised on the gpt-oss-20b run.
- Do not start a second `CorpusBuilder` on a manifest that is already fetching
  in the UI (WAL).

## Schema / engine

- No schema versioning: claims store `claim_type` strings only.
- Breaking field-type migrate is refused, not implemented.
- Schemas with **>20 claim types** or huge enums are untested (prompt vs
  context).
- The engine has **no domain-specific branches**. QC vs transformers vs recipes
  is schema JSON only.

## Data layout

- Application code: `~/opt/citehop`.
- Corpora / projects: `/run/media/h-livv/Vault/CiteHop` (exFAT). The Vault path
  must be mounted.
