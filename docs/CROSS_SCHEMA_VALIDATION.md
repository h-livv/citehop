# Cross-schema validation (Phase 2, Step 5)

Still true as of 2026-08-30: the engine has no domain-specific Python branches.
Starter templates and how to clone them: [USAGE.md](USAGE.md).

The same extraction engine (`citehop.claims.engine`) was run against two projects
whose schemas share no claim types:

| Project | Template (data file only) | Claim types in output |
| --- | --- | --- |
| Kitchen | `recipe_claims.json` | subset of `ingredient_substitution`, `cooking_time_estimate` |
| Lit review | `quantum_computing_review.json` | subset of that template’s `type_id`s |

A third project used `quantitative_claims.json` (`quantitative_result`, `comparison_claim`).

**Result:** `tests.test_claims_engine.EngineCrossSchemaTests` passed. Every claim had a
verbatim `quoted_source_span`, a `[start, end)` offset into the stored paper text,
`agreement` from dual-pass merge, and `verification_status: unverified_by_human` until a
human review action.

**What changed for the second schema:** nothing in the engine. Prompt construction
iterates `schema["claim_types"]`; alignment uses span proximity; the fixture LLM used
in tests parses `<<<SCHEMA_JSON>>>` from the prompt rather than importing a template.
No `project_domain_label` or `schema_id` branch exists in Python under `citehop/claims/`.

Empty schemas fail at `start_run` with a validation error (not a silent default taxonomy).
