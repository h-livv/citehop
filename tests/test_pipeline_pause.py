"""Corpus analysis pause: in-flight fetch stops; pending papers stay pending."""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from citehop.http_client import FetchCancelled, RateLimitedClient
from citehop.ids import file_id
from citehop.pipeline import BuildPaused, CorpusBuilder
from citehop.seed import SeedQuery


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

    def test_store_pdf_and_missing_pdf_backfill(self) -> None:
        builder = self._builder()
        cid = "arxiv:1234.56789"
        rec = _pending_paper(cid)
        rec["arxiv_id"] = "1234.56789"
        rec["status"] = "fetched"
        rec["full_text_available"] = 1
        builder.manifest.upsert_paper(rec)
        fake = b"%PDF-1.4\n%fake-pdf\n"
        builder._fetch_arxiv_pdf_bytes = lambda _row: fake  # type: ignore[method-assign]
        builder.fetch_missing_pdfs()
        self.assertTrue(builder._pdf_path(cid).is_file())
        self.assertEqual(builder.snapshot_counts()["pdf_count"], 1)
        builder.manifest.close()

    def test_progress_callback_sees_fetched_count(self) -> None:
        seen: list[dict] = []
        builder = CorpusBuilder(
            self.root / "corpus-progress",
            log=lambda _m: None,
            progress=seen.append,
        )
        builder.manifest.upsert_paper(_pending_paper("paper-a"))
        builder.manifest.set_status("paper-a", "fetched", full_text_available=1)
        builder._emit_progress()
        self.assertTrue(seen)
        self.assertEqual(seen[-1]["status_counts"].get("fetched"), 1)
        self.assertEqual(seen[-1]["paper_count"], 1)
        self.assertEqual(seen[-1]["success_count"], 1)
        builder.manifest.close()

    def test_unsuccessful_fetch_is_retried_not_counted_as_processed(self) -> None:
        builder = self._builder()
        builder.manifest.upsert_paper(_pending_paper("paper-miss"))
        builder._finish_no_fulltext("paper-miss", {}, "no_open_access_version")
        row = builder.manifest.get_paper("paper-miss")
        assert row is not None
        self.assertEqual(row["status"], "failed_retry")
        self.assertEqual(builder.manifest.count_successful_fetches(), 0)
        waiting = [r["canonical_id"] for r in builder.manifest.papers_needing_fetch()]
        self.assertIn("paper-miss", waiting)
        self.assertEqual(builder.snapshot_counts()["success_count"], 0)
        builder.manifest.close()

    def test_requeue_unsuccessful_leaves_true_fetches_alone(self) -> None:
        builder = self._builder()
        miss = _pending_paper("paper-abs")
        miss["status"] = "fetched"
        miss["full_text_available"] = 0
        builder.manifest.upsert_paper(miss)
        ok = _pending_paper("paper-ok")
        ok["status"] = "fetched"
        ok["full_text_available"] = 1
        builder.manifest.upsert_paper(ok)
        n = builder.manifest.requeue_unsuccessful_fetches()
        self.assertEqual(n, 1)
        self.assertEqual(builder.manifest.get_paper("paper-abs")["status"], "failed_retry")
        self.assertEqual(builder.manifest.get_paper("paper-ok")["status"], "fetched")
        self.assertEqual(builder.manifest.count_successful_fetches(), 1)
        builder.manifest.close()

    def test_fetch_one_saves_arxiv_pdf_for_new_pending_paper(self) -> None:
        builder = self._builder()
        cid = "arxiv:1234.56789"
        rec = _pending_paper(cid)
        rec["arxiv_id"] = "1234.56789"
        builder.manifest.upsert_paper(rec)
        row = builder.manifest.get_paper(cid)
        assert row is not None
        latex = "body " * 80
        fake_pdf = b"%PDF-1.4\n%fake-pdf-bytes\n"

        def fake_eprint(*_a, **_k):
            return b"%latex", "application/x-eprint"

        with (
            patch("citehop.pipeline.arxiv_api.fetch_eprint", side_effect=fake_eprint),
            patch(
                "citehop.pipeline.extract_eprint_text",
                return_value=("arxiv_latex", latex, None),
            ),
        ):
            builder._fetch_arxiv_pdf_bytes = lambda _row: fake_pdf  # type: ignore[method-assign]
            builder._fetch_one(row)
        dest = builder._pdf_path(cid)
        self.assertTrue(dest.is_file(), "Analyze/CLI fetch must write raw/<id>.pdf")
        self.assertEqual(dest.read_bytes(), fake_pdf)
        fetched = builder.manifest.get_paper(cid)
        assert fetched is not None
        self.assertEqual(fetched["status"], "fetched")
        self.assertEqual(fetched["full_text_available"], 1)
        self.assertEqual(builder.snapshot_counts()["pdf_count"], 1)
        builder.manifest.close()

    def test_fetch_one_saves_oa_pdf_for_new_pending_paper(self) -> None:
        builder = self._builder()
        cid = "doi:10.1234/new-paper"
        rec = _pending_paper(cid)
        rec["doi"] = "10.1234/new-paper"
        rec["metadata"] = {"s2_open_access_pdf_url": "https://example.org/oa.pdf"}
        builder.manifest.upsert_paper(rec)
        row = builder.manifest.get_paper(cid)
        assert row is not None
        fake_pdf = b"%PDF-1.4\n%oa-pdf-bytes\n"
        with (
            patch("citehop.pipeline.extract_pdf_text", return_value="claim " * 80),
            patch.object(
                builder.http,
                "download",
                return_value=SimpleNamespace(content=fake_pdf),
            ),
        ):
            builder._fetch_one(row)
        self.assertTrue(builder._pdf_path(cid).is_file())
        self.assertEqual(builder._pdf_path(cid).read_bytes(), fake_pdf)
        fetched = builder.manifest.get_paper(cid)
        assert fetched is not None
        self.assertEqual(fetched["status"], "fetched")
        builder.manifest.close()

    def test_copy_seed_pdf_into_raw_for_new_seed(self) -> None:
        seed_pdf = self.root / "uploaded.pdf"
        seed_pdf.write_bytes(b"%PDF-1.4\n%uploaded-seed\n")
        builder = CorpusBuilder(
            self.root / "corpus-seed",
            seed=SeedQuery(pdf=seed_pdf),
            log=lambda _m: None,
        )
        cid = "arxiv:seed.00001"
        rec = _pending_paper(cid)
        rec["relation_to_seed"] = "seed"
        builder.manifest.upsert_paper(rec)
        self.assertTrue(builder._copy_seed_pdf(cid))
        self.assertEqual(
            builder._pdf_path(cid).read_bytes(),
            b"%PDF-1.4\n%uploaded-seed\n",
        )
        builder.manifest.close()

    def test_missing_pdf_backfill_skips_pending_new_papers(self) -> None:
        builder = self._builder()
        rec = _pending_paper("arxiv:pend.00001")
        rec["arxiv_id"] = "pend.00001"
        builder.manifest.upsert_paper(rec)
        called: list[str] = []
        builder._save_pdf_for_row = (  # type: ignore[method-assign]
            lambda row: called.append(row["canonical_id"]) or False
        )
        builder.fetch_missing_pdfs()
        self.assertEqual(called, [])
        builder.manifest.close()


if __name__ == "__main__":
    unittest.main()
