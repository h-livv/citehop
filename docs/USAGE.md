# Citehop — commands, UI, and scripts

How I invoke this tool on this machine. Defaults write under
`/run/media/h-livv/Vault/CiteHop/`. Change `CITEHOP_CORPORA_DIR` (and related
env vars below) before running if that path is not yours.

Entry points are equivalent:

```bash
python -m citehop …          # from the venv, PYTHONPATH=src or pip install -e .
scripts/citehop …            # uses .venv/bin/python when present
citehop …                    # after `pip install -e .`
```

No subcommand, or `ui`, opens the desktop app.

The walkthrough is [GETTING_STARTED.md](GETTING_STARTED.md). This page is the
full command and control list.

---

## What the tool does

1. Resolve a **seed** (DOI, arXiv, title+author, local PDF, or `--preset NAME`).
2. Build a **1-hop** corpus: seed + papers it cites + papers that cite it. Pause
   and resume; state lives in `manifest.db`.
3. Fetch **open-access** PDFs into `raw/` and extracted text into `text/`.
   Metadata + abstract when there is no OA copy. No paywall scraping.
4. Bind a **project** (one corpus + one schema + a token budget).
5. **Extract** claims with a local model (Ollama or FreeToken). Dual pass is
   automatic: two generations per paper, then a merge. Agreement is two samples
   of the **same** model, not a validity check.
6. **Review** claims against the quoted span. Confirm / reject / edit.
7. **Export** `citehop.claims.v1` JSON. Optionally a markdown evidence table of
   rows you confirmed.

Citehop stops at extracted spans plus your flags. It does not write a literature
review or treat `match` as truth. Provider-reported citation counts (Semantic
Scholar / OpenAlex) are shown next to ingested counts; 1-hop completeness is
**not** enforced.

---

## CLI

### `ui`

```bash
python -m citehop
python -m citehop ui
```

Opens the Qt desktop app (tabs below).

### Seed arguments (shared by `sample` and `build`)

| Flag | Meaning |
| --- | --- |
| `--doi` | Seed DOI |
| `--arxiv` | Seed arXiv id |
| `--title` | Seed title (use with `--author` when possible) |
| `--author` | Author family name |
| `--venue` | Journal or venue |
| `--year` | Publication year (integer) |
| `--pdf` | Optional local PDF of the seed |
| `--preset` | Named seed. `qc4hep` is built in; others live in `~/.config/citehop/named_seeds.json`. The name is the corpus folder slug. |
| `--corpus-dir` | Output directory. Default: `$CITEHOP_CORPORA_DIR/<seed-slug>/` |

You can resume an existing corpus with `--corpus-dir` and no seed.

### `sample`

Seed + N cited papers + N citing papers. Cheap checkpoint before a full fetch.

```bash
python -m citehop sample --arxiv 1706.03762 --n-backward 5 --n-forward 5
python -m citehop sample --title "Attention Is All You Need" --author Vaswani
python -m citehop sample --preset qc4hep --n-backward 5 --n-forward 5
```

| Flag | Default |
| --- | --- |
| `--n-backward` | 5 |
| `--n-forward` | 5 |

Paused fetch: re-run the same command. Prints `Paused. Re-run the same command to continue.`
A finished sample also writes `CATALOG.md`, `papers/*.md`, `metadata/*.json`,
`citation_graph.json`, and `run_state.json` (same as `export`).

### `build`

Full 1-hop neighborhood. **`--yes` is required** (alias `--i-confirmed-the-sample`)
so a full run cannot start by accident.

```bash
python -m citehop build --yes --arxiv 1706.03762
python -m citehop build --yes --preset qc4hep
python -m citehop build --yes --corpus-dir /run/media/h-livv/Vault/CiteHop/qc4hep
```

Without `--yes` the process exits and prints the command to retry. Full-text
download is OA/arXiv only. Re-run to resume. A finished full run also writes
`README.md` in the corpus folder, plus the same catalog/notes as `export`.

Do not start a second `build`/`sample`/`fetch-pdfs` (or Analyze fetch) against a
corpus that is already fetching.

### `export` (corpus, not claims)

Writes per-paper JSON and markdown for papers already in a corpus.

```bash
python -m citehop export
python -m citehop export --corpus-dir /run/media/h-livv/Vault/CiteHop/qc4hep
```

Default: every corpus under `$CITEHOP_CORPORA_DIR/`. Writes
`metadata/<file_id>.json`, `papers/<file_id>.md`, `CATALOG.md`, a copy
`$CITEHOP_CORPORA_DIR/<slug>.md`, `citation_graph.json`, `run_state.json`.

This is **not** the claims handoff.

### `fetch-pdfs`

Download OA/arXiv PDFs into an existing corpus `raw/` folder. Requires
`manifest.db`.

```bash
python -m citehop fetch-pdfs --corpus-dir /run/media/h-livv/Vault/CiteHop/qc4hep
```

Same pause/resume behavior as build. Use this when metadata is already ingested
and you only want to continue full-text fetch.

### `extract` (claims API)

The desktop **Extract** / **Review** tabs are the primary interface. These
subcommands talk to the same `ClaimsAPI`.

`--project` is the project id (folder name under `_projects/`), not a path.

```bash
python -m citehop extract templates
python -m citehop extract projects
python -m citehop extract start --project <id>
python -m citehop extract pause --project <id>
python -m citehop extract resume --project <id>
python -m citehop extract status --project <id>
python -m citehop extract run --project <id>
python -m citehop extract claims --project <id>
python -m citehop extract export --project <id>
python -m citehop extract abort-model
python -m citehop extract unload-model
```

| Subcommand | What it does |
| --- | --- |
| `templates` | Print starter schema ids (`template_id`, claim-type count, domain label). |
| `projects` | Print `project_id`, display name, corpus dir. |
| `start` | New run over every paper in the corpus. Refuses if a run is already `running` or `paused` (resume instead). JSON status; may include `"warning"` if fetch is still open. Completed runs: start creates a **new** `run_id` and re-extracts. |
| `pause` | Abort in-flight generation; in-flight paper goes back to pending. Weights stay in VRAM. |
| `resume` | Continue a paused or crashed (`running`) run. Requeues: `extracting`, retryable errors (`model is still loading`, …), `skipped_no_text` that now has text, and **`done` papers whose `text/<file_id>.txt` mtime is newer than extract** (deletes that run’s claims for those papers). Status may include `"requeued_stale_text"`. Refuses `completed` / `failed`. |
| `status` | JSON progress, including a `coverage` object (ingested vs provider-reported counts, fetch still open, corpus papers missing from the run, done papers with newer text). |
| `run` | Start or resume, then `process_available` one paper at a time until idle. Prints progress lines, then final JSON. |
| `claims` | Print claims JSON. Filters: `--claim-type`, `--agreement`, `--verification`, `--paper` (canonical id). |
| `export` | Write `citehop.claims.v1`. `--out PATH` (default `<project>/exports/claims-<run_id>.json`). `--verification` e.g. `human_confirmed` (default: every claim in the latest run). |
| `abort-model` | Abort in-flight FreeToken generation (works after the UI has quit). |
| `unload-model` | Abort generation and unload FreeToken/Ollama from VRAM. |

`start` does **not** refuse when fetch is still open. Extraction uses whatever
text is on disk (often abstracts). The warning is the gate; you decide.

Agreement values: `match`, `partial_match`, `disagreement`, `single_pass_only`.

Verification values: `unverified_by_human`, `human_confirmed`, `human_rejected`,
`human_edited`.

---

## Desktop UI

Launch: `python -m citehop ui`, the **Citehop** app-menu entry after
`scripts/install-desktop.sh`, or `scripts/citehop ui`.

Pages, left to right: **Analyze → Corpus → Models → Projects → Schema → Extract → Review**.

If the corpus disk is unmounted, a storage banner appears. Mount the Vault, then
Refresh or restart.

### Analyze

Build or resume a 1-hop corpus.

| Control | What it does |
| --- | --- |
| KPIs | **Cited by seed**, **Citing the seed**, **With full text**, **PDFs**. Titles stay on the card. Full text is papers with extracted text, of the corpus size. PDFs is files in `raw/` over cited + citing (seed not in the denominator). |
| Coverage line | Seed count; S2 and OpenAlex lists at resolve (not a fraction); fetch still open. |
| Drop zone / **Choose PDF** | Local seed PDF; identifiers are filled from the file when possible. |
| Title, DOI, Author, arXiv, Year, Venue | Seed identifiers. Start needs DOI, arXiv, or title. |
| **Sample checkpoint** | Default. Backward/forward spinboxes (1–50, default 5). Same as CLI `sample`. |
| **Full 1-hop** | Shows checkbox **I confirmed the sample looks right — run the full 1-hop corpus**. Required, same as CLI `--yes`. |
| **Named seed** | Combo of saved names (`qc4hep` is built in). Selecting one fills identifiers and uses that name as the corpus folder. **Save as…** writes the current identifiers to `~/.config/citehop/named_seeds.json`. |
| **Start analysis** | Run sample or full fetch. |
| **Pause** | Stop; progress stays in the corpus folder. Closing the app pauses the same way. |
| **Resume** | Continue with the last payload. |
| Log | Fetch/API messages. |

### Corpus

Browse corpora under `$CITEHOP_CORPORA_DIR`.

| Control | What it does |
| --- | --- |
| Root paper selector | Pick a corpus. |
| **Refresh** / **Open folder** | Reload list / open the corpus directory. |
| **Fetch remaining PDFs** | Same as CLI `fetch-pdfs`: retry OA/arXiv full text for this corpus. Disabled while Analyze or another fetch is running. |
| **Pause fetch** | Stop the in-flight PDF/full-text fetch. Click Fetch remaining PDFs again to continue. |
| Same four KPIs as Analyze | Cited by seed / citing the seed / with full text / PDFs. |
| Search | Title, DOI, arXiv, authors. |
| Relation filter | All / Seed / Cited by seed / Cites seed. |
| Table | Title, year, relation, status, DOI, arXiv, full text. Double-click opens PDF when present. |
| Detail pane | Authors, abstract, identifiers. |
| **Open PDF** / **Open note** | `raw/` PDF or `papers/<file_id>.md`. |

### Models

Ollama tags on this box plus FreeToken weights (default Vault `freetoken/`).
Citehop will not download a model. There is no Gemini/OpenAI (or other cloud
LLM) backend.

| Control | What it does |
| --- | --- |
| Table | Model, source, GPU layers (Ollama / Machina cached `num_gpu`), size, loaded. Double-click = use. |
| **Refresh** | Re-scan Ollama and FreeToken. |
| **Use for extraction** | Write `~/.config/citehop/model.json` and load weights. |
| **Unload from VRAM** | Abort in-flight generation and free GPU memory. **Pause on Extract does not unload.** |

No sampling knobs in the UI.

### Projects

A project is one corpus + one schema + a token budget.

| Control | What it does |
| --- | --- |
| Name, Corpus, Schema template, Token budget | Create form. Budget range 1 000–99 999 999, default 500 000. |
| **Create project** | Writes `_projects/<id>/` (`project.json`, `schema.json`, `extraction.db`). |
| Table | Project, corpus, token budget, schema id, id. Select a row to use it on Schema / Extract / Review. |
| **Refresh** / **Edit schema** / **Open folder** | Reload, jump to Schema, open the project directory. |

Time budget exists on `project.json` (`time_budget_seconds`) and will pause a run
when exceeded. There is no spinbox for it in the UI.

### Schema

Edits `schema.json` for the selected project. The extractor iterates this JSON;
there is no built-in taxonomy.

| Control | What it does |
| --- | --- |
| Schema id, Domain label | Label is display-only — never used as logic. |
| Template combo + **Clone template into this project** | Overwrite schema from a starter. |
| **Save schema** | Persist. Removing/renaming a `type_id` still referenced by claims is refused; changing a field’s JSON type on such a type is refused. Adding types/fields and renaming displays is allowed. |
| **Add claim type** / **Remove type** | `type_id`, display name, description (shown to the model). Zero-field types are allowed. |
| **Add field** / **Remove field** | `key`, type (`string`, `number`, `boolean`, `enum`), `enum_values`. |

Starter templates:

| Id | Claim types |
| --- | --- |
| `quantitative_claims` | `quantitative_result`, `comparison_claim` |
| `quantum_computing_review` | `advantage_claim`, `resource_estimate`, `limitation_claim` |
| `recipe_claims` | `ingredient_substitution`, `cooking_time_estimate` (toy; proves the engine is not QC-only) |

### Extract

| Control | What it does |
| --- | --- |
| Header | Selected project and current model. |
| KPIs | Papers (done/total), tokens, ETA, status. |
| Coverage line | Cited/citing in corpus; S2 and OpenAlex lists at resolve; fetch still open; corpus papers not in this run; done papers with newer text. |
| **Start extraction** | Same as `extract start`. If fetch is still open, a dialog asks **Extract anyway?** (Cancel does not start a run). Papers without a PDF use abstracts. |
| **Pause** | Same as `extract pause`. Log notes that weights stay loaded. |
| **Resume** | Same as `extract resume`. Log notes how many papers were requeued for newer text. |
| **Open exports** | Opens the project `exports/` folder (handoff JSON after a completed run). |
| Progress log | Per-paper status; completed runs log the handoff JSON path. |

Dual pass is always on. Each claim is also a JSON file under `claims/` as papers
finish.

### Review

| Control | What it does |
| --- | --- |
| Filters | Claim type; agreement (`disagreement`, `single_pass_only`, `partial_match`, `match`); verification (`unverified_by_human`, `human_confirmed`, `human_rejected`, `human_edited`). |
| **Refresh** | Reload latest run. |
| **Export JSON…** | Save `citehop.claims.v1` (honors the verification filter; All = every claim in the latest run). |
| **Evidence table…** | Save a markdown table of **human_confirmed** rows (same as `scripts/evidence_table.py`). |
| Table | Sorted worst-agreement first. Copy: *match is two samples of the same model, not a validity check*. |
| Provenance pane | Quoted span in stored text (highlight). Stale offsets get a clamp note. |
| Claim pane | Text, fields, agreement, verification. |
| **Confirm** / **Reject** / **Edit…** | Review flags. Edit keeps the original under `human_edit`. |
| **Go to paper** | PDF highlight or text viewer at the quote. PDF search may use shorter prefixes of the quote (hyphenation); claim **grounding** still requires the quote to locate in stored text. |
| **Open JSON** | The per-claim file under `claims/`. |

---

## Helper scripts (not CLI subcommands)

Run with the project venv and `PYTHONPATH=src`, or
`./.venv/bin/python scripts/<name>.py …`. They are **read-only** except
`evidence_table.py`, which only writes the markdown file you pass to `--out`.

### `scripts/citehop`

Wrapper: `.venv/bin/python -m citehop "$@"` with `PYTHONPATH=src`.

### `scripts/install-desktop.sh`

Installs `citehop.desktop` and the SVG icon into `~/.local/share/…` for this
machine. Exec line points at `scripts/citehop ui`.

### `scripts/audit_corpus.py`

1-hop completeness / duplicate-id check. Does not write.

```bash
python scripts/audit_corpus.py /run/media/h-livv/Vault/CiteHop/qc4hep
```

Prints relation counts, fetch status, provider-reported counts from `run_meta`,
duplicate DOI/arXiv/S2/OpenAlex ids, and `merge_conflicts.jsonl` line count.

### `scripts/audit_grounding.py`

How stored quotes sit in corpus text under **current** `locate_span` (exact,
strip, whitespace-collapse — **no** 80-character prefix fallback).

```bash
python scripts/audit_grounding.py --corpus DIR --project DIR
```

Classifies: `current_locate`, `prefix80_only` (old locator would have hit),
`unmatched`, `no_text`. `span_exactly_80` is a fingerprint for historical
prefix-rewritten quotes, not proof.

### `scripts/audit_extract_drift.py`

Corpus fetch/text vs latest extraction run. Does not start, resume, or requeue.

```bash
python scripts/audit_extract_drift.py --corpus DIR --project DIR
```

Reports corpus papers missing from the run, `skipped_no_text` that now have
text, `done` with newer text mtime, fetch still open.

### `scripts/evidence_table.py`

Markdown table for reading confirmed claims. Does not extract or review.
Prefer **Review → Evidence table…** when using the app.

```bash
python scripts/evidence_table.py --json exports/claims-<run>.json
python scripts/evidence_table.py --json exports/claims-<run>.json --out evidence.md
python scripts/evidence_table.py --project DIR
python scripts/evidence_table.py --json export.json --verification ""   # every claim
```

Columns: paper, type, fields, quote, verification. Default `--verification
human_confirmed`. Empty string disables the filter. Typical flow: `citehop
extract export --project ID --verification human_confirmed` then this script.

---

## Environment variables

| Variable | Role |
| --- | --- |
| `CITEHOP_CORPORA_DIR` | Corpus root. Default `/run/media/h-livv/Vault/CiteHop`. |
| `CITEHOP_PROJECTS_DIR` | Projects root. Default `$CITEHOP_CORPORA_DIR/_projects`. |
| `CITEHOP_CONFIG_DIR` | Config. Default `~/.config/citehop`. Holds `model.json` and `named_seeds.json`. |
| `CITEHOP_MODEL_SETTINGS` | Override path for `model.json`. |
| `CITEHOP_CONTACT_EMAIL` | Unpaywall / User-Agent mailto. Default is a personal address. |
| `SEMANTIC_SCHOLAR_API_KEY` or `S2_API_KEY` | S2 key; reduces throttling. |
| `CITEHOP_LLM` | Force backend: `ollama` or `freetoken`. Empty: Models-tab selection. `fixture` / `grounded` / `test` are unit tests only. Cloud LLM APIs are not supported. |
| `CITEHOP_OLLAMA_HOST` / `OLLAMA_HOST` | Ollama HTTP. Default `http://127.0.0.1:11434`. |
| `CITEHOP_OLLAMA_MODEL` | Ollama tag when using the Ollama backend from env. |
| `CITEHOP_FREETOKEN_DIR` | Weights directory. Else FreeToken desktop.json, else `/run/media/$USER/Vault/freetoken`. |
| `CITEHOP_FREETOKEN_DAEMON` | Daemon URL. Else desktop.json (~port 1900). |
| `CITEHOP_FREETOKEN_HOST` | Engine URL. Else desktop.json (~port 1919). |
| `CITEHOP_FREETOKEN_PYTHON` | Python for FreeToken abort helper. |
| `CITEHOP_FT_ABORT_UID_PATH` | Abort uid file. Else `$XDG_RUNTIME_DIR`. |
| `CITEHOP_EXTRACT_LEASE_SECONDS` | Abandoned `extracting` rows (crash). Default 600. |
| `MACHINA_CONFIG_DIR` | Machina GPU-layer cache. Default `~/.config/machina`. |

---

## On disk

Application code: `~/opt/citehop`. Do not write corpora here.

Corpus `$CITEHOP_CORPORA_DIR/<slug>/`:

- `manifest.db` — papers, edges, run_meta (resume)
- `raw/` — OA PDFs
- `text/` — extracted text (`<file_id>.txt`)
- `metadata/` — per-paper JSON (after `export`)
- `papers/` — Obsidian-style notes (after `export`)
- `CATALOG.md`, `citation_graph.json`, `run_state.json`
- `README.md` — after a confirmed full `build`
- `merge_conflicts.jsonl` — identity-merge notes (audit this; ingest does not
  fail closed on duplicates)

Project `$CITEHOP_PROJECTS_DIR/<id>/`:

- `project.json`, `schema.json`, `extraction.db`
- `claims/*.json`, `claims/index.json`
- `exports/claims-<run_id>.json`

---

## Grounding and resume (current engine)

`locate_span` accepts a quote only if it appears in stored text (exact, strip, or
whitespace-collapsed). An unmatched quote is dropped; there is **no** 80-character
prefix fallback. Claims already stored from older runs are not rewritten — use
`audit_grounding.py`.

Prompt clip is 60 000 characters (halved on context overflow). Offsets resolve
against full stored text.

Resume after a PDF replaces an abstract: `done` papers with a newer `text/` file
are requeued and their claims for that run are deleted.

---

## Tests (this machine)

```bash
cd ~/opt/citehop
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests
```

Use the venv (PyMuPDF is not on system Python).
