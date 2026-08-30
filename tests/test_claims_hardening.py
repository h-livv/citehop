"""Hardening-pass regressions: integrity, schema lifecycle, review, run semantics."""

from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from citehop.claims.api import BackendUnavailable, ClaimsAPI, ExtractionError, SchemaError
from citehop.claims.engine import coerce_fields
from citehop.claims.llm import GroundedFixtureLLM, check_cancelled, clear_generation_abort
from citehop.claims.locate import clamp_span
from citehop.claims.schema import check_schema_edit, validate_schema
from citehop.claims.store import ClaimStore
from citehop.ids import file_id
from citehop.store import Manifest, utcnow

from tests.test_claims_engine import RECIPE_TEXT, QUANT_TEXT, _run_until_idle, _write_corpus

os.environ["CITEHOP_LLM"] = "fixture"

ZERO_FIELD_SCHEMA = {
    "schema_id": "zero-fields",
    "project_domain_label": "",
    "claim_types": [
        {
            "type_id": "bare_claim",
            "display_name": "Bare claim",
            "description": "A stated substitution or cooking-time instruction in the recipe.",
            "structured_fields": [],
        }
    ],
}


class JunkThenFixtureLLM:
    """Pass A/B of the first paper are garbage; later calls use the fixture."""

    name = "junk-then-fixture"

    def __init__(self) -> None:
        self.n = 0
        self.inner = GroundedFixtureLLM()

    def complete(self, prompt: str) -> tuple[str, int]:
        self.n += 1
        if self.n == 1:
            return "this is not json at all", 8
        return self.inner.complete(prompt)


class DieLLM:
    name = "die"

    def complete(self, prompt: str) -> tuple[str, int]:
        raise BackendUnavailable("Ollama is not reachable at http://127.0.0.1:11434")


class HardeningTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["CITEHOP_LLM"] = "fixture"
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._old_uid_path = os.environ.get("CITEHOP_FT_ABORT_UID_PATH")
        os.environ["CITEHOP_FT_ABORT_UID_PATH"] = str(self.root / "ft-abort-uid")
        self.api = ClaimsAPI(projects_root=self.root / "projects")

    def tearDown(self) -> None:
        clear_generation_abort()
        if self._old_uid_path is None:
            os.environ.pop("CITEHOP_FT_ABORT_UID_PATH", None)
        else:
            os.environ["CITEHOP_FT_ABORT_UID_PATH"] = self._old_uid_path
        self.tmp.cleanup()

    def test_zero_field_claim_type_is_allowed_and_extracts(self) -> None:
        validated = validate_schema(ZERO_FIELD_SCHEMA)
        self.assertEqual(validated["claim_types"][0]["structured_fields"], [])
        corpus = _write_corpus(
            self.root / "c-zero",
            "p-zero",
            "Zero",
            "A bare claim in this recipe: use margarine in place of butter.",
        )
        proj = self.api.create_project("Zero fields", corpus)
        self.api.update_schema(proj["project_id"], ZERO_FIELD_SCHEMA)
        status = _run_until_idle(self.api, proj["project_id"])
        self.assertEqual(status["status"], "completed")
        claims = self.api.list_claims(proj["project_id"])
        self.assertTrue(claims)
        self.assertEqual(claims[0]["structured_fields"], {})

    def test_cannot_remove_claim_type_that_has_claims(self) -> None:
        corpus = _write_corpus(self.root / "c-lock", "p-lock", "Stew", RECIPE_TEXT)
        proj = self.api.create_project("Locked", corpus, template_id="recipe_claims")
        _run_until_idle(self.api, proj["project_id"])
        schema = self.api.get_schema(proj["project_id"])
        used = {c["claim_type"] for c in self.api.list_claims(proj["project_id"])}
        self.assertTrue(used)
        schema["claim_types"] = [
            ct for ct in schema["claim_types"] if ct["type_id"] not in used
        ]
        with self.assertRaisesRegex(SchemaError, "Cannot remove claim type"):
            self.api.update_schema(proj["project_id"], schema)

    def test_can_add_type_after_claims_exist(self) -> None:
        corpus = _write_corpus(self.root / "c-add", "p-add", "Stew", RECIPE_TEXT)
        proj = self.api.create_project("Add type", corpus, template_id="recipe_claims")
        _run_until_idle(self.api, proj["project_id"])
        schema = self.api.get_schema(proj["project_id"])
        schema["claim_types"].append(
            {
                "type_id": "plating_note",
                "display_name": "Plating note",
                "description": "How the dish looks.",
                "structured_fields": [{"key": "vessel", "type": "string"}],
            }
        )
        saved = self.api.update_schema(proj["project_id"], schema)
        self.assertIn("plating_note", [ct["type_id"] for ct in saved["claim_types"]])

    def test_cannot_change_field_type_while_claims_exist(self) -> None:
        old = validate_schema(ZERO_FIELD_SCHEMA)
        old["claim_types"][0]["structured_fields"] = [{"key": "n", "type": "number"}]
        new = validate_schema(
            {
                **ZERO_FIELD_SCHEMA,
                "claim_types": [
                    {
                        "type_id": "bare_claim",
                        "display_name": "Bare claim",
                        "description": "A claim with only text and a quote; no extra fields.",
                        "structured_fields": [{"key": "n", "type": "string"}],
                    }
                ],
            }
        )
        with self.assertRaisesRegex(SchemaError, "Cannot change"):
            check_schema_edit(old, new, {"bare_claim"})

    def test_same_type_id_does_not_leak_across_projects(self) -> None:
        schema_a = {
            "schema_id": "a",
            "project_domain_label": "",
            "claim_types": [
                {
                    "type_id": "shared_slot",
                    "display_name": "A",
                    "description": "Type A uses a string field named value.",
                    "structured_fields": [{"key": "value", "type": "string"}],
                }
            ],
        }
        schema_b = {
            "schema_id": "b",
            "project_domain_label": "",
            "claim_types": [
                {
                    "type_id": "shared_slot",
                    "display_name": "B",
                    "description": "Type B uses a number field named value.",
                    "structured_fields": [{"key": "value", "type": "number"}],
                }
            ],
        }
        ca = _write_corpus(
            self.root / "c-a",
            "paper-a",
            "A",
            "Type A uses a string field named value. Hello.",
        )
        cb = _write_corpus(
            self.root / "c-b",
            "paper-b",
            "B",
            "Type B uses a number field named value 7.",
        )
        pa = self.api.create_project("Proj A", ca)
        pb = self.api.create_project("Proj B", cb)
        self.api.update_schema(pa["project_id"], schema_a)
        self.api.update_schema(pb["project_id"], schema_b)
        _run_until_idle(self.api, pa["project_id"])
        _run_until_idle(self.api, pb["project_id"])
        a_claims = self.api.list_claims(pa["project_id"])
        b_claims = self.api.list_claims(pb["project_id"])
        self.assertTrue(a_claims and b_claims)
        self.assertTrue(all(c["claim_type"] == "shared_slot" for c in a_claims + b_claims))
        a_ids = {c["claim_id"] for c in a_claims}
        b_ids = {c["claim_id"] for c in b_claims}
        self.assertTrue(a_ids.isdisjoint(b_ids))
        with self.assertRaises(KeyError):
            self.api.get_claim(pb["project_id"], next(iter(a_ids)))

    def test_rerun_creates_new_run_and_review_shows_latest_only(self) -> None:
        corpus = _write_corpus(self.root / "c-rerun", "p-rerun", "Stew", RECIPE_TEXT)
        proj = self.api.create_project("Rerun", corpus, template_id="recipe_claims")
        first = _run_until_idle(self.api, proj["project_id"])
        self.assertEqual(first["status"], "completed")
        old_claims = self.api.list_claims(proj["project_id"])
        old_ids = {c["claim_id"] for c in old_claims}
        self.assertTrue(old_ids)
        second = _run_until_idle(self.api, proj["project_id"])
        self.assertEqual(second["status"], "completed")
        self.assertNotEqual(first["run_id"], second["run_id"])
        latest = self.api.list_claims(proj["project_id"])
        latest_ids = {c["claim_id"] for c in latest}
        self.assertTrue(latest_ids)
        self.assertTrue(old_ids.isdisjoint(latest_ids))
        store = ClaimStore(self.api.projects.db_path(proj["project_id"]))
        try:
            all_rows = store.query_claims(proj["project_id"])
            all_ids = {c["claim_id"] for c in all_rows}
            self.assertTrue(old_ids <= all_ids)
        finally:
            store.close()

    def test_start_while_paused_is_refused(self) -> None:
        corpus = _write_corpus(self.root / "c-pause-start", "p-ps", "Stew", RECIPE_TEXT)
        proj = self.api.create_project("Pause start", corpus, template_id="recipe_claims")
        self.api.start_run(proj["project_id"])
        self.api.pause_run(proj["project_id"])
        with self.assertRaisesRegex(ExtractionError, "Resume"):
            self.api.start_run(proj["project_id"])

    def test_pause_resume_pause_leaves_consistent_status(self) -> None:
        corpus = _write_corpus(self.root / "c-prp", "p-prp", "Stew", RECIPE_TEXT)
        proj = self.api.create_project("PRP", corpus, template_id="recipe_claims")
        pid = proj["project_id"]
        self.api.start_run(pid)
        self.api.pause_run(pid)
        self.assertEqual(self.api.run_status(pid)["status"], "paused")
        self.assertTrue(self.api.run_status(pid)["pause_requested"])
        self.api.resume_run(pid)
        self.assertEqual(self.api.run_status(pid)["status"], "running")
        self.assertFalse(self.api.run_status(pid)["pause_requested"])
        self.api.pause_run(pid)
        self.api.pause_run(pid)
        st = self.api.run_status(pid)
        self.assertEqual(st["status"], "paused")
        self.assertTrue(st["pause_requested"])

    def test_pause_aborts_in_flight_paper_and_resume_retries(self) -> None:
        corpus = _write_corpus(self.root / "c-abort", "p-abort", "Stew", RECIPE_TEXT)
        proj = self.api.create_project("Abort", corpus, template_id="recipe_claims")
        pid = proj["project_id"]
        self.api.start_run(pid)
        entered = threading.Event()

        class BlockingLLM:
            name = "block"

            def complete(self, prompt: str, should_stop=None) -> tuple[str, int]:
                entered.set()
                while True:
                    check_cancelled(should_stop)
                    time.sleep(0.02)

        errors: list[BaseException] = []

        def work() -> None:
            try:
                self.api.process_available(pid, max_papers=1)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        with patch("citehop.claims.api._ready_llm", return_value=BlockingLLM()):
            worker = threading.Thread(target=work)
            worker.start()
            self.assertTrue(entered.wait(5), "in-flight complete() never started")
            status = self.api.pause_run(pid)
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(status["status"], "paused")
        self.assertEqual(status["papers_done"], 0)
        self.assertEqual(status["papers_pending"], 1)
        self.assertFalse(self.api.list_claims(pid))
        store = ClaimStore(self.api.projects.db_path(pid))
        try:
            rows = store.conn.execute(
                "SELECT status FROM run_papers WHERE paper_canonical_id=?",
                ("p-abort",),
            ).fetchall()
            self.assertEqual([r["status"] for r in rows], ["pending"])
        finally:
            store.close()

        resumed = self.api.resume_run(pid)
        self.assertEqual(resumed["status"], "running")
        self.assertFalse(resumed["pause_requested"])
        status = resumed
        while status["status"] == "running":
            status = self.api.process_available(pid, max_papers=1)
        self.assertEqual(status["status"], "completed")
        self.assertEqual(status["papers_done"], 1)
        self.assertTrue(self.api.list_claims(pid))

    def test_abort_closes_hanging_http(self) -> None:
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        from citehop.claims.llm import GenerationCancelled, OllamaLLM, abort_generation

        started = threading.Event()
        release = threading.Event()

        class Hang(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                started.set()
                release.wait(30)

            def log_message(self, *_args: object) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Hang)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host = f"http://127.0.0.1:{server.server_address[1]}"
        llm = OllamaLLM(model="dummy")
        llm.host = host
        caught: list[BaseException] = []

        def call() -> None:
            try:
                llm.complete("extract nothing")
            except BaseException as exc:  # noqa: BLE001
                caught.append(exc)

        client = threading.Thread(target=call)
        client.start()
        self.assertTrue(started.wait(5), "server never saw POST")
        abort_generation()
        client.join(5)
        release.set()
        server.shutdown()
        server.server_close()
        self.assertFalse(client.is_alive())
        self.assertTrue(caught)
        self.assertIsInstance(caught[0], GenerationCancelled)

    def test_abort_sends_freetoken_scheduler_uid(self) -> None:
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        from citehop.claims.llm import (
            FreeTokenLLM,
            GenerationCancelled,
            abort_generation,
            clear_generation_abort,
            _GATE,
        )

        clear_generation_abort()
        started = threading.Event()
        release = threading.Event()
        aborted = threading.Event()
        seen: list[int] = []

        class FirstChunkThenHang(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                payload = (
                    b'data: {"id": "chatcmpl-42", "choices": '
                    b'[{"delta": {"role": "assistant", "content": ""}, "index": 0}]}\n\n'
                )
                self.wfile.write(f"{len(payload):X}\r\n".encode() + payload + b"\r\n")
                self.wfile.flush()
                started.set()
                release.wait(30)

            def log_message(self, *_args: object) -> None:
                return

        def record(uid: int) -> None:
            seen.append(uid)
            aborted.set()

        server = ThreadingHTTPServer(("127.0.0.1", 0), FirstChunkThenHang)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host = f"http://127.0.0.1:{server.server_address[1]}"
        caught: list[BaseException] = []

        def call() -> None:
            try:
                llm = FreeTokenLLM("dummy")
                llm.host = host
                llm.complete("extract nothing")
            except BaseException as exc:  # noqa: BLE001
                caught.append(exc)

        with patch("citehop.claims.llm._send_freetoken_abort", side_effect=record):
            client = threading.Thread(target=call)
            client.start()
            self.assertTrue(started.wait(5), "server never saw POST")
            deadline = time.monotonic() + 3
            uid = None
            while time.monotonic() < deadline:
                with _GATE._lock:
                    uid = _GATE._ft_uid
                if uid == 42:
                    break
                time.sleep(0.05)
            self.assertEqual(uid, 42, f"client never parsed chatcmpl uid; caught={caught}")
            abort_generation()
            self.assertTrue(aborted.wait(2), "scheduler abort was not sent")
            client.join(5)
        release.set()
        server.shutdown()
        server.server_close()
        self.assertFalse(client.is_alive())
        self.assertEqual(seen, [42])
        self.assertTrue(caught)
        self.assertIsInstance(caught[0], GenerationCancelled)

    def test_abort_uses_persisted_freetoken_uid_after_quit(self) -> None:
        from citehop.claims.llm import (
            abort_generation,
            clear_generation_abort,
            _store_ft_uid,
        )

        clear_generation_abort()
        seen: list[int] = []
        _store_ft_uid(99)
        with patch("citehop.claims.llm._send_freetoken_abort", side_effect=seen.append):
            abort_generation()
        self.assertEqual(seen, [99])
        clear_generation_abort()

    def test_malformed_output_fails_one_paper_not_the_run(self) -> None:
        c1 = self.root / "c-junk"
        _write_corpus(c1, "paper-aaa", "A", RECIPE_TEXT)
        fid = file_id("paper-bbb")
        (c1 / "text" / f"{fid}.txt").write_text(RECIPE_TEXT, encoding="utf-8")
        manifest = Manifest(c1 / "manifest.db")
        try:
            manifest.upsert_paper(
                {
                    "canonical_id": "paper-bbb",
                    "file_id": fid,
                    "status": "fetched",
                    "relation_to_seed": "forward_citation",
                    "title": "B",
                    "abstract": RECIPE_TEXT[:80],
                    "full_text_available": 1,
                    "metadata": {},
                }
            )
        finally:
            manifest.close()
        proj = self.api.create_project("Junk", c1, template_id="recipe_claims")
        junk = JunkThenFixtureLLM()
        with patch("citehop.claims.api._ready_llm", return_value=junk):
            status = self.api.start_run(proj["project_id"])
            while status["status"] == "running":
                status = self.api.process_available(proj["project_id"], max_papers=1)
        self.assertEqual(status["status"], "completed")
        self.assertGreaterEqual(status["papers_done"], 1)
        self.assertGreaterEqual(status["papers_skipped"], 1)
        store = ClaimStore(self.api.projects.db_path(proj["project_id"]))
        try:
            run_id = status["run_id"]
            rows = store.conn.execute(
                "SELECT paper_canonical_id, status, error FROM run_papers WHERE run_id=?",
                (run_id,),
            ).fetchall()
            statuses = {r["paper_canonical_id"]: r["status"] for r in rows}
            self.assertEqual(set(statuses), {"paper-aaa", "paper-bbb"})
            self.assertEqual(sorted(statuses.values()), ["done", "error"])
            err = [r for r in rows if r["status"] == "error"][0]
            self.assertIn("JSON", err["error"] or "")
        finally:
            store.close()

    def test_backend_unavailable_pauses_and_keeps_paper_pending(self) -> None:
        corpus = _write_corpus(self.root / "c-die", "p-die", "Stew", RECIPE_TEXT)
        proj = self.api.create_project("Die", corpus, template_id="recipe_claims")
        self.api.start_run(proj["project_id"])
        with patch("citehop.claims.api._ready_llm", return_value=DieLLM()):
            status = self.api.process_available(proj["project_id"], max_papers=1)
        self.assertEqual(status["status"], "paused")
        self.assertIn("Ollama is not reachable", status["error"] or "")
        self.assertEqual(status["papers_done"], 0)
        self.assertEqual(status["papers_pending"], 1)
        store = ClaimStore(self.api.projects.db_path(proj["project_id"]))
        try:
            row = store.conn.execute(
                "SELECT status FROM run_papers WHERE run_id=?",
                (status["run_id"],),
            ).fetchone()
            self.assertEqual(row["status"], "pending")
        finally:
            store.close()
        with patch("citehop.claims.api._ready_llm", return_value=GroundedFixtureLLM()):
            resumed = self.api.resume_run(proj["project_id"])
            self.assertEqual(resumed["status"], "running")
            while resumed["status"] == "running":
                resumed = self.api.process_available(proj["project_id"], max_papers=1)
        self.assertEqual(resumed["status"], "completed")
        self.assertEqual(resumed["papers_done"], 1)

    def test_complete_paper_is_atomic_on_insert_failure(self) -> None:
        db = self.root / "atomic.db"
        store = ClaimStore(db)
        try:
            run_id = store.create_run(
                "p",
                [{"canonical_id": "paper-1", "file_id": "f1"}],
                token_budget=1000,
            )
            claimed = store.claim_next_paper(run_id)
            self.assertEqual(claimed["paper_canonical_id"], "paper-1")
            self.assertEqual(store.paper_status(run_id, "paper-1"), "extracting")
            bad = {
                "claim_id": "c1",
                "project_id": "p",
                "run_id": run_id,
                "paper_canonical_id": "paper-1",
                "claim_type": "t",
                "claim_text": "x",
                "structured_fields": {},
                "quoted_source_span": "x",
                "source_char_offset": ["not-an-int", 1],
                "confidence_self_reported": "medium",
                "present_in_pass_a": True,
                "present_in_pass_b": True,
                "agreement": "match",
            }
            with self.assertRaises((TypeError, ValueError)):
                store.complete_paper(run_id, "paper-1", claims=[bad], tokens_used=3, add_tokens=3)
            self.assertEqual(store.paper_status(run_id, "paper-1"), "extracting")
            n = store.conn.execute("SELECT COUNT(*) AS n FROM claims").fetchone()["n"]
            self.assertEqual(n, 0)
            self.assertEqual(int(store.get_run(run_id)["tokens_used"]), 0)
            n = store.requeue_extracting(run_id)
            self.assertEqual(n, 1)
            self.assertEqual(store.paper_status(run_id, "paper-1"), "pending")
        finally:
            store.close()

    def test_resume_requeues_extracting_after_simulated_crash(self) -> None:
        corpus = _write_corpus(self.root / "c-crash", "p-crash", "Stew", RECIPE_TEXT)
        proj = self.api.create_project("Crash", corpus, template_id="recipe_claims")
        started = self.api.start_run(proj["project_id"])
        store = ClaimStore(self.api.projects.db_path(proj["project_id"]))
        try:
            store.conn.execute(
                "UPDATE run_papers SET status='extracting', updated_at=? WHERE run_id=?",
                (utcnow(), started["run_id"]),
            )
        finally:
            store.close()
        stuck = self.api.process_available(proj["project_id"], max_papers=1)
        self.assertEqual(stuck["status"], "running")
        self.assertGreaterEqual(stuck.get("papers_extracting") or 0, 1)
        resumed = self.api.resume_run(proj["project_id"])
        self.assertEqual(resumed["status"], "running")
        self.assertEqual(resumed["papers_pending"], 1)
        done = resumed
        while done["status"] == "running":
            done = self.api.process_available(proj["project_id"], max_papers=1)
        self.assertEqual(done["status"], "completed")
        self.assertEqual(done["papers_done"], 1)

    def test_stale_extracting_lease_requeues(self) -> None:
        db = self.root / "lease.db"
        store = ClaimStore(db)
        try:
            run_id = store.create_run(
                "p",
                [{"canonical_id": "paper-1", "file_id": "f1"}],
                token_budget=1000,
            )
            old = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
            store.conn.execute(
                "UPDATE run_papers SET status='extracting', updated_at=? WHERE run_id=?",
                (old, run_id),
            )
            n = store.requeue_stale_extracting(run_id, lease_seconds=5)
            self.assertEqual(n, 1)
            self.assertEqual(store.paper_status(run_id, "paper-1"), "pending")
        finally:
            store.close()

    def test_two_workers_same_project_do_not_duplicate_a_paper(self) -> None:
        cdir = self.root / "c-race"
        _write_corpus(cdir, "paper-aaa", "A", RECIPE_TEXT)
        fid = file_id("paper-bbb")
        (cdir / "text" / f"{fid}.txt").write_text(RECIPE_TEXT, encoding="utf-8")
        manifest = Manifest(cdir / "manifest.db")
        try:
            manifest.upsert_paper(
                {
                    "canonical_id": "paper-bbb",
                    "file_id": fid,
                    "status": "fetched",
                    "relation_to_seed": "forward_citation",
                    "title": "B",
                    "abstract": RECIPE_TEXT[:80],
                    "full_text_available": 1,
                    "metadata": {},
                }
            )
        finally:
            manifest.close()
        proj = self.api.create_project("Race", cdir, template_id="recipe_claims")
        self.api.start_run(proj["project_id"])
        errors: list[BaseException] = []

        def worker() -> None:
            api = ClaimsAPI(projects_root=self.root / "projects")
            try:
                status = api.run_status(proj["project_id"])
                while status["status"] == "running":
                    status = api.process_available(proj["project_id"], max_papers=1)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertFalse(errors)
        store = ClaimStore(self.api.projects.db_path(proj["project_id"]))
        try:
            run = store.latest_run(proj["project_id"])
            rows = store.conn.execute(
                "SELECT paper_canonical_id, status FROM run_papers WHERE run_id=?",
                (run["run_id"],),
            ).fetchall()
            self.assertEqual({r["status"] for r in rows}, {"done"})
            counts = {
                r["paper_canonical_id"]: r["n"]
                for r in store.conn.execute(
                    "SELECT paper_canonical_id, COUNT(*) AS n FROM claims "
                    "WHERE run_id=? GROUP BY paper_canonical_id",
                    (run["run_id"],),
                )
            }
            self.assertEqual(set(counts), {"paper-aaa", "paper-bbb"})
            solo = next(iter(counts.values()))
            self.assertTrue(all(n == solo for n in counts.values()))
        finally:
            store.close()

    def test_two_projects_concurrent_do_not_share_token_counts(self) -> None:
        ca = _write_corpus(self.root / "c-p1", "p1", "A", RECIPE_TEXT)
        cb = _write_corpus(self.root / "c-p2", "p2", "B", QUANT_TEXT)
        pa = self.api.create_project("One", ca, template_id="recipe_claims")
        pb = self.api.create_project("Two", cb, template_id="quantitative_claims")
        errors: list[BaseException] = []

        def run_proj(pid: str) -> None:
            api = ClaimsAPI(projects_root=self.root / "projects")
            try:
                status = api.start_run(pid)
                while status["status"] == "running":
                    status = api.process_available(pid, max_papers=1)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [
            threading.Thread(target=run_proj, args=(pa["project_id"],)),
            threading.Thread(target=run_proj, args=(pb["project_id"],)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertFalse(errors)
        sa = self.api.run_status(pa["project_id"])
        sb = self.api.run_status(pb["project_id"])
        self.assertEqual(sa["status"], "completed")
        self.assertEqual(sb["status"], "completed")
        self.assertNotEqual(sa["run_id"], sb["run_id"])
        a_ids = {c["claim_id"] for c in self.api.list_claims(pa["project_id"])}
        b_ids = {c["claim_id"] for c in self.api.list_claims(pb["project_id"])}
        self.assertTrue(a_ids and b_ids)
        self.assertTrue(a_ids.isdisjoint(b_ids))

    def test_review_persists_and_rejects_string_in_number_field(self) -> None:
        corpus = _write_corpus(self.root / "c-rev", "p-rev", "Stew", RECIPE_TEXT)
        proj = self.api.create_project("Review", corpus, template_id="recipe_claims")
        _run_until_idle(self.api, proj["project_id"])
        claims = self.api.list_claims(proj["project_id"])
        timed = next(c for c in claims if c["claim_type"] == "cooking_time_estimate")
        confirmed = self.api.review_claim(proj["project_id"], timed["claim_id"], "confirm")
        self.assertEqual(confirmed["verification_status"], "human_confirmed")
        api2 = ClaimsAPI(projects_root=self.root / "projects")
        again = api2.get_claim(proj["project_id"], timed["claim_id"])
        self.assertEqual(again["verification_status"], "human_confirmed")
        fields = dict(timed["structured_fields"])
        fields["minutes"] = "not a number"
        with self.assertRaisesRegex(SchemaError, "must be a number"):
            self.api.review_claim(
                proj["project_id"],
                timed["claim_id"],
                "edit",
                edit={"claim_text": timed["claim_text"], "structured_fields": fields},
            )
        fields["minutes"] = 12.0
        edited = self.api.review_claim(
            proj["project_id"],
            timed["claim_id"],
            "edit",
            edit={"claim_text": "human paraphrase", "structured_fields": fields},
        )
        self.assertEqual(edited["verification_status"], "human_edited")
        self.assertEqual(edited["claim_text"], "human paraphrase")
        self.assertEqual(edited["human_edit"]["original"]["claim_text"], timed["claim_text"])
        reloaded = api2.get_claim(proj["project_id"], timed["claim_id"])
        self.assertEqual(reloaded["human_edit"]["edited"]["claim_text"], "human paraphrase")

    def test_list_claims_empty_filter_is_empty_list(self) -> None:
        corpus = _write_corpus(self.root / "c-emptyf", "p-ef", "Stew", RECIPE_TEXT)
        proj = self.api.create_project("Empty filter", corpus, template_id="recipe_claims")
        _run_until_idle(self.api, proj["project_id"])
        none = self.api.list_claims(proj["project_id"], agreement="disagreement")
        self.assertEqual(none, [])

    def test_short_abstract_only_and_long_text_extract(self) -> None:
        short_dir = self.root / "c-short"
        short_dir.mkdir()
        (short_dir / "text").mkdir()
        manifest = Manifest(short_dir / "manifest.db")
        try:
            manifest.upsert_paper(
                {
                    "canonical_id": "abs-only",
                    "file_id": file_id("abs-only"),
                    "status": "fetched",
                    "relation_to_seed": "seed",
                    "title": "Abstract only",
                    "abstract": RECIPE_TEXT,
                    "full_text_available": 0,
                    "metadata": {},
                }
            )
        finally:
            manifest.close()
        proj = self.api.create_project("Short", short_dir, template_id="recipe_claims")
        st = _run_until_idle(self.api, proj["project_id"])
        self.assertEqual(st["status"], "completed")
        self.assertGreaterEqual(st["papers_done"], 1)
        self.assertTrue(self.api.list_claims(proj["project_id"]))

        long_text = RECIPE_TEXT + (" padding sentence.\n" * 8000)
        self.assertGreater(len(long_text), 60_000)
        long_dir = _write_corpus(self.root / "c-long", "p-long", "Long", long_text)
        proj2 = self.api.create_project("Long", long_dir, template_id="recipe_claims")
        st2 = _run_until_idle(self.api, proj2["project_id"])
        self.assertEqual(st2["status"], "completed")
        claims = self.api.list_claims(proj2["project_id"])
        self.assertTrue(claims)
        src = self.api.paper_source(proj2["project_id"], "p-long")
        for claim in claims:
            start, end = claim["source_char_offset"]
            self.assertLessEqual(end, len(src["text"]))
            self.assertEqual(src["text"][start:end], claim["quoted_source_span"])

    def test_clamp_span_out_of_range(self) -> None:
        s, e, oob = clamp_span("hello", 0, 400)
        self.assertTrue(oob)
        self.assertEqual((s, e), (0, 5))
        s, e, oob = clamp_span("hello", 2, 4)
        self.assertFalse(oob)
        self.assertEqual((s, e), (2, 4))
        s, e, oob = clamp_span("hello", 9, 3)
        self.assertTrue(oob)

    def test_coerce_fields_strict_rejects_string_number(self) -> None:
        defs = [{"key": "n", "type": "number"}]
        self.assertEqual(coerce_fields(defs, {"n": "3.5"})["n"], 3.5)
        with self.assertRaisesRegex(SchemaError, "must be a number"):
            coerce_fields(defs, {"n": "3.5"}, strict=True)
        self.assertEqual(coerce_fields(defs, {"n": 3.5}, strict=True)["n"], 3.5)

    def test_locate_collapsed_whitespace(self) -> None:
        from citehop.claims.locate import locate_span

        text = "hello   world\nnext"
        found = locate_span(text, "hello world")
        self.assertIsNotNone(found)
        start, end = found
        self.assertIn("hello", text[start:end])
        self.assertIn("world", text[start:end])

    def test_paper_source_includes_pdf_path_when_raw_pdf_exists(self) -> None:
        import pymupdf

        cdir = _write_corpus(self.root / "c-pdfsrc", "p-pdfsrc", "Has PDF", RECIPE_TEXT)
        fid = file_id("p-pdfsrc")
        (cdir / "raw").mkdir(exist_ok=True)
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 72), "body")
        doc.save(cdir / "raw" / f"{fid}.pdf")
        doc.close()
        proj = self.api.create_project("PdfSrc", cdir, template_id="recipe_claims")
        src = self.api.paper_source(proj["project_id"], "p-pdfsrc")
        self.assertTrue(src["pdf_path"])
        self.assertTrue(Path(src["pdf_path"]).is_file())
        src_no = self.api.paper_source(
            self.api.create_project(
                "NoPdf",
                _write_corpus(self.root / "c-nopdf", "p-nopdf", "No PDF", RECIPE_TEXT),
                template_id="recipe_claims",
            )["project_id"],
            "p-nopdf",
        )
        self.assertIsNone(src_no["pdf_path"])

    def test_backend_dies_after_first_paper_keeps_progress(self) -> None:
        cdir = self.root / "c-mid"
        _write_corpus(cdir, "paper-aaa", "A", RECIPE_TEXT)
        fid = file_id("paper-bbb")
        (cdir / "text" / f"{fid}.txt").write_text(RECIPE_TEXT, encoding="utf-8")
        manifest = Manifest(cdir / "manifest.db")
        try:
            manifest.upsert_paper(
                {
                    "canonical_id": "paper-bbb",
                    "file_id": fid,
                    "status": "fetched",
                    "relation_to_seed": "forward_citation",
                    "title": "B",
                    "abstract": RECIPE_TEXT[:80],
                    "full_text_available": 1,
                    "metadata": {},
                }
            )
        finally:
            manifest.close()
        proj = self.api.create_project("Mid die", cdir, template_id="recipe_claims")
        self.api.start_run(proj["project_id"])

        class FixtureThenDie:
            name = "fixture-then-die"

            def __init__(self) -> None:
                self.n = 0
                self.inner = GroundedFixtureLLM()

            def complete(self, prompt: str) -> tuple[str, int]:
                self.n += 1
                if self.n <= 2:
                    return self.inner.complete(prompt)
                raise BackendUnavailable("Ollama is not reachable")

        with patch("citehop.claims.api._ready_llm", return_value=FixtureThenDie()):
            status = self.api.process_available(proj["project_id"], max_papers=1)
            self.assertEqual(status["papers_done"], 1)
            status = self.api.process_available(proj["project_id"], max_papers=1)
        self.assertEqual(status["status"], "paused")
        self.assertEqual(status["papers_done"], 1)
        self.assertEqual(status["papers_pending"], 1)
        self.assertTrue(self.api.list_claims(proj["project_id"]))

    def test_context_overflow_retries_with_shorter_clip(self) -> None:
        from citehop.claims.engine import extract_paper
        from citehop.claims.llm import ContextTooLong
        from citehop.claims.prompt import PAPER_BEGIN, PAPER_END, extract_marked_section

        long_text = RECIPE_TEXT + (" padding sentence.\n" * 400)
        self.assertGreater(len(long_text), 3000)

        class ShrinkThenFixture:
            name = "shrink"

            def __init__(self) -> None:
                self.seen: list[int] = []
                self.inner = GroundedFixtureLLM()

            def complete(self, prompt: str) -> tuple[str, int]:
                paper = extract_marked_section(prompt, PAPER_BEGIN, PAPER_END)
                self.seen.append(len(paper))
                if len(paper) > 2500:
                    raise ContextTooLong("Paper text exceeds this model's context window.")
                return self.inner.complete(prompt)

        llm = ShrinkThenFixture()
        claims, tokens = extract_paper(
            project_id="p",
            run_id="r",
            schema=ZERO_FIELD_SCHEMA,
            paper={"canonical_id": "p-long"},
            stored_text=long_text,
            llm=llm,
        )
        self.assertTrue(llm.seen)
        self.assertGreater(max(llm.seen), 2500)
        self.assertTrue(all(n <= 2500 for n in llm.seen[-2:]))
        self.assertTrue(claims)
        self.assertGreater(tokens, 0)

    def test_ollama_context_error_is_legible(self) -> None:
        from citehop.claims.llm import ContextTooLong, _ollama_client_error

        body = (
            '{"error":"{\\"error\\":{\\"code\\":400,\\"message\\":'
            '\\"request (7289 tokens) exceeds the available context size (4096 tokens)\\",'
            '\\"type\\":\\"exceed_context_size_error\\",\\"n_prompt_tokens\\":7289,\\"n_ctx\\":4096}}"}'
        )
        exc = _ollama_client_error(400, body)
        self.assertIsInstance(exc, ContextTooLong)
        self.assertIn("context window", str(exc))
        self.assertIn("7289", str(exc))
        self.assertIn("4096", str(exc))
        ui = Path(__file__).resolve().parents[1] / "src" / "citehop" / "ui" / "pages"
        projects = (ui / "projects.py").read_text(encoding="utf-8")
        schema = (ui / "schema.py").read_text(encoding="utf-8")
        review = (ui / "review.py").read_text(encoding="utf-8")
        corpus = (ui / "corpus.py").read_text(encoding="utf-8")
        extract = (ui / "extract.py").read_text(encoding="utf-8")
        self.assertIn("No projects yet", projects)
        self.assertIn("Select a project on the Projects tab to author a schema", schema)
        self.assertIn("No claims yet", review)
        self.assertIn("No claims with agreement=", review)
        self.assertIn("Go to paper", review)
        self.assertIn("No corpora yet", corpus)
        self.assertIn("Select a project on the Projects tab", extract)

    def test_retryable_backend_message_matches_loading_not_json(self) -> None:
        from citehop.claims.llm import retryable_backend_message

        self.assertTrue(
            retryable_backend_message(
                'FreeToken HTTP 503: {"error":"model is still loading"}'
            )
        )
        self.assertFalse(retryable_backend_message("Model output was not valid JSON"))

    def test_start_run_records_backend_model_and_schema(self) -> None:
        corpus = _write_corpus(self.root / "c-id", "p-id", "Stew", RECIPE_TEXT)
        proj = self.api.create_project("Identity", corpus, template_id="recipe_claims")
        status = self.api.start_run(proj["project_id"])
        self.assertEqual(status["llm_backend"], "fixture")
        self.assertEqual(status["llm_model"], "fixture")
        self.assertTrue(status["schema_id"])
        self.api.pause_run(proj["project_id"])

    def test_abstract_only_header_is_not_fed_to_the_model(self) -> None:
        from citehop.claims.engine import load_paper_text

        corpus = self.root / "c-abs"
        text = (
            "[abstract_only]\n\n"
            "An ingredient substitution is allowed: use margarine in place of butter. "
            "A cooking time estimate for the stew is 45 minutes at 180 degrees."
        )
        corpus = _write_corpus(corpus, "p-abs", "Abstract only", text)
        loaded = load_paper_text(
            corpus, {"canonical_id": "p-abs", "file_id": file_id("p-abs")}
        )
        self.assertIsNotNone(loaded)
        self.assertFalse(loaded.startswith("[abstract_only]"))
        proj = self.api.create_project("Abs header", corpus, template_id="recipe_claims")
        status = _run_until_idle(self.api, proj["project_id"])
        self.assertEqual(status["status"], "completed")
        claims = self.api.list_claims(proj["project_id"])
        self.assertTrue(claims)
        for claim in claims:
            self.assertNotIn("[abstract_only]", claim["quoted_source_span"])
            self.assertNotIn("[abstract_only]", claim["claim_text"])

    def test_loading_503_pauses_and_leaves_paper_pending(self) -> None:
        from citehop.claims.llm import LLMError

        corpus = _write_corpus(self.root / "c-503", "p-503", "Stew", RECIPE_TEXT)
        proj = self.api.create_project("Loading", corpus, template_id="recipe_claims")
        self.api.start_run(proj["project_id"])

        class LoadingLLM:
            name = "loading"

            def complete(self, prompt: str) -> tuple[str, int]:
                raise LLMError('FreeToken HTTP 503: {"error":"model is still loading"}')

        with patch("citehop.claims.api._ready_llm", return_value=LoadingLLM()):
            status = self.api.process_available(proj["project_id"], max_papers=1)
        self.assertEqual(status["status"], "paused")
        self.assertEqual(status["papers_done"], 0)
        self.assertEqual(status["papers_pending"], 1)
        store = ClaimStore(self.api.projects.db_path(proj["project_id"]))
        try:
            row = store.conn.execute(
                "SELECT status, error FROM run_papers WHERE run_id=?",
                (status["run_id"],),
            ).fetchone()
            self.assertEqual(row["status"], "pending")
            self.assertIsNone(row["error"])
        finally:
            store.close()

    def test_resume_requeues_retryable_errors_and_refreshes_counts(self) -> None:
        corpus = _write_corpus(self.root / "c-requeue", "p-requeue", "Stew", RECIPE_TEXT)
        proj = self.api.create_project("Requeue", corpus, template_id="recipe_claims")
        status = self.api.start_run(proj["project_id"])
        self.api.pause_run(proj["project_id"])
        store = ClaimStore(self.api.projects.db_path(proj["project_id"]))
        try:
            store.complete_paper(
                status["run_id"],
                "p-requeue",
                status="error",
                error='FreeToken HTTP 503: {"error":"model is still loading"}',
            )
        finally:
            store.close()
        after_error = self.api.run_status(proj["project_id"])
        self.assertEqual(after_error["papers_skipped"], 1)
        self.assertEqual(after_error["papers_pending"], 0)
        resumed = self.api.resume_run(proj["project_id"])
        self.assertEqual(resumed["status"], "running")
        self.assertEqual(resumed["papers_pending"], 1)
        self.assertEqual(resumed["papers_skipped"], 0)

    def test_resume_requeues_skipped_once_text_exists(self) -> None:
        from citehop.store import Manifest

        corpus = self.root / "c-skip"
        corpus.mkdir(parents=True)
        (corpus / "text").mkdir()
        fid = file_id("p-skip")
        manifest = Manifest(corpus / "manifest.db")
        try:
            manifest.upsert_paper(
                {
                    "canonical_id": "p-skip",
                    "file_id": fid,
                    "status": "pending",
                    "relation_to_seed": "seed",
                    "title": "Later text",
                    "abstract": "",
                    "full_text_available": 0,
                    "metadata": {},
                }
            )
        finally:
            manifest.close()
        proj = self.api.create_project("Skip then text", corpus, template_id="recipe_claims")
        status = _run_until_idle(self.api, proj["project_id"])
        self.assertEqual(status["status"], "completed")
        self.assertEqual(status["papers_skipped"], 1)
        (corpus / "text" / f"{fid}.txt").write_text(RECIPE_TEXT, encoding="utf-8")
        # Completed runs refuse resume; start a new run would re-extract everything.
        # Simulate the paused qc4hep case: mark skipped on a paused run.
        store = ClaimStore(self.api.projects.db_path(proj["project_id"]))
        try:
            store.set_run_status(status["run_id"], "paused")
            store.conn.execute(
                "UPDATE run_papers SET status='skipped_no_text', error=NULL WHERE run_id=?",
                (status["run_id"],),
            )
            store._refresh_run_paper_counts(status["run_id"])
        finally:
            store.close()
        resumed = self.api.resume_run(proj["project_id"])
        self.assertEqual(resumed["papers_pending"], 1)
        while resumed["status"] == "running":
            resumed = self.api.process_available(proj["project_id"], max_papers=1)
        self.assertEqual(resumed["status"], "completed")
        self.assertEqual(resumed["papers_done"], 1)
        self.assertTrue(self.api.list_claims(proj["project_id"]))

    def test_export_claims_json_is_a_complete_handoff(self) -> None:
        import json

        corpus = _write_corpus(self.root / "c-export", "p-export", "Stew", RECIPE_TEXT)
        proj = self.api.create_project("Export", corpus, template_id="recipe_claims")
        _run_until_idle(self.api, proj["project_id"])
        claims = self.api.list_claims(proj["project_id"])
        self.assertTrue(claims)
        reviewed = self.api.review_claim(proj["project_id"], claims[0]["claim_id"], "confirm")
        self.assertEqual(reviewed["verification_status"], "human_confirmed")
        dest = self.root / "handoff.json"
        result = self.api.export_claims(proj["project_id"], dest)
        self.assertEqual(result["claim_count"], len(claims))
        payload = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
        self.assertEqual(payload["format"], "citehop.claims.v1")
        self.assertIn("Citehop's job ends at this file", payload["handoff"])
        self.assertEqual(payload["run"]["llm_backend"], "fixture")
        self.assertTrue(payload["schema"]["schema_id"])
        exported_ids = {c["claim_id"] for c in payload["claims"]}
        self.assertEqual(exported_ids, {c["claim_id"] for c in claims})
        confirmed = [
            c for c in payload["claims"] if c["verification_status"] == "human_confirmed"
        ]
        self.assertEqual(len(confirmed), 1)
        self.assertEqual(confirmed[0]["claim_id"], claims[0]["claim_id"])
        self.assertIn("claim_text", payload["claims"][0])
        self.assertIn("quoted_source_span", payload["claims"][0])
        self.assertIn("structured_fields", payload["claims"][0])
        self.assertIn("agreement", payload["claims"][0])
        filtered = self.api.export_claims(
            proj["project_id"],
            self.root / "confirmed-only.json",
            verification_status="human_confirmed",
        )
        only = json.loads(Path(filtered["path"]).read_text(encoding="utf-8"))
        self.assertEqual(len(only["claims"]), 1)

    def test_each_claim_is_a_json_file_in_the_project_dir(self) -> None:
        import json

        from citehop.claims.files import claim_file_path, claims_dir

        corpus = _write_corpus(self.root / "c-files", "p-files", "Stew", RECIPE_TEXT)
        proj = self.api.create_project("Files", corpus, template_id="recipe_claims")
        pid = proj["project_id"]
        _run_until_idle(self.api, pid)
        claims = self.api.list_claims(pid)
        self.assertTrue(claims)
        folder = claims_dir(Path(proj["project_dir"]))
        self.assertTrue((folder / "index.json").is_file())
        for claim in claims:
            path = claim_file_path(Path(proj["project_dir"]), claim["claim_id"])
            self.assertTrue(path.is_file(), path)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["format"], "citehop.claim.v1")
            self.assertEqual(payload["claim_id"], claim["claim_id"])
            self.assertEqual(payload["claim_text"], claim["claim_text"])
            self.assertEqual(payload["paper_canonical_id"], claim["paper_canonical_id"])
        reviewed = self.api.review_claim(pid, claims[0]["claim_id"], "confirm")
        updated = json.loads(
            claim_file_path(Path(proj["project_dir"]), claims[0]["claim_id"]).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(reviewed["verification_status"], "human_confirmed")
        self.assertEqual(updated["verification_status"], "human_confirmed")
        status = self.api.run_status(pid)
        self.assertGreaterEqual(int(status.get("claims_count") or 0), len(claims))


if __name__ == "__main__":
    unittest.main()
