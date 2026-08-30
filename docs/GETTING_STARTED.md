# Getting started with Citehop

**This walkthrough is how I run Citehop on my machine.** Defaults write under
`/run/media/h-livv/Vault/CiteHop/`. If that path is not yours, stop and change
config before you run anything. See the [root README](../README.md).

Every flag, tab, script, and env var is listed in [USAGE.md](USAGE.md). This
page is the path I actually use.

Citehop builds a **local, resumable 1-hop citation corpus** around a seed paper
(the paper, the papers it cites, and the papers that cite it), then extracts
structured claims from that corpus using a schema you choose. It talks only to
live scholarly APIs (arXiv, Crossref, Semantic Scholar, OpenAlex, Unpaywall) and
to a **local** model (Ollama or FreeToken on this box). It does not scrape
paywalls: you get metadata plus an abstract when there is no open-access copy.

This walkthrough uses a **non-physics seed** on purpose: Vaswani et al.,
*Attention Is All You Need* (arXiv 1706.03762). The same steps work for any
topic. QC4HEP / Di Meglio 2024 is a built-in named seed (`--preset qc4hep`); you
can save others from Analyze.

Citehop is **gather + extract that you still read**. It will not write a
literature review. Dual-pass `match` is two samples of the same model, not a
validity check. Ingested citation counts can be lower than what Semantic Scholar
or OpenAlex reported for the seed; the UI shows both.

## Install

Python 3.11+ and a virtualenv:

```bash
cd ~/opt/citehop          # or wherever you cloned this tree
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Optional: a Semantic Scholar API key as `SEMANTIC_SCHOLAR_API_KEY` (or
`S2_API_KEY`) reduces throttling. Unpaywall uses the address in
`CITEHOP_CONTACT_EMAIL`.

Corpora are **not** written under this source tree. By default they go to
`/run/media/h-livv/Vault/CiteHop/<slug>/`. Override with `CITEHOP_CORPORA_DIR`.
Extraction projects live in `$CITEHOP_PROJECTS_DIR` (default
`$CITEHOP_CORPORA_DIR/_projects/`).

You also need an extraction model **already on this machine** (Ollama tag or
FreeToken weights). Citehop will not download a model for you. In the desktop
app: **Models** → select a model → **Use for extraction**. Pause does not unload
VRAM; **Unload from VRAM** does.

```bash
python -m citehop ui
# same:
scripts/citehop ui
```

On this machine a menu launcher is `scripts/install-desktop.sh`. After that,
**Citehop** is in the app grid; pin it to the dash if you want a taskbar click.

The rest of this page uses the CLI so you can copy-paste it. The UI runs the
same pipeline: **Analyze** (sample/full fetch) → **Corpus** → **Models** →
**Projects** → **Schema** → **Extract** → **Review**.

## 1. Point at a topic (sample first)

A sample is a cheap checkpoint: the seed plus a few cited papers and a few
citing papers. Confirm the seed is the paper you meant before spending a full
1-hop fetch.

```bash
python -m citehop sample --arxiv 1706.03762 --n-backward 5 --n-forward 5
```

That writes something like `$CITEHOP_CORPORA_DIR/attention-is-all-you-need/`
(slug comes from the resolved title). A finished sample/build already writes
`CATALOG.md` and per-paper notes. Open that or the **Corpus** tab and check
titles, years, and that the seed is Vaswani 2017.

Equivalent identifiers:

```bash
python -m citehop sample --title "Attention Is All You Need" --author Vaswani
```

`--preset qc4hep` is the same command with a named seed, not a different code
path. In the UI: **Analyze** → pick **qc4hep** (or **Save as…** for a new name),
leave **Sample checkpoint** on, **Start analysis**.

## 2. Build the full 1-hop corpus

After the sample looks right:

```bash
python -m citehop build --yes --arxiv 1706.03762
```

`--yes` is required so a full neighborhood cannot start by accident (UI: **Full
1-hop** plus the confirmation checkbox). Re-run the same command to resume a
paused fetch. Full-text download is open-access only; papers without an OA PDF
stay as metadata + abstract.

If metadata is already in `manifest.db` and you only want PDFs, use **Corpus →
Fetch remaining PDFs**, or:

```bash
python -m citehop fetch-pdfs --corpus-dir "$CITEHOP_CORPORA_DIR/<slug>"
```

`python -m citehop export --corpus-dir …` writes per-paper JSON/markdown for the
**corpus** (titles, abstracts, citation graph). That is not the claims handoff.
Claims are the next sections.

On **Analyze** / **Corpus**, four cards: cited by seed, citing the seed, papers
with full text (of the corpus size), and PDFs (files in `raw/` over cited +
citing). The line under them lists S2/OpenAlex counts from seed resolve and how
many fetches are still open — those are not denominators for the cards.

## 3. Define or clone a schema

A schema is a list of claim types and fields. The extractor has no built-in
physics, NLP, or cooking knowledge: it only sees this JSON.

Starter templates (clone, then edit):

| Template | Use when |
|---|---|
| `quantitative_claims` | Numeric results and “better than X” comparisons (this walkthrough) |
| `quantum_computing_review` | Advantage / resource / limitation claims in QC reviews |
| `recipe_claims` | Toy schema used in tests; proves the engine is not QC-only |

Desktop: **Projects** → new project pointing at the corpus (token budget default
500 000) → **Schema** → apply a template → edit types and fields (`string` /
`number` / `boolean` / `enum`). **Save schema**.

CLI: `python -m citehop extract templates` lists template ids;
`python -m citehop extract projects` lists projects. Project records live under
`_projects/`.

For this walkthrough, clone `quantitative_claims`. You should see types
`quantitative_result` (quantity, value, units) and `comparison_claim`
(subject, baseline, direction). Change labels if you want; do not put
domain-specific `if topic == …` logic anywhere — the schema *is* the domain.

## 4. Run extraction

Select the model on **Models**, then **Extract** → **Start extraction** (or
**Resume**). Dual pass is automatic: two generations per paper, then a merge.

```bash
python -m citehop extract start --project <project-id>
python -m citehop extract run --project <project-id>
python -m citehop extract status --project <project-id>
```

`start` JSON may include a `"warning"` if the corpus still has papers pending
fetch. In the UI, **Start extraction** asks **Extract anyway?** before creating
a run. Papers without a PDF still use abstracts.

Pause from the UI or `extract pause`. **Resume** requeues:

- papers stuck in `extracting` (crash / abort)
- retryable errors (including `model is still loading`)
- `skipped_no_text` that now have a text file or abstract
- `done` papers whose `text/<file_id>.txt` is **newer** than the extract (those
  claims for this run are deleted and the paper is queued again)

A completed run cannot be resumed; **Start** makes a new `run_id` and
re-extracts. Unload the model when you are done so VRAM is free
(`extract unload-model` or **Unload from VRAM**).

Quotes must appear in stored text. Paraphrases are dropped. There is no
80-character prefix fallback (older stored claims are unchanged; see
`scripts/audit_grounding.py`).

Agreement flags (`match`, `partial_match`, `disagreement`, `single_pass_only`)
are **review-queue hints**, not proof the science is correct.

## 5. Review flagged claims

**Review** lists claims, worst agreement first. For each row:

- **Confirm** — you accept the span and fields
- **Reject** — not a real claim, or wrong paper
- **Edit…** — fix text/fields; the original is kept under `human_edit`
- **Go to paper** — PDF or extracted text at the quoted span
- **Open JSON** — the per-claim file
- **Export JSON…** / **Evidence table…** — save the handoff file or a confirmed-claims markdown table

Read the quote against the paper. A `match` only means both passes agreed with
each other.

## 6. Export (this is where Citehop stops)

From the app: **Review** → **Export JSON…** (and **Evidence table…** for
confirmed rows), or **Extract** → **Open exports** after a run completes.

```bash
python -m citehop extract export --project <project-id>
python -m citehop extract export --project <project-id> --out ~/claims.json
python -m citehop extract export --project <project-id> --verification human_confirmed
```

Default path: `_projects/<id>/exports/claims-<run_id>.json`.

Optional markdown table of confirmed rows (does not extract or review):

```bash
python scripts/evidence_table.py --json _projects/<id>/exports/claims-<run_id>.json --out evidence.md
# or read extraction.db directly:
python scripts/evidence_table.py --project _projects/<id>
```

The JSON file looks like this (truncated):

```json
{
  "format": "citehop.claims.v1",
  "exported_at": "2026-08-30T18:00:00+00:00",
  "handoff": "Citehop's job ends at this file. These are extracted spans plus your review flags …",
  "project": { "project_id": "attention-is-all-you-need", "corpus_dir": "…" },
  "schema": { "schema_id": "…", "claim_types": [ { "type_id": "quantitative_result" } ] },
  "run": {
    "run_id": "…",
    "llm_backend": "freetoken",
    "llm_model": "gpt-oss-20b",
    "papers_done": 18,
    "tokens_used": 66813
  },
  "agreement_counts": { "match": 8, "partial_match": 9 },
  "verification_counts": { "human_confirmed": 3, "unverified_by_human": 20 },
  "claims": [
    {
      "claim_id": "…",
      "paper_canonical_id": "arxiv:1706.03762",
      "claim_type": "quantitative_result",
      "claim_text": "…",
      "structured_fields": { "quantity_name": "BLEU", "value": 28.4, "units": "points" },
      "quoted_source_span": "…",
      "source_char_offset": [120, 200],
      "agreement": "match",
      "verification_status": "human_confirmed",
      "human_edit": null
    }
  ]
}
```

**Citehop's job ends at this file.** What you do next — load it in a notebook,
filter `human_confirmed`, write a review, or decide a research question — is
your judgment. The software will not form a problem statement for you.

`--verification human_confirmed` exports only rows you confirmed. The default
dump includes every claim in the latest run so you can see rejects and
unreviewed items too.

## Read-only checks (optional)

These scripts do not extract or mutate the DBs (except writing a markdown file
you name):

```bash
python scripts/audit_corpus.py "$CITEHOP_CORPORA_DIR/<slug>"
python scripts/audit_grounding.py --corpus DIR --project DIR
python scripts/audit_extract_drift.py --corpus DIR --project DIR
```

See [USAGE.md](USAGE.md) for arguments.

## What Citehop will not do

- Expand to 2-hop citations unless you change the code (do not; 1-hop is the
  product).
- Download a new model without you asking.
- Treat `match` as ground truth.
- Enforce that ingested 1-hop equals provider-reported citation counts.
- Replace reading the papers.
