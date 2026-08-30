"""Corpus analysis pause: in-flight fetch stops; pending papers stay pending."""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from citehop.http_client import FetchCancelled, RateLimitedClient
from citehop.ids import file_id
from citehop.pipeline import BuildPaused, CorpusBuilder


def _pending_paper(canonical_id: str, relation: str = "backward_reference") -> dict:
    return {
        "canonical_id": canonical_id,
        "file_id": file_id(canonical_id),
        "status": "pending",
        "relation_to_seed": relation,
        "title": canonical_id,
        "abstract": "An abstract.",
        "full_text_available": 0,
        "metadata": {},
    }


class PipelinePauseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _builder(self) -> CorpusBuilder:
        return CorpusBuilder(self.root / "corpus", log=lambda _m: None)

    def test_pause_mid_full_text_keeps_fetched_and_leaves_rest_pending(self) -> None:
        builder = self._builder()
        for cid in ("paper-a", "paper-b", "paper-c"):
            builder.manifest.upsert_paper(_pending_paper(cid))

        def fake_fetch(row) -> None:
            builder._check_pause()
            if row["canonical_id"] == "paper-b":
                builder.pause()
                builder._check_pause()
            builder.manifest.set_status(
                row["canonical_id"],
                "fetched",
                full_text_available=1,
            )

        builder._fetch_one = fake_fetch  # type: ignore[method-assign]
        with self.assertRaises(BuildPaused):
            builder.fetch_full_texts()
        statuses = {
            r["canonical_id"]: r["status"] for r in builder.manifest.all_papers()
        }
        self.assertEqual(statuses["paper-a"], "fetched")
        self.assertEqual(statuses["paper-b"], "pending")
        self.assertEqual(statuses["paper-c"], "pending")
        builder.manifest.close()

    def test_pause_does_not_mark_paper_failed_retry(self) -> None:
        builder = self._builder()
        builder.manifest.upsert_paper(_pending_paper("paper-hang"))

        def boom(_row) -> None:
            raise FetchCancelled("Corpus fetch paused")

        builder._fetch_one = boom  # type: ignore[method-assign]
        with self.assertRaises(FetchCancelled):
            builder.fetch_full_texts()
        row = builder.manifest.get_paper("paper-hang")
        assert row is not None
        self.assertEqual(row["status"], "pending")
        builder.manifest.close()

    def test_run_pause_writes_paused_not_finished(self) -> None:
        builder = self._builder()

        def stop() -> None:
            builder.pause()
            builder._check_pause()

        builder.resolve_seed = lambda: "seed"  # type: ignore[method-assign]
        builder.fetch_backward_s2 = lambda: None  # type: ignore[method-assign]
        builder.fetch_forward_s2 = lambda: None  # type: ignore[method-assign]
        builder.fetch_openalex_citations = lambda: None  # type: ignore[method-assign]
        builder.fetch_openalex_references = lambda: None  # type: ignore[method-assign]
        builder.enrich_openalex_ids = lambda: None  # type: ignore[method-assign]
        builder.fetch_full_texts = stop  # type: ignore[method-assign]
        with self.assertRaises(BuildPaused):
            builder.run()
        self.assertIsNone(builder.manifest.get_meta("run_finished_at"))
        self.assertTrue(builder.manifest.get_meta("run_paused_at"))
        builder.manifest.close()

    def test_http_sleep_aborts_immediately(self) -> None:
        log = self.root / "fetch_log.jsonl"
        client = RateLimitedClient(log)
        threading.Timer(0.08, client.abort).start()
        t0 = time.monotonic()
        with self.assertRaises(FetchCancelled):
            client._sleep(8)
        self.assertLess(time.monotonic() - t0, 2.0)

    def test_migrate_canonical_logs_conflict_and_keeps_dest(self) -> None:
        import json

        builder = self._builder()
        builder.manifest.upsert_paper(_pending_paper("old-id"))
        dest_row = _pending_paper("new-id")
        dest_row["title"] = "Keep me"
        builder.manifest.upsert_paper(dest_row)
        builder.manifest.conn.execute(
            "INSERT INTO edges(source, target, relation) VALUES(?,?,?)",
            ("old-id", "new-id", "cites"),
        )
        builder.manifest.conn.commit()
        builder._migrate_canonical("old-id", "new-id")
        old = builder.manifest.get_paper("old-id")
        dest = builder.manifest.get_paper("new-id")
        self.assertIsNotNone(old)
        self.assertEqual(dest["title"], "Keep me")
        log_path = self.root / "corpus" / "merge_conflicts.jsonl"
        rec = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(rec["old_canonical_id"], "old-id")
        self.assertEqual(rec["new_canonical_id"], "new-id")
        self.assertEqual(rec["action"], "skipped_dest_exists")
        builder.manifest.close()


if __name__ == "__main__":
    unittest.main()
