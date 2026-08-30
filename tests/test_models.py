"""Machina GPU-layer lookup and model settings round-trip."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from citehop.models import load_settings, remembered_gpu_layers, save_settings, select_model


class ModelConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        os.environ["MACHINA_CONFIG_DIR"] = str(root / "machina")
        os.environ["CITEHOP_MODEL_SETTINGS"] = str(root / "citehop-model.json")
        (root / "machina").mkdir()
        (root / "machina" / "gpu-layers.json").write_text(
            json.dumps(
                {
                    "ollama:hf.co/unsloth/Qwen3-14B-GGUF:UD-IQ3_XXS": {
                        "layers": 36,
                        "total": 41,
                        "v": 2,
                    }
                }
            ),
            encoding="utf-8",
        )
        (root / "machina" / "model-params.json").write_text(
            json.dumps({"models": {"other-tag:latest": {"num_gpu": 12}}}),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_reads_machina_max_gpu_layers(self) -> None:
        found = remembered_gpu_layers("hf.co/unsloth/Qwen3-14B-GGUF:UD-IQ3_XXS")
        self.assertEqual(found, (36, 41))
        found = remembered_gpu_layers("other-tag:latest")
        self.assertEqual(found, (12, None))

    def test_select_ollama_copies_gpu_layers_into_settings(self) -> None:
        saved = select_model(
            {
                "backend": "ollama",
                "model": "hf.co/unsloth/Qwen3-14B-GGUF:UD-IQ3_XXS",
            }
        )
        self.assertEqual(saved["backend"], "ollama")
        self.assertEqual(saved["num_gpu"], 36)
        self.assertEqual(saved["num_gpu_total"], 41)
        loaded = load_settings()
        self.assertEqual(loaded["num_gpu"], 36)

    def test_select_freetoken(self) -> None:
        saved = save_settings(
            {"backend": "freetoken", "model": "gpt-oss-20b", "path": "/tmp/gpt-oss-20b"}
        )
        self.assertEqual(saved["backend"], "freetoken")
        self.assertEqual(load_settings()["model"], "gpt-oss-20b")


if __name__ == "__main__":
    unittest.main()
