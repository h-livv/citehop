# Citehop

Citehop is a **personal research tool**. It was built for my own literature work on this machine. It is **not** a product, not supported, and not meant to ship.

Given a seed paper, it builds a **local 1-hop citation corpus** (the seed, the papers it cites, and the papers that cite it), then runs a **local model** against a **schema you define** to pull out structured claims. You review those claims against the source and keep them as JSON.

## What you can do with it

1. **Point at a paper** — drop a PDF or enter a DOI, arXiv id, or title.
2. **Fetch the 1-hop neighborhood** — live scholarly APIs only (arXiv, Crossref, Semantic Scholar, OpenAlex, Unpaywall). Pause and resume; nothing is lost.
3. **Keep what is actually open** — metadata for every paper; full text and PDF when an OA copy exists. No paywall scraping.
4. **Load a model you already have** — Ollama or FreeToken weights on this box. Unload when you are done so VRAM is free. Citehop will not download a model for you.
5. **Define what “a claim” means** — a project is one corpus + one schema. Types and fields are yours (results, comparisons, limitations, gaps, or anything else). Templates are only starters.
6. **Extract, then read** — dual-pass extraction, review in the app, jump to the quote in the paper. Each claim is also a JSON file under the project folder.

The software stops at extracted spans plus your confirm / reject / edit flags. It does not write a review, pick a research question, or treat model agreement as truth.

## Which papers this is for

This is **inference from the design and a small sample**, not a result validated across fields.

It fits **open-access, preprint-heavy** areas (physics, CS, quantum computing, math, most of ML) where full text actually arrives. Closed-journal fields will mostly get abstracts, and abstract-only extraction is thin.

It fits when the important claims are **one-sentence, self-contained facts in the prose** — a quotable span with a number attached (speedups, error rates, resource counts). It does **not** parse tables or figures, and it will miss anything that only exists as a table plus a caption three paragraphs later.

It pays off when you can write a **small, closed schema up front** (advantage, resource estimate, benchmark, limitation) and papers actually report those things in comparable form. That is why QC4HEP is a plausible match. Qualitative restatements of a paper’s own framing extract poorly; philosophy, theory-heavy argument, and most qualitative social science are a bad fit. Even good extraction does not make incomparable metrics comparable — that part is yours.

Long papers are an open risk: content past the front of the file can look like “nothing to extract” because it was never shown to the model.

Desktop: `python -m citehop ui`. A longer walkthrough is in [`docs/GETTING_STARTED.md`](docs/GETTING_STARTED.md).

## Status

This repo matches **how I run it**: a Linux box, a local LLM, corpora on a local disk. Paths, models, and defaults are personal. Expect sharp edges, machine-specific assumptions, and no compatibility promise.

## Later

I want to **generalize** this so it is not tied to one setup: any seed, any schema, **any machine**, with portable paths and a straightforward install. Until then, treat it as a private tool, not a release.