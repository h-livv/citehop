# Known limitations (hardening pass, 2026-08-30)

This pass converted unknown risk into a listed, mostly regression-tested set of
issues (`BUGS.md`). It does **not** mean extraction output is trustworthy on every
backend, model, schema, or corpus size. If a path is named here, assume it is
untested and verify it before depending on the claims.

## What this pass did cover

- Fixture-LLM unit tests for atomic writes, leases, pause/resume, two projects, two
  workers, schema edit policy, review type checks, short/long text, malformed JSON,
  backend-down pause.
- One **real Ollama** model (`hf.co/unsloth/Qwen3-0.6B-GGUF:BF16`, 4096 context) on
  **three real papers** from the qc4hep sample (not the 863-paper manifest): ~32k and
  ~22k full text plus one abstract-only PRL. Dual-pass extract + confirm/edit via
  `ClaimsAPI`.
- Context-overflow retry (that 0.6B cannot swallow a 60k-char clip).

## Backends and models not tested

- **Gemini** (`CITEHOP_LLM=gemini`) — not run. Transport mapping to
  `BackendUnavailable` is by analogy with Ollama.
- **FreeToken** engine (`127.0.0.1:1919`) — not started this pass. Unload/load
  interplay with Ollama was not re-tested.
- **Ollama models other than Qwen3-0.6B** — no 1.7B / 4B / 14B / 32B agreement
  distribution. Larger instruct models may produce more `match` / `partial_match`;
  that is a hypothesis, not a measurement.
- **Machina `num_gpu` on a cached tag** — 0.6B has no cache. A probe with
  `num_gpu=99` returned HTTP 500; see `BUGS.md` deferred item.

## Corpora and scale not tested

- **Full qc4hep 863-paper manifest** — `start_run` would enqueue all of them. Do not
  treat the 3-paper scratch run as a load test.
- **Corpora above ~1000 papers** — no lock, memory, or ETA testing.
- **The 540k-char seed PDF text** (`10.1103_prxquantum.5.037001`) — not sent to
  Ollama. Fixture tests use a synthetic >60k string. Live truncation quality on a
  half-megabyte file is unknown.
- **Papers with neither full text nor abstract** — `skipped_no_text` is unit-tested
  only insofar as empty text is skipped; a live OA-missing row was not pulled.

## Schemas not tested

- Schemas with **>20 claim types** or large enum lists (prompt size vs context).
- Templates `quantum_computing_review` and `quantitative_claims` **on Ollama**
  (fixture only). Live run used a one-type `reported_finding` schema.
- **Breaking field-type migrate** — explicitly refused, not implemented.
- **Schema versioning / keeping old claims bound to an old schema file** — not
  implemented; claims store `claim_type` strings only.

## UI / process not tested

- **Qt click-through** of Extract / Review (confirm, filters, provenance highlight).
  Empty-state copy is asserted as source text. Offsets out of range are unit-tested
  via `clamp_span`, not a running `QTextEdit`.
- **Desktop file / `citehop ui` against this pass’s code** — not launched.
- **Live CLI and UI on one project at once** — two API workers were raced in a test;
  a human did not run `citehop extract start` with the Extract page open.
- **`kill -9` during a SQLite page write** — WAL + a single transaction is the
  design; we did not SIGKILL a process mid-`COMMIT`.
- **ExtractWorker QThread interruption mid-`llm.complete`** — pause aborts the
  in-flight HTTP generation, requeues the paper as `pending`, and resume retries
  it. Fixture + hanging-HTTP tests cover this; live FreeToken/Ollama abort during
  prefill is the same socket close.
- **Time-budget pause** — code path exists; not exercised this pass.

## Alignment heuristic (do not over-generalize)

On 0.6B + one claim type + three papers, **83% of grounded claims were
`single_pass_only`**, **0% `match`**. That is the opposite of fixture tests. Until
someone repeats the measurement on a larger instruct model and a richer schema,
treat agreement flags from live runs as **review-queue hints**, not as a validated
precision/recall story.

Grounding (`quoted_source_span` must `locate_span` in stored text) dropped all
claims from the IOP full-text paper even though the run marked it `done`. Verbatim
locate will systematically drop paraphrases. Relaxing that would weaken the
provenance invariant; it was not done here.

## Token / context behavior

- Prompt paper clip starts at 60k **characters**, then halves on
  `exceed_context_size_error` down to 1500 characters. Offsets are still resolved
  against **full stored text**. Claims can only quote the prefix the model saw.
- We do not read the model’s `n_ctx` before the first request.
- Token budget pause was not hit (budget 2M on a 19k-token run).

## Project / data layout

- Hardening projects were **not** written into `~/Library/Metadata/_projects`.
- This tree is application code; corpora stay under `~/Library/Metadata/<slug>/`.
- There was **no git repository** in `/home/h-livv/opt/citehop` at the end of this
  pass, so the requested “commit test and fix together” could not be executed here.
