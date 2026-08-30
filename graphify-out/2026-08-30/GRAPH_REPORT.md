# Graph Report - citehop  (2026-08-30)

## Corpus Check
- 53 files · ~34,490 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 782 nodes · 2068 edges · 35 communities (23 shown, 12 thin omitted)
- Extraction: 96% EXTRACTED · 4% INFERRED · 0% AMBIGUOUS · INFERRED: 91 edges (avg confidence: 0.51)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- engine.py
- citehop/extract.py
- main_window.py
- MainWindow
- ClaimStore
- api.py
- ClaimsAPI
- Manifest
- RateLimitedClient
- ReviewPage
- SchemaPage
- citehop/models.py
- citehop
- clients/__init__.py
- citehop
- align.py
- HardeningTests
- CROSS_SCHEMA_VALIDATION.md
- claims/__init__.py
- ModelsPage
- run_app
- AnalyzePage
- DropZone
- ProjectsPage
- ExtractWorker
- Hardening pass — bugs and checklist
- LLMError
- GenerationCancelled
- llm.py
- Known limitations (hardening pass, 2026-08-30)
- _GenerationGate
- PipelinePauseTests
- locate.py
- prompt.py
- ContextTooLong

## God Nodes (most connected - your core abstractions)
1. `ClaimsAPI` - 63 edges
2. `ClaimStore` - 52 edges
3. `RateLimitedClient` - 46 edges
4. `Manifest` - 42 edges
5. `CorpusBuilder` - 35 edges
6. `HardeningTests` - 35 edges
7. `utcnow()` - 31 edges
8. `_write_corpus()` - 25 edges
9. `MainWindow` - 24 edges
10. `AnalyzePage` - 23 edges

## Surprising Connections (you probably didn't know these)
- `EngineCrossSchemaTests` --uses--> `ClaimsAPI`  [INFERRED]
  tests/test_claims_engine.py → src/citehop/claims/api.py
- `DieLLM` --uses--> `ClaimsAPI`  [INFERRED]
  tests/test_claims_hardening.py → src/citehop/claims/api.py
- `HardeningTests` --uses--> `ClaimsAPI`  [INFERRED]
  tests/test_claims_hardening.py → src/citehop/claims/api.py
- `JunkThenFixtureLLM` --uses--> `ClaimsAPI`  [INFERRED]
  tests/test_claims_hardening.py → src/citehop/claims/api.py
- `DieLLM` --uses--> `ContextTooLong`  [INFERRED]
  tests/test_claims_hardening.py → src/citehop/claims/llm.py

## Import Cycles
- None detected.

## Communities (35 total, 12 thin omitted)

### Community 0 - "engine.py"
Cohesion: 0.17
Nodes (18): Protocol, corpus_papers(), extract_paper(), _extract_paper_clipped(), ExtractionError, load_paper_text(), normalize_raw_claim(), process_one_paper() (+10 more)

### Community 1 - "citehop/extract.py"
Cohesion: 0.17
Nodes (22): BytesIO, _author_family(), _clean_doi(), _decode(), extract_eprint_text(), extract_pdf_text(), _first_arxiv(), inspect_pdf() (+14 more)

### Community 2 - "main_window.py"
Cohesion: 0.17
Nodes (12): Extraction run dashboard. Start/pause/resume go through ClaimsAPI only., Page, QWidget, Minimal model picker: Ollama tags and FreeToken weights. Extraction uses this., Create and select extraction projects. Talks only to ClaimsAPI., Review extracted claims: filters, provenance highlight, confirm/reject/edit., Schema authoring form. Writes project schema.json through ClaimsAPI., card() (+4 more)

### Community 3 - "MainWindow"
Cohesion: 0.07
Nodes (17): QMainWindow, icon_path(), Path, citehop — resumable 1-hop citation corpus builder for any seed paper., BuildWorker, MainWindow, _make_builder(), Any (+9 more)

### Community 4 - "ClaimStore"
Cohesion: 0.07
Nodes (19): ClaimStore, extract_lease_seconds(), Any, Path, Row, Per-project SQLite store for extraction runs and claim records., Atomically take one pending paper (pending → extracting)., Force every extracting paper back to pending (resume after crash / pause). (+11 more)

### Community 5 - "api.py"
Cohesion: 0.11
Nodes (35): Public API for schema, extraction runs, claim query, and human review. The…, coerce_fields(), Coerce structured_fields to the schema. Extraction (strict=False) accepts JSON-…, ProjectError, ProjectStore, Any, Path, ValueError (+27 more)

### Community 6 - "ClaimsAPI"
Cohesion: 0.14
Nodes (12): ClaimsAPI, Any, Event, Path, Start a new extraction run. After a completed run, this creates a *new* run_id…, Continue a paused run, or reattach after a crash that left status=running., Process up to max_papers pending papers. Called by the UI worker and CLI., Abort generation when another process or the UI sets pause_requested. (+4 more)

### Community 7 - "Manifest"
Cohesion: 0.06
Nodes (37): ArgumentParser, Namespace, atomic_write_bytes(), atomic_write_text(), _catalog_markdown(), export_readable(), generate_readme(), Any (+29 more)

### Community 8 - "RateLimitedClient"
Cohesion: 0.06
Nodes (72): Element, fetch_eprint(), fetch_pdf(), get_by_id(), _parse_entry(), _query(), search_by_doi(), search_by_title() (+64 more)

### Community 9 - "ReviewPage"
Cohesion: 0.16
Nodes (11): QDialog, QTextEdit, clamp_span(), Clamp [start, end) to text; third value is True if the input was out of range., EditClaimDialog, _field_widget(), _highlight(), Any (+3 more)

### Community 11 - "citehop/models.py"
Cohesion: 0.10
Nodes (37): _dir_size(), freetoken_daemon(), freetoken_daemon_reachable(), freetoken_desktop(), freetoken_engine(), freetoken_models_dir(), _freetoken_status(), _gpu_cache_keys() (+29 more)

### Community 15 - "align.py"
Cohesion: 0.36
Nodes (11): _diff_notes(), merge_passes(), _nearest_partner(), _pair(), Any, _quotes_alike(), Merge two extraction passes using spatial proximity of source spans. Alignment…, 0 if ranges overlap; otherwise the gap between them. (+3 more)

### Community 16 - "HardeningTests"
Cohesion: 0.09
Nodes (14): GroundedFixtureLLM, Deterministic extractor used in tests. Reads schema + paper from the prompt., file_id(), SQLite manifest for resumable corpus construction., EngineCrossSchemaTests, Path, Dual-pass engine on two unrelated schemas. Same code path; no special cases., _run_until_idle() (+6 more)

### Community 19 - "ModelsPage"
Cohesion: 0.19
Nodes (7): _fmt_bytes(), _gpu_cell(), LoadWorker, ModelsPage, Any, QThread, Slot

### Community 20 - "run_app"
Cohesion: 0.60
Nodes (3): QApplication, run_app(), apply_theme()

### Community 25 - "Hardening pass — bugs and checklist"
Cohesion: 0.08
Nodes (23): Backend / extraction correctness, Bugs, Checklist, General UX, Hardening pass — bugs and checklist, Lifecycle decisions (were undefined; now explicit), Review UI, Run lifecycle & idempotency (+15 more)

### Community 26 - "LLMError"
Cohesion: 0.18
Nodes (11): Resolve the configured backend and make sure it is loaded for extraction., _ready_llm(), BackendUnavailable, FreeTokenLLM, GeminiLLM, LLMError, OllamaLLM, RuntimeError (+3 more)

### Community 27 - "GenerationCancelled"
Cohesion: 0.26
Nodes (12): Session, _cancellable_request(), check_cancelled(), generation_aborted(), GenerationCancelled, _iter_lines_cancellable(), _ollama_client_error(), Exception (+4 more)

### Community 28 - "llm.py"
Cohesion: 0.25
Nodes (12): _close_http(), _fill_fields(), _fixture_claims(), parse_claims_json(), Any, socket, LLM backends for extraction. No domain vocabulary; prompts are opaque strings., RST the in-flight socket so a blocked read returns and the model stops. (+4 more)

### Community 29 - "Known limitations (hardening pass, 2026-08-30)"
Cohesion: 0.20
Nodes (9): Alignment heuristic (do not over-generalize), Backends and models not tested, Corpora and scale not tested, Known limitations (hardening pass, 2026-08-30), Project / data layout, Schemas not tested, Token / context behavior, UI / process not tested (+1 more)

### Community 30 - "_GenerationGate"
Cohesion: 0.22
Nodes (4): clear_generation_abort(), _GenerationGate, Allow a new generation after pause/resume or a new run., One in-flight HTTP generation. Pause closes the socket so the backend stops.

### Community 32 - "locate.py"
Cohesion: 0.33
Nodes (5): locate_span(), _map_collapsed_offset(), Locate a quoted span in the paper's stored text. Offsets are required., Return [start, end) character offsets into stored_text, or None., Map an index in whitespace-collapsed text back onto original.

### Community 33 - "prompt.py"
Cohesion: 0.40
Nodes (4): build_extraction_prompt(), Any, Build extraction prompts from a project's schema. This is the only module that…, Assemble a dual-pass prompt. `pass_id` is A or B (independent runs).

## Knowledge Gaps
- **31 isolated node(s):** `citehop`, `Lifecycle decisions (were undefined; now explicit)`, `Backend / extraction correctness`, `Schema lifecycle`, `Run lifecycle & idempotency` (+26 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **12 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `ClaimsAPI` connect `ClaimsAPI` to `engine.py`, `main_window.py`, `MainWindow`, `ClaimStore`, `api.py`, `Manifest`, `ReviewPage`, `SchemaPage`, `citehop/models.py`, `HardeningTests`, `ModelsPage`, `ProjectsPage`, `ExtractWorker`, `LLMError`, `GenerationCancelled`?**
  _High betweenness centrality (0.188) - this node is a cross-community bridge._
- **Why does `Manifest` connect `Manifest` to `engine.py`, `RateLimitedClient`, `ClaimStore`, `HardeningTests`?**
  _High betweenness centrality (0.099) - this node is a cross-community bridge._
- **Why does `ClaimStore` connect `ClaimStore` to `engine.py`, `HardeningTests`, `api.py`, `ClaimsAPI`?**
  _High betweenness centrality (0.082) - this node is a cross-community bridge._
- **Are the 20 inferred relationships involving `ClaimsAPI` (e.g. with `ExtractionError` and `BackendUnavailable`) actually correct?**
  _`ClaimsAPI` has 20 INFERRED edges - model-reasoned connections that need verification._
- **Are the 7 inferred relationships involving `ClaimStore` (e.g. with `ClaimsAPI` and `ExtractionError`) actually correct?**
  _`ClaimStore` has 7 INFERRED edges - model-reasoned connections that need verification._
- **Are the 4 inferred relationships involving `RateLimitedClient` (e.g. with `BuildPaused` and `CorpusBuilder`) actually correct?**
  _`RateLimitedClient` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 3 inferred relationships involving `Manifest` (e.g. with `CorpusSummary` and `BuildPaused`) actually correct?**
  _`Manifest` has 3 INFERRED edges - model-reasoned connections that need verification._