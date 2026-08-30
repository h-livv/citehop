# Investigation log — qc4hep extraction close-out

Continued 2026-08-30. This file is evidence for the v1.0 close-out, not a redesign brief.

Data sources unless noted: `/run/media/h-livv/Vault/CiteHop/_projects/qc4hep/extraction.db`, `schema.json`, `project.json`; corpus `/run/media/h-livv/Vault/CiteHop/qc4hep/`; `~/.config/citehop/model.json`; git log in `/home/h-livv/opt/citehop`.

---

## Part 0 — Ground truth for the “203-paper / 23-claim” result

### What actually ran

There are **two** rows in `runs`. The 8/9/6/0 agreement split lives only on the second.

| | Run A | Run B (the 23-claim result) |
|---|---|---|
| `run_id` | `d235a0b7dc0f4913b01d9f90719b76d1` | `15196472fb144c09aa92d5fee5733deb` |
| `started_at` (UTC) | 2026-08-30 11:48:18 | 2026-08-30 12:08:34 |
| `status` | `completed` in 33s | `paused` at 14:41:15 |
| `papers_total` | 863 | 863 |
| `tokens_used` | 0 | 66813 |
| Claims | 0 | **23** |

Run B `run_papers` (sums to 863):

| status | n |
|---|---|
| `pending` | 660 |
| `error` | 130 |
| `skipped_no_text` | 55 |
| `done` | 18 |
| `extracting` | 0 |

**203 = 18 done + 130 error + 55 skipped_no_text.** That is “left the pending queue,” not “successfully extracted.” Only **18 papers reached `done`.** 14 of those 18 produced at least one claim (23 claims total).

### Corpus

- Path: `/run/media/h-livv/Vault/CiteHop/qc4hep`
- Seed: Di Meglio et al., `10.1103/prxquantum.5.037001` (QC4HEP / PRX Quantum 5, 037001)
- Manifest: **863 papers** (1 seed + 481 backward + 381 forward). `run_state.json` still says `run_mode: sample:backward=5,forward=5` from the sample checkpoint; the manifest was later grown to the full 1-hop neighborhood.
- Fetch at close-out: **415 `fetched` / 448 `pending`**. Full-text OA fetch is **not** finished. Extraction was started against this incomplete fetch.

### Schema

`/run/media/h-livv/Vault/CiteHop/_projects/qc4hep/schema.json` is a clone of template `quantum_computing_review`: **3 claim types** (`advantage_claim`, `resource_estimate`, `limitation_claim`). This is a multi-type schema, unlike the hardening-pass one-type `reported_finding` pilot.

Claim-type mix in the 23: 12 advantage, 10 limitation, 1 resource.

### Model

`runs` did **not** store backend/model/schema identity (gap; fixed in this close-out). From `~/.config/citehop/model.json` as of 2026-08-30 evening and from the error strings on the 130 `error` rows:

- backend **FreeToken**, model **`gpt-oss-20b`**, weights `/run/media/h-livv/Vault/freetoken/gpt-oss-20b`, engine port 1919.

That is **not** the hardening-pass Ollama `Qwen3-0.6B`. Comparing agreement rates across those two runs is comparing different models *and* different schemas.

### Did Part 2 bug-fixes from the prior prompt land before this run?

There is **no prior `INVESTIGATION.md`** in the tree. `BUGS.md` + git (`9a8b62a` first commit 19:47 IST; extraction started 17:38 IST the same day) is what we have.

| Fix named in the close-out prompt | In the tree? | Applied *before* run B? | Could it have affected this result? |
|---|---|---|---|
| Extract lease / timeout (`CITEHOP_EXTRACT_LEASE_SECONDS`, 600s) | Yes, `store.py` | Code was in the live app by the time run B paused (14:41 UTC). Git commit is later than run start. | Unlikely to change the 23 claims. No `extracting` rows left. |
| `[abstract_only]` header **not** fed to the model | **No** — `pipeline.py` still writes `[abstract_only]\n\n` + abstract; `load_paper_text` returned that prefix verbatim | Header present during run B | **Yes, in principle.** 4 of 130 `error` papers currently have that header on disk. None of the 23 claims quote the literal string `[abstract_only]`. |
| `num_gpu` decoupling from Machina | Partial: Ollama still sends Machina `num_gpu` when cached. FreeToken ignores it. | N/A for this FreeToken run | **No.** |
| `_migrate_canonical` instrumentation / `merge_conflicts.jsonl` | **No** — `pipeline.py` still has `pass` when dest exists; **no** `merge_conflicts.jsonl` on disk | Not applied | Not an extraction-yield issue. Incomplete ID merge remains a corpus-integrity hole. |

**Gap recorded:** extraction runs did not snapshot model/schema. Fixed this close-out (`runs.llm_backend`, `runs.llm_model`, `runs.schema_id`).

---

## Part 1 — Spot-check quality (no alignment redesign)

### Merge logic can emit `disagreement`

`align.py` `_pair()` sets `agreement = "disagreement"` when paired claims share a type and sit within 120 characters but have neither overlapping quotes, identical fields, nor any shared non-empty field value. `single_pass_only` always writes notes `"Only present in pass A/B"`.

On run B, SQLite:

- `match` 8, notes **NULL** (8/8)
- `partial_match` 9, notes **non-empty** (9/9)
- `single_pass_only` 6, notes **non-empty** (6/6)
- `disagreement` **0 rows**

The zero is a **real empty bucket**, not a code path that never assigns the label. Pass flags: 8 match and 9 partial are `(pass_a=1, pass_b=1)`; 2 single-pass A-only, 4 B-only.

### All 8 `match` claims (read in full)

IDs below are `claim_id` prefixes. All eight have a verbatim-looking quote, a sensible paraphrase, and schema-shaped fields. They are not two copies of garbage.

1. `4a003566…` `10.1007/jhep02(2025)118` limitation — lattice calculations in the timelike region remain challenging. Quote matches the claim. `is_fundamental: false` is a judgment call, not nonsense.
2. `7686ce87…` same paper, limitation — spacelike lattice vs timelike dispersive discrepancies. Well-formed.
3. `6aaf98c6…` `10.1007/jhep04(2026)122` advantage — hybrid simulation of Abelian LGT + fermions. `advantage_kind: unspecified` is honest; the quote does not name asymptotic vs empirical.
4. `d6cab69f…` `10.1063/5.0287269` advantage — QuGStep, 94% fewer shots. `advantage_kind: empirical`. Quote supports it.
5. `e02c6eab…` same paper, **resource_estimate** — same 94% shot reduction stored as `amount: 94`, `unit: percent`. Schema-valid but stretching “resource estimate” (it is a relative reduction, not a qubit/gate count). Still a real sentence in the paper, not invented.
6. `ba42960e…` `10.1080/17445760.2026.2626759` advantage — deeper ansatz improves generalization. Empirical, causal classification.
7. `2dad3d99…` same paper, advantage — multi-axis Pauli maps vs underfitting. Quote supports it.
8. `aac7951d…` `10.1088/0034-4885/79/1/014401` limitation — ultracold atoms lack local gauge/Lorentz invariance. Long quote is a real methods caveat.

**Conclusion:** `match` here means both passes agreed on a grounded span. It is worth treating as a review-queue priority signal, not as independently audited scientific truth.

### 4 of 9 `partial_match` (substantive vs superficial)

1. `e284936e…` `10.1002/andp.201300104` — A/B `problem_class` differs only by a Unicode hyphen (`real‑time` vs `real-time`). **Superficial.**
2. `eaf175eb…` `10.1002/spe.70080` — same `advantage_kind: empirical`; B’s `problem_class` is a shorter prefix of A’s. **Mostly wording.**
3. `dd50b316…` same paper, limitation — `is_fundamental` **False vs True**, and B adds “in NISQ systems” to `scope`. **Substantive.** Human review should look at this one.
4. `a2a38c53…` `10.1088/0034-4885/79/1/014401` — `advantage_kind` **conjectured vs empirical** for QCD-simulator hopes. **Substantive.** The quote is aspirational (“one could hope”).

The other five partials are the same pattern as (1)–(2): nearby spans, shared type, slightly different string fields (`dip depth` vs `dip depth control`; `quantum simulation` unspecified vs empirical).

**Partial_match is mixed.** Most rows are hyphen/wording noise. Two of the four inspected are real A/B disagreements on a boolean or on empirical vs conjectured. Keep them in the review queue; do not collapse partial_match into match.

### Alignment redesign?

**No.** On gpt-oss-20b + a 3-type schema, **17/23 (74%) are match or partial_match.** The hardening-pass 83% `single_pass_only` on Qwen3-0.6B + one type does not reproduce here. Zero `disagreement` is consistent with `_pair()` preferring `partial_match` whenever any non-empty field matches. That is conservative labeling, not a suppressed bucket.

---

## Part 2 — Yield audit

### Headline

**23/203 is not extraction yield.** It is 23 claims from **18 `done` papers**, plus 130 errors and 55 skips.

### Error breakdown (130)

Inspected `run_papers.error` for run B:

| bucket | n | what it is |
|---|---|---|
| `FreeToken HTTP 503: {"error":"model is still loading"}` | **122** | Engine not ready. **False error.** These papers never got a real extract. |
| context / prompt too long | 6 | Real model-limit failures after clip retry. |
| HTTP read timeout (300s) | 1 | Transport. |
| cancelled / aborted | 1 | Pause. |

Current `FreeTokenLLM.complete` maps HTTP ≥500 to `BackendUnavailable` (pause, paper stays pending). The stored 503 strings **do not** include the later “Extraction paused…” suffix, so run B classified them as per-paper `LLMError`. Those 122 rows will stay `error` forever unless requeued. **Fix in this close-out:** treat loading/503/timeouts as retryable on resume.

### Skips (55)

52 have **no** `text/<file_id>.txt` on disk now. 3 later grew files (fetch continued after extract skipped them):

- `10.1007/jhep08(2022)014` (53 313 bytes)
- `10.1038/s41534-023-00710-y` (98 881 bytes)
- `10.1038/s41586-020-2910-8` (71 450 bytes)

Expected race: extract started while OA fetch was still running. **Fix in this close-out:** resume requeues `skipped_no_text` when text has since appeared.

### `done` with zero claims (4 of 18)

| paper | text | should the schema have hit? |
|---|---|---|
| `10.1007/bf02551274` Cybenko 1989 sigmoid approximation | 29 921 chars, OA PDF | **No.** Classical approximation theory. Zero claims is correct. |
| `10.1088/0034-4885/74/1/014001` Fukushima/Hatsuda dense QCD phase diagram | 105 540 chars | **Mostly no.** QCD theory review; `qubit` count 0. Two “advantage” hits are not quantum-*computing* advantage. |
| `10.1080/00107514.2016.1151199` Dalmonte/Montangero lattice gauge theory in the QI era | 92 854 chars, `qubit`×5, `classical`×12, `resource`×5 | **Yes, plausibly.** This is the one under-extraction candidate among the four. Front matter is noisy LaTeX macros. |
| `10.1007/jhep04(2026)183` | **no text file now** | Cannot audit. `fetch_method` null. `done` with no stored text is a stale path. |

### Yield conclusion

Among papers that **actually completed** extract, **14/18 produced claims** (23 claims). That is a reasonable rate for a 3-type QC-advantage/resource/limitation schema on a mixed HEP/QI 1-hop neighborhood.

**23/203 is not an expected scientific yield.** It is dominated by **122 engine-not-ready errors**. After requeue, those papers should be tried again. Do not treat 23/203 as evidence the model cannot extract. Do not treat it as evidence the corpus is empty of claims either — 660 papers never left `pending`, and 448 corpus rows still have no fetch.

---

## Part 3 — Front-clip (60 000 characters)

`extract_paper` clips stored text to `MAX_PAPER_CHARS` (60 000) then halves on context overflow.

**Every one of the 23 claims has `source_start` ≤ 1370.** None sit past the 60k boundary.

Of 9 `done` papers that still have text files, 5 are longer than 60k. The three long papers that *did* produce claims (`10.1002/andp.201300104` 89 652; `10.1002/qute.201900052` 115 225; `10.1088/0034-4885/79/1/014401` 115 971`) still only contributed spans in the first ~1.3k characters (abstract/intro).

At char 60 000 in the Dalmonte paper the text is MPS/rishon methods, not a clean “advantage vs classical” sentence. The Cybenko paper is under 60k and correctly empty.

**Clip bias is possible but not demonstrated as the cause of the 23-claim cap.** The cap is the 18 `done` papers. Claims that exist are intro-heavy, which is also where abstracts state advantages. No claim in this run was lost *from these 23* by the 60k cut. A later full run should still watch long papers with 0 claims (Dalmonte is the watch item).

No clip-window redesign in this close-out.

---

## Part 4 — Full-scale run (did not proceed)

**Blockers found in Parts 2–3, now fixed in code but not re-run:**

1. 122 papers marked `error` for “model is still loading” must be requeued (resume now does this).
2. Corpus OA fetch is **415/863**. A second `CorpusBuilder` must not be started against the same `manifest.db` if the UI is already fetching (WAL was live at 20:21).
3. FreeToken **gpt-oss-20b was unloaded from VRAM** immediately before this close-out at the user’s request (~5.5 GB → ~114 MB). Reloading it for ~700 dual-pass papers is tens of hours of GPU time. That is not a silent background job.

**What already exists at 1-hop scale:** 863 metadata rows in the qc4hep manifest (481 + 381 + seed). That *is* the full neighborhood listing. Full-text fetch and extraction are not complete.

**Not started this session:** a new extraction run, a model reload, or a competing corpus fetch.

When you next want the full extract: Models → Use for extraction (gpt-oss-20b) → Extract → Resume. Resume will requeue the 122 loading errors and any skips that now have text. Then let it run to completion. Compare agreement/yield to the 18-paper slice in Part 1–2.

`merge_conflicts.jsonl`: **0 lines** (file absent). `_migrate_canonical` now logs and **returns** when the destination ID already exists, instead of `pass` and continuing.

---

## Close-out notes

`GETTING_STARTED.md` is the user-facing walkthrough (Vaswani 2017 / arXiv
1706.03762, not QC4HEP). Export of reviewed claims is `citehop extract export`
(`citehop.claims.v1`). Citehop stops at that JSON; interpreting QC4HEP is the
user's job. Full-scale extract did not run this session (model unloaded; 122
false 503s now requeued on resume; OA fetch incomplete).
