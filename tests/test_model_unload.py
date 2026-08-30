"""Unload FreeToken/Ollama from VRAM."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from citehop.models import unload_loaded_models, _stop_freetoken


class UnloadTests(unittest.TestCase):
    def test_stop_skips_when_daemon_down(self) -> None:
        with patch("citehop.models.freetoken_daemon_reachable", return_value=False):
            out = _stop_freetoken()
        self.assertTrue(out["skipped"])

    def test_stop_noops_when_engine_already_down(self) -> None:
        with patch("citehop.models.freetoken_daemon_reachable", return_value=True):
            with patch("citehop.models._freetoken_status", return_value={"running": False}):
                with patch("citehop.models._wait_freetoken_vram"):
                    with patch("citehop.models.requests.post") as post:
                        out = _stop_freetoken()
        self.assertTrue(out["already"])
        post.assert_not_called()

    def test_unload_posts_engine_stop_force(self) -> None:
        post = MagicMock()
        post.return_value.status_code = 200
        post.return_value.content = b'{"stopped":true}'
        post.return_value.json.return_value = {"stopped": True}
        statuses = [{"running": True, "starting": False}, {"running": False, "starting": False}]
        with patch("citehop.claims.llm.abort_generation") as abort:
            with patch("citehop.models.freetoken_daemon_reachable", return_value=True):
                with patch("citehop.models._freetoken_status", side_effect=statuses):
                    with patch("citehop.models.freetoken_daemon", return_value="http://127.0.0.1:1900"):
                        with patch("citehop.models.requests.post", post):
                            with patch("citehop.models._wait_freetoken_vram"):
                                with patch("citehop.models._ollama_loaded_names", return_value=set()):
                                    out = unload_loaded_models()
        abort.assert_called_once()
        post.assert_called_once()
        args, kwargs = post.call_args
        self.assertEqual(args[0], "http://127.0.0.1:1900/engine/stop")
        self.assertEqual(kwargs["json"], {"force": True})
        self.assertTrue(out["ok"])
        self.assertIn("FreeToken engine stopped", out["message"])

    def test_models_page_has_unload_button(self) -> None:
        text = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "citehop"
            / "ui"
            / "pages"
            / "models.py"
        ).read_text(encoding="utf-8")
        self.assertIn("Unload from VRAM", text)
        self.assertNotIn("Stop generation", text)


if __name__ == "__main__":
    unittest.main()
