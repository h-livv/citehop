# Docs

If you found this repo by accident: **start at the [root README](../README.md)**. Citehop is a private literature tool. It is not a product, not supported, and will not run on your machine until you rewrite hardcoded paths and other personal wiring. There is no installer.

This folder is extra context, not a user manual for a shipped app.

## Read in this order

| Doc | What it is |
| --- | --- |
| [README](../README.md) | What it does, who it is for, why you should not clone-and-run |
| This page | Map of the rest, what lands on disk, what it will not do |
| [GETTING_STARTED.md](GETTING_STARTED.md) | How *I* run it (sample → full 1-hop → schema → extract → review → JSON). Defaults point at my disk. |
| [USAGE.md](USAGE.md) | Full CLI, desktop tabs, helper scripts, environment variables |
| [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) | Honest leftovers after a close-out on one corpus. Yield numbers there are not a product claim. |

Lab notes, not onboarding:

| Doc | What it is |
| --- | --- |
| [BUGS.md](BUGS.md) | Hardening-pass bugs and lifecycle decisions, plus later engine changes |
| [INVESTIGATION.md](INVESTIGATION.md) | Frozen evidence log for one QC4HEP extraction run (the “23 claims” story) |
| [CROSS_SCHEMA_VALIDATION.md](CROSS_SCHEMA_VALIDATION.md) | Same engine, unrelated schemas; no domain hardcoded in Python |

## Workflow in one paragraph

Point at a seed paper → build a **1-hop** corpus (seed + papers it cites + papers that cite it) → keep metadata and any **open-access** PDF/text → load a **model you already have** → create a **project** with a **schema you write** → extract claims → review them against the quote → take the JSON (optionally a markdown evidence table of confirmed rows). Citehop stops there. Interpreting the claims is yours.

QC4HEP / Di Meglio 2024 is one built-in named seed. You can save any other seed under a short name; the same path works for any paper the APIs can resolve.

Desktop tabs: **Analyze**, **Corpus**, **Models**, **Projects**, **Schema**, **Extract**, **Review**. CLI: `ui`, `sample`, `build --yes`, `export`, `fetch-pdfs`, `extract …`. Details in [USAGE.md](USAGE.md).

## What you would see on disk (on my machine)

Corpora are **not** inside this git tree. Defaults:

- Corpus: `/run/media/h-livv/Vault/CiteHop/<slug>/` — `manifest.db`, `raw/` (PDFs), `text/`, `metadata/`, `papers/`
- Projects: `/run/media/h-livv/Vault/CiteHop/_projects/<id>/` — `project.json`, `schema.json`, `extraction.db`, `claims/*.json`, `exports/`

Override with `CITEHOP_CORPORA_DIR` / `CITEHOP_PROJECTS_DIR`. On another machine the defaults are wrong until you change them.

## What it talks to

Live: arXiv, Crossref, Semantic Scholar, OpenAlex, Unpaywall. Local models:
Ollama and/or FreeToken on this box. No cloud LLM APIs. It does not scrape
publisher paywalls.

## What it will not do

- 2-hop citation expansion
- Download a model for you
- Treat dual-pass `match` as scientific truth (it is two samples of the same model)
- Guarantee 1-hop completeness vs Semantic Scholar / OpenAlex reported counts
- Turn claims into a paper, a review, or a research question
- Run unmodified on a random laptop
