# Docs

If you found this repo by accident: **start at the [root README](../README.md)**. Citehop is a private literature tool. It is not a product, not supported, and will not run on your machine until you rewrite hardcoded paths and other personal wiring. There is no installer.

This folder is extra context, not a user manual for a shipped app.

## Read in this order

| Doc | What it is |
| --- | --- |
| [README](../README.md) | What it does, who it is for, why you should not clone-and-run |
| This page | Map of the rest, what lands on disk, what it will not do |
| [GETTING_STARTED.md](GETTING_STARTED.md) | How *I* run it (sample → full 1-hop → schema → extract → review → JSON). Defaults point at my disk. |
| [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) | Honest leftovers after a close-out on one corpus. Yield numbers there are not a product claim. |

Lab notes, not onboarding:

| Doc | What it is |
| --- | --- |
| [BUGS.md](BUGS.md) | Hardening-pass bugs and lifecycle decisions |
| [INVESTIGATION.md](INVESTIGATION.md) | Evidence log for one QC4HEP extraction run (the “23 claims” story) |
| [CROSS_SCHEMA_VALIDATION.md](CROSS_SCHEMA_VALIDATION.md) | Same engine, unrelated schemas; no domain hardcoded in Python |

## Workflow in one paragraph

Point at a seed paper → build a **1-hop** corpus (seed + papers it cites + papers that cite it) → keep metadata and any **open-access** PDF/text → load a **model you already have** → create a **project** with a **schema you write** → extract claims → review them against the quote → take the JSON. Citehop stops there. Interpreting the claims is yours.

QC4HEP / Di Meglio 2024 is only a named preset for one seed I care about. The same path works for any seed the APIs can resolve.

## What you would see on disk (on my machine)

Corpora are **not** inside this git tree. Defaults:

- Corpus: `/run/media/h-livv/Vault/CiteHop/<slug>/` — `manifest.db`, `raw/` (PDFs), `text/`, `metadata/`
- Projects: `/run/media/h-livv/Vault/CiteHop/_projects/<id>/` — `project.json`, `schema.json`, `extraction.db`, `claims/*.json`, `exports/`

On another machine those paths are wrong until you change them.

## What it talks to

Live: arXiv, Crossref, Semantic Scholar, OpenAlex, Unpaywall. Local: Ollama and/or FreeToken, as configured on this box. It does not scrape publisher paywalls.

## What it will not do

- 2-hop citation expansion
- Download a model for you
- Treat dual-pass `match` as scientific truth
- Turn claims into a paper, a review, or a research question
- Run unmodified on a random laptop
