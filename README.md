# CiteHop

CiteHop is a **personal research tool**. It was built for my own literature work on my machine. It is **not** a product, not supported, and not meant to ship.

**Do not clone this and expect it to run.** Corpus storage, model weights, presets, and other defaults are **hardcoded to my machine** (local disk paths, my email in the API user-agent, FreeToken/Machina layout). If you decide to run it anyway, you **must** change those paths and other personal wiring first. Skip that and it will write to the wrong disk, miss models, or fail outright. There is no installer and no portability layer. That is not a small cleanup — it is required, and it is on you.

Given a seed paper, it builds a **local 1-hop citation corpus** (the seed, the papers it cites, and the papers that cite it), then runs a **local model** against a **schema you define** to pull out structured claims. You review those claims against the source and keep them as JSON (and, if you want, a markdown evidence table of rows you confirmed).

## What you can do with it

1. **Point at a paper** — drop a PDF or enter a DOI, arXiv id, or title. Save a **named seed** if you want a short folder name (`qc4hep` is the built-in example).
2. **Fetch the 1-hop neighborhood** — live scholarly APIs only (arXiv, Crossref, Semantic Scholar, OpenAlex, Unpaywall). Pause and resume; nothing is lost. Ingested counts are shown next to provider-reported totals; completeness is not enforced.
3. **Keep what is actually open** — metadata for every paper; full text and PDF when an OA copy exists. No paywall scraping.
4. **Load a model you already have** — Ollama or FreeToken weights on this box. Unload when you are done so VRAM is free. CiteHop will not download a model for you and has no cloud LLM API.
5. **Define what “a claim” means** — a project is one corpus + one schema. Types and fields are yours (results, comparisons, limitations, gaps, or anything else). Templates are only starters.
6. **Extract, then read** — dual-pass extraction (two samples of the same model), review in the app, jump to the quote in the paper. Quotes must appear in stored text (no prefix fallback). Each claim is also a JSON file under the project folder.
7. **Export** — `citehop.claims.v1` JSON, optionally filtered to `human_confirmed`, then an optional markdown evidence table.

The software stops at extracted spans plus your confirm / reject / edit flags. It does not write a review, pick a research question, or treat model agreement as truth. Use it as gather + extract that **you still read**.

## Which papers this is for

This is **inference from the design and a small sample**, not a result validated across fields.

It fits **open-access, preprint-heavy** areas (physics, CS, quantum computing, math, most of ML) where full text actually arrives. Closed-journal fields will mostly get abstracts, and abstract-only extraction is thin.

It fits when the important claims are **one-sentence, self-contained facts in the prose** — a quotable span with a number attached (speedups, error rates, resource counts). It does **not** parse tables or figures, and it will miss anything that only exists as a table plus a caption three paragraphs later.

It pays off when you can write a **small, closed schema up front** (advantage, resource estimate, benchmark, limitation) and papers actually report those things in comparable form. That is why QC4HEP is a plausible match. Qualitative restatements of a paper’s own framing extract poorly; philosophy, theory-heavy argument, and most qualitative social science are a bad fit. Even good extraction does not make incomparable metrics comparable — that part is yours.

Long papers are an open risk: content past the front of the file can look like “nothing to extract” because it was never shown to the model.

## Documentation

| Doc | What it is |
| --- | --- |
| [docs/README.md](docs/README.md) | Map of the docs, what lands on disk, what this will not do |
| [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md) | How I run it (sample → extract → review → JSON) |
| [docs/USAGE.md](docs/USAGE.md) | Every CLI command, UI control, helper script, and env var |
| [docs/KNOWN_LIMITATIONS.md](docs/KNOWN_LIMITATIONS.md) | Unverified leftover paths and yield caveats |
| [LICENSE](LICENSE) | No license is granted |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Not taking contributions |

Desktop, on this machine: `python -m citehop ui` (or `scripts/citehop ui`).

## Status

This repo matches **how I run it**: a Linux box, a local LLM, corpora on a local disk. Paths, models, and defaults are personal. It will not work on another machine until those hardcoded locations and custom bits are replaced. Expect sharp edges and no compatibility promise.

## Later

I want to **generalize** this so it is not tied to one setup: any seed, any schema, **any machine**, with portable paths and a straightforward install. Until then, treat it as a private tool, not a release.
