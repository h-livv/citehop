"""Dual-pass engine on two unrelated schemas. Same code path; no special cases."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from citehop.claims.api import ClaimsAPI, SchemaError
from citehop.claims.schema import load_template, type_ids
from citehop.ids import file_id
from citehop.store import Manifest

os.environ["CITEHOP_LLM"] = "fixture"

RECIPE_TEXT = (
    "An ingredient substitution is allowed: use margarine in place of butter. "
    "A cooking time estimate for the stew is 45 minutes at 180 degrees."
)

REVIEW_TEXT = (
    "This review states an advantage claim versus classical methods. "
    "A resource estimate of 120 logical units is given. "
    "A limitation is that the method does not scale."
)

QUANT_TEXT = (
    "The quantitative result for accuracy is 0.92 dimensionless. "
    "A comparison claim says the new method is better than the baseline."
)


def _write_corpus(root: Path, canonical_id: str, title: str, text: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "text").mkdir(exist_ok=True)
    fid = file_id(canonical_id)
    (root / "text" / f"{fid}.txt").write_text(text, encoding="utf-8")
    manifest = Manifest(root / "manifest.db")
    try:
        manifest.upsert_paper(
            {
                "canonical_id": canonical_id,
                "file_id": fid,
                "status": "fetched",
                "relation_to_seed": "seed",
                "title": title,
                "abstract": text[:180],
                "full_text_available": 1,
                "metadata": {},
            }
        )
    finally:
        manifest.close()
    return root


def _run_until_idle(api: ClaimsAPI, project_id: str) -> dict:
    status = api.start_run(project_id)
    while status["status"] == "running":
        status = api.process_available(project_id, max_papers=1)
    return status


class EngineCrossSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["CITEHOP_LLM"] = "fixture"
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.api = ClaimsAPI(projects_root=self.root / "projects")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_empty_schema_fails_before_extraction(self) -> None:
        corpus = _write_corpus(self.root / "c-empty", "p1", "Empty", "hello world")
        proj = self.api.create_project("Blank", corpus, template_id=None)
        with self.assertRaisesRegex(SchemaError, "no claim types"):
            self.api.start_run(proj["project_id"])

    def test_two_unrelated_schemas_same_engine(self) -> None:
        recipe_corpus = _write_corpus(self.root / "c-recipe", "paper-recipe", "Stew", RECIPE_TEXT)
        review_corpus = _write_corpus(self.root / "c-review", "paper-review", "Review", REVIEW_TEXT)
        recipe = self.api.create_project("Kitchen", recipe_corpus, template_id="recipe_claims")
        review = self.api.create_project(
            "Lit review", review_corpus, template_id="quantum_computing_review"
        )

        st_r = _run_until_idle(self.api, recipe["project_id"])
        st_v = _run_until_idle(self.api, review["project_id"])
        self.assertEqual(st_r["status"], "completed")
        self.assertEqual(st_v["status"], "completed")
        self.assertGreaterEqual(st_r["papers_done"], 1)
        self.assertGreaterEqual(st_v["papers_done"], 1)

        recipe_claims = self.api.list_claims(recipe["project_id"])
        review_claims = self.api.list_claims(review["project_id"])
        self.assertTrue(recipe_claims, "recipe project produced no claims")
        self.assertTrue(review_claims, "review project produced no claims")

        recipe_types = {c["claim_type"] for c in recipe_claims}
        review_types = {c["claim_type"] for c in review_claims}
        self.assertTrue(recipe_types <= set(type_ids(load_template("recipe_claims"))))
        self.assertTrue(review_types <= set(type_ids(load_template("quantum_computing_review"))))
        self.assertTrue(recipe_types.isdisjoint(review_types))

        for claim in recipe_claims + review_claims:
            self.assertEqual(claim["verification_status"], "unverified_by_human")
            self.assertIsInstance(claim["quoted_source_span"], str)
            self.assertTrue(claim["quoted_source_span"])
            start, end = claim["source_char_offset"]
            self.assertGreaterEqual(end, start)
            src = self.api.paper_source(claim["project_id"], claim["paper_canonical_id"])
            self.assertEqual(src["text"][start:end], claim["quoted_source_span"])
            self.assertIn(claim["agreement"], ("match", "partial_match", "disagreement", "single_pass_only"))
            self.assertIn("paper_title", claim)
            self.assertIn("full_text_used", claim)
            self.assertIn("prompt_char_range", claim)

        # Human review is schema-agnostic.
        first = recipe_claims[0]
        confirmed = self.api.review_claim(recipe["project_id"], first["claim_id"], "confirm")
        self.assertEqual(confirmed["verification_status"], "human_confirmed")
        edited = self.api.review_claim(
            recipe["project_id"],
            first["claim_id"],
            "edit",
            edit={"claim_text": "edited paraphrase", "structured_fields": first["structured_fields"]},
        )
        self.assertEqual(edited["verification_status"], "human_edited")
        self.assertEqual(edited["claim_text"], "edited paraphrase")
        self.assertEqual(edited["human_edit"]["original"]["claim_text"], first["claim_text"])

    def test_quantitative_template_also_runs(self) -> None:
        corpus = _write_corpus(self.root / "c-quant", "paper-quant", "Numbers", QUANT_TEXT)
        proj = self.api.create_project("Numbers", corpus, template_id="quantitative_claims")
        status = _run_until_idle(self.api, proj["project_id"])
        self.assertEqual(status["status"], "completed")
        claims = self.api.list_claims(proj["project_id"])
        self.assertTrue(claims)
        self.assertTrue({c["claim_type"] for c in claims} <= {"quantitative_result", "comparison_claim"})

    def test_engine_python_has_no_domain_taxonomy(self) -> None:
        claims_dir = Path(__file__).resolve().parents[1] / "src" / "citehop" / "claims"
        forbidden = (
            "advantage_claim",
            "resource_estimate",
            "limitation_claim",
            "ingredient_substitution",
            "cooking_time_estimate",
            "quantitative_result",
            "qc4hep",
            "qubits",
        )
        leaked = []
        for path in claims_dir.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            for word in forbidden:
                if word in text:
                    leaked.append(f"{path.name}: {word}")
        self.assertEqual(leaked, [], f"domain strings leaked into engine code: {leaked}")


if __name__ == "__main__":
    unittest.main()
