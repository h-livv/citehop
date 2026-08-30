"""Schema round-trip and validation. Run before relying on the extraction engine."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from citehop.claims.schema import (
    SchemaError,
    clone_schema,
    empty_schema,
    list_templates,
    load_schema_file,
    load_template,
    save_schema_file,
    type_ids,
    validate_schema,
    validate_schema_for_run,
)


class SchemaTests(unittest.TestCase):
    def test_templates_are_valid_and_unrelated(self) -> None:
        ids = {t["template_id"] for t in list_templates()}
        self.assertIn("recipe_claims", ids)
        self.assertIn("quantitative_claims", ids)
        self.assertIn("quantum_computing_review", ids)
        recipe = load_template("recipe_claims")
        quant = load_template("quantitative_claims")
        self.assertIn("ingredient_substitution", type_ids(recipe))
        self.assertIn("cooking_time_estimate", type_ids(recipe))
        self.assertIn("quantitative_result", type_ids(quant))
        self.assertTrue(set(type_ids(recipe)).isdisjoint(set(type_ids(quant))))

    def test_recipe_schema_round_trips(self) -> None:
        schema = load_template("recipe_claims")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "schema.json"
            save_schema_file(path, schema)
            loaded = load_schema_file(path)
            self.assertEqual(loaded, schema)
            again = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(validate_schema(again), schema)

    def test_clone_then_edit_arbitrary_types(self) -> None:
        cloned = clone_schema(load_template("recipe_claims"), "kitchen_v2")
        cloned["claim_types"].append(
            {
                "type_id": "plating_note",
                "display_name": "Plating note",
                "description": "How the dish should look when served.",
                "structured_fields": [
                    {"key": "vessel", "type": "string"},
                    {"key": "garnish", "type": "boolean"},
                ],
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "kitchen.json"
            save_schema_file(path, cloned)
            loaded = load_schema_file(path)
            self.assertEqual(loaded["schema_id"], "kitchen_v2")
            self.assertIn("plating_note", type_ids(loaded))
            fields = loaded["claim_types"][-1]["structured_fields"]
            self.assertEqual([f["key"] for f in fields], ["vessel", "garnish"])

    def test_empty_schema_fails_loudly_for_run(self) -> None:
        schema = empty_schema("blank")
        validate_schema(schema)
        with self.assertRaisesRegex(SchemaError, "no claim types"):
            validate_schema_for_run(schema)

    def test_malformed_schema_fails_loudly(self) -> None:
        with self.assertRaisesRegex(SchemaError, "schema_id"):
            validate_schema({"claim_types": []})
        with self.assertRaisesRegex(SchemaError, "snake_case"):
            validate_schema(
                {
                    "schema_id": "x",
                    "claim_types": [
                        {
                            "type_id": "Bad Type",
                            "display_name": "Bad",
                            "description": "nope",
                            "structured_fields": [],
                        }
                    ],
                }
            )
        with self.assertRaisesRegex(SchemaError, "enum_values"):
            validate_schema(
                {
                    "schema_id": "x",
                    "claim_types": [
                        {
                            "type_id": "t",
                            "display_name": "T",
                            "description": "d",
                            "structured_fields": [{"key": "k", "type": "enum"}],
                        }
                    ],
                }
            )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaisesRegex(SchemaError, "not valid JSON"):
                load_schema_file(path)


if __name__ == "__main__":
    unittest.main()
