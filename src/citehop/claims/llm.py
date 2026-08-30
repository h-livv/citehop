"""LLM backends for extraction: local Ollama and FreeToken only. No cloud LLM APIs."""

from __future__ import annotations

import json
import os
import re
import select
import shutil
import socket
import struct
import subprocess
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Protocol

import requests

from citehop.claims.prompt import (
    PAPER_BEGIN,
    PAPER_END,
    PASS_BEGIN,
    PASS_END,
    SCHEMA_BEGIN,
    SCHEMA_END,
    extract_marked_section,
)

CONFIDENCE = ("high", "medium", "low")


class LLMError(RuntimeError):
    """No usable backend, or the backend returned unusable output."""


class BackendUnavailable(LLMError):
    """Transport/process down. Pause the run; do not mark the current paper failed."""


def retryable_backend_message(message: str) -> bool:
    """True when a paper 'error' is the engine not being ready, not bad JSON."""
    low = (message or "").lower()
    needles = (
        "model is still loading",
        "http 503",
        "http 429",
        "http 408",
        "not reachable",
        "read timed out",
        "connection refused",
        "connection reset",
        "generation cancelled",
    )
    return any(n in low for n in needles)


class ContextTooLong(LLMError):
    """Prompt exceeded the model's context window. Caller may retry with a shorter paper clip."""


class GenerationCancelled(Exception):
    """Pause aborted an in-flight generation. The paper stays pending; nothing is written."""


class _GenerationGate:
    """One in-flight HTTP generation. Pause RSTs the socket and aborts FreeToken by uid."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._abort = threading.Event()
        self._session: requests.Session | None = None
        self._response: requests.Response | None = None
        self._ft_uid: int | None = None

    def abort(self) -> None:
        self._abort.set()
        with self._lock:
            resp = self._response
            sess = self._session
            uid = self._ft_uid
            self._response = None
        if uid is None:
            uid = _load_ft_uid()
        # Must finish in this process. A daemon thread dies with Citehop and FreeToken
        # keeps prefilling/decoding; Desktop has no cancel for API requests.
        if uid is not None:
            _send_freetoken_abort(uid)
            _clear_stored_ft_uid()
            with self._lock:
                self._ft_uid = None
        for obj in (resp, sess):
            _close_http(obj)

    def clear(self) -> None:
        self._abort.clear()
        with self._lock:
            self._ft_uid = None
        _clear_stored_ft_uid()

    def aborted(self) -> bool:
        return self._abort.is_set()

    def bind(self, session: requests.Session, response: requests.Response | None) -> None:
        with self._lock:
            self._session = session
            self._response = response

    def unbind(self) -> None:
        with self._lock:
            self._session = None
            self._response = None

    def note_backend_id(self, chunk_id: str | None) -> None:
        """Record FreeToken's scheduler uid from the first SSE `id` (`chatcmpl-<uid>`)."""
        uid = _chatcmpl_uid(chunk_id)
        if uid is None:
            return
        with self._lock:
            self._ft_uid = uid
        _store_ft_uid(uid)


_GATE = _GenerationGate()


def _walk_sockets(root: Any) -> list[socket.socket]:
    found: list[socket.socket] = []
    seen: set[int] = set()
    stack = [root]
    while stack and len(seen) < 80:
        obj = stack.pop()
        if obj is None:
            continue
        oid = id(obj)
        if oid in seen:
            continue
        seen.add(oid)
        if isinstance(obj, socket.socket):
            found.append(obj)
            continue
        for name in (
            "raw",
            "_fp",
            "fp",
            "_connection",
            "connection",
            "sock",
            "_sock",
            "_original_response",
        ):
            try:
                nxt = getattr(obj, name, None)
            except Exception:
                nxt = None
            if nxt is not None:
                stack.append(nxt)
    return found


def _rst_socket(sock: socket.socket) -> None:
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    except Exception:
        pass
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except Exception:
        pass
    try:
        sock.close()
    except Exception:
        pass


def _close_http(obj: Any) -> None:
    """RST the in-flight socket so a blocked read returns."""
    if obj is None:
        return
    for sock in _walk_sockets(obj):
        _rst_socket(sock)
    raw = getattr(obj, "raw", None)
    if raw is not None:
        try:
            raw.close()
        except Exception:
            pass
    try:
        obj.close()
    except Exception:
        pass


def _chatcmpl_uid(chunk_id: str | None) -> int | None:
    if not chunk_id:
        return None
    text = str(chunk_id).strip()
    if not text.startswith("chatcmpl-"):
        return None
    rest = text[len("chatcmpl-") :]
    if rest.isdigit():
        return int(rest)
    return None


def _ft_uid_path() -> Path:
    env = (os.environ.get("CITEHOP_FT_ABORT_UID_PATH") or "").strip()
    if env:
        return Path(env)
    runtime = (os.environ.get("XDG_RUNTIME_DIR") or "").strip()
    base = Path(runtime) if runtime else Path("/tmp")
    return base / "citehop-freetoken-abort-uid"


def _store_ft_uid(uid: int) -> None:
    path = _ft_uid_path()
    try:
        path.write_text(str(uid), encoding="utf-8")
    except OSError:
        return


def _load_ft_uid() -> int | None:
    path = _ft_uid_path()
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if raw.isdigit():
        return int(raw)
    return None


def _clear_stored_ft_uid() -> None:
    try:
        _ft_uid_path().unlink(missing_ok=True)
    except OSError:
        return


_FT_ABORT_PY = """
import sys
import msgpack
import zmq

uid = int(sys.argv[1])
addr = sys.argv[2]
ctx = zmq.Context()
sock = ctx.socket(zmq.PUSH)
sock.setsockopt(zmq.LINGER, 400)
sock.connect(addr)
sock.send(msgpack.packb({"__type__": "AbortMsg", "uid": uid}, use_bin_type=True))
sock.close(linger=400)
ctx.term()
"""


def _freetoken_python() -> str | None:
    env = (os.environ.get("CITEHOP_FREETOKEN_PYTHON") or "").strip()
    if env and Path(env).is_file():
        return env
    candidates: list[Path] = [
        Path.home() / ".freetoken" / "venv" / "bin" / "python3",
        Path.home() / ".freetoken" / "venv" / "bin" / "python",
    ]
    ft = shutil.which("ft")
    if ft:
        candidates.insert(0, Path(ft).resolve().parent / "python3")
    for path in candidates:
        if path.is_file():
            return str(path)
    return None


def _freetoken_abort_addr(pid: int) -> str | None:
    for name in (f"freetoken_1.pid={pid}", f"freetoken_4.pid={pid}"):
        path = Path("/tmp") / name
        if path.exists():
            return f"ipc:///tmp/{name}"
    return None


def _send_freetoken_abort(uid: int) -> None:
    """Tell FreeToken's scheduler to drop this uid. Closing HTTP alone does not stop prefill."""
    try:
        from citehop.models import _freetoken_status
    except Exception:
        return
    try:
        pid = int((_freetoken_status() or {}).get("pid") or 0)
    except (TypeError, ValueError):
        return
    if pid <= 0:
        return
    addr = _freetoken_abort_addr(pid)
    python = _freetoken_python()
    if not addr or not python:
        return
    try:
        subprocess.run(
            [python, "-c", _FT_ABORT_PY, str(uid), addr],
            check=False,
            timeout=2,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return


def abort_generation() -> None:
    """Stop the current model request. Safe to call from the UI or another thread.

    Closes the HTTP socket and, for FreeToken, synchronously sends the scheduler
    AbortMsg for the in-flight uid. Disconnect and quitting Citehop do not stop
    FreeToken on their own; Desktop has no cancel for API generations.
    """
    _GATE.abort()


def clear_generation_abort() -> None:
    """Allow a new generation after pause/resume or a new run."""
    _GATE.clear()


def generation_aborted() -> bool:
    return _GATE.aborted()


def check_cancelled(should_stop: Callable[[], bool] | None = None) -> None:
    if _GATE.aborted() or (should_stop is not None and should_stop()):
        raise GenerationCancelled("Extraction paused")


def complete_prompt(
    llm: LLMBackend,
    prompt: str,
    should_stop: Callable[[], bool] | None = None,
) -> tuple[str, int]:
    """Call `llm.complete`, aborting immediately if pause was requested."""
    check_cancelled(should_stop)
    complete = llm.complete
    try:
        return complete(prompt, should_stop=should_stop)  # type: ignore[call-arg]
    except TypeError as exc:
        msg = str(exc)
        if "should_stop" not in msg and "unexpected keyword" not in msg:
            raise
        check_cancelled(should_stop)
        return complete(prompt)


@contextmanager
def _cancellable_request(
    method: str,
    url: str,
    *,
    should_stop: Callable[[], bool] | None,
    timeout: float | tuple[float, float],
    **kwargs: Any,
) -> Iterator[requests.Response]:
    check_cancelled(should_stop)
    session = requests.Session()
    halt = threading.Event()
    resp: requests.Response | None = None

    def watch() -> None:
        while not halt.wait(0.05):
            if _GATE.aborted() or (should_stop is not None and should_stop()):
                _GATE.abort()
                return

    _GATE.bind(session, None)
    watcher = threading.Thread(target=watch, name="citehop-gen-watch", daemon=True)
    watcher.start()
    try:
        check_cancelled(should_stop)
        resp = session.request(method, url, timeout=timeout, **kwargs)
        _GATE.bind(session, resp)
        check_cancelled(should_stop)
        yield resp
    except GenerationCancelled:
        raise
    except Exception as exc:
        if _GATE.aborted() or (should_stop is not None and should_stop()):
            raise GenerationCancelled("Extraction paused") from exc
        raise
    finally:
        halt.set()
        _GATE.unbind()
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass
        try:
            session.close()
        except Exception:
            pass


def _iter_lines_cancellable(
    resp: requests.Response,
    should_stop: Callable[[], bool] | None,
) -> Iterator[str]:
    raw = resp.raw
    buf = ""
    try:
        while True:
            check_cancelled(should_stop)
            socks = _walk_sockets(resp)
            if socks:
                ready, _, _ = select.select(socks, [], [], 0.05)
                if not ready:
                    continue
            try:
                read1 = getattr(raw, "read1", None)
                chunk = read1(1024) if callable(read1) else raw.read(1024)
            except Exception as exc:
                if _GATE.aborted() or (should_stop is not None and should_stop()):
                    raise GenerationCancelled("Extraction paused") from exc
                raise
            if not chunk:
                check_cancelled(should_stop)
                break
            if isinstance(chunk, bytes):
                chunk = chunk.decode("utf-8", "replace")
            buf += chunk
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                line = line.rstrip("\r")
                if line:
                    yield line
        check_cancelled(should_stop)
        if buf.strip():
            yield buf.rstrip("\r")
    except GenerationCancelled:
        raise
    except Exception as exc:
        if _GATE.aborted() or (should_stop is not None and should_stop()):
            raise GenerationCancelled("Extraction paused") from exc
        raise BackendUnavailable(
            f"Model stream dropped: {exc}. Extraction paused; resume when the backend is up."
        ) from exc


def _read_ollama_stream(
    resp: requests.Response,
    prompt: str,
    should_stop: Callable[[], bool] | None,
) -> tuple[str, int]:
    parts: list[str] = []
    tokens = 0
    for raw in _iter_lines_cancellable(resp, should_stop):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        if data.get("error"):
            blob = raw if isinstance(raw, str) else json.dumps(data)
            raise _ollama_client_error(400, blob)
        msg = ((data.get("message") or {}).get("content")) or data.get("response") or ""
        if msg:
            parts.append(str(msg))
        if data.get("done"):
            tokens = int(data.get("prompt_eval_count") or 0) + int(data.get("eval_count") or 0)
            break
    check_cancelled(should_stop)
    text = "".join(parts)
    if tokens <= 0:
        tokens = max(1, (len(prompt) + len(text)) // 4)
    return text, tokens


def _read_openai_stream(
    resp: requests.Response,
    prompt: str,
    should_stop: Callable[[], bool] | None,
) -> tuple[str, int]:
    parts: list[str] = []
    tokens = 0
    for raw in _iter_lines_cancellable(resp, should_stop):
        line = raw.strip()
        if line.startswith("data:"):
            line = line[5:].strip()
        if not line or line == "[DONE]":
            if line == "[DONE]":
                break
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        if data.get("error"):
            raise LLMError(str(data.get("error"))[:400])
        _GATE.note_backend_id(data.get("id") if isinstance(data.get("id"), str) else None)
        choice = (data.get("choices") or [{}])[0]
        delta = choice.get("delta") if isinstance(choice, dict) else {}
        msg = choice.get("message") if isinstance(choice, dict) else {}
        bit = ""
        if isinstance(delta, dict):
            bit = delta.get("content") or ""
        if not bit and isinstance(msg, dict):
            bit = msg.get("content") or ""
        if bit:
            parts.append(str(bit))
        usage = data.get("usage") or {}
        if isinstance(usage, dict) and usage.get("total_tokens"):
            tokens = int(usage["total_tokens"])
    check_cancelled(should_stop)
    text = "".join(parts)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S | re.I).strip()
    if tokens <= 0:
        tokens = max(1, (len(prompt) + len(text)) // 4)
    return text, tokens


class LLMBackend(Protocol):
    name: str

    def complete(
        self, prompt: str, should_stop: Callable[[], bool] | None = None
    ) -> tuple[str, int]:
        """Return (response_text, token_count_estimate)."""


_LOCAL_LLM_ENV = frozenset({"", "ollama", "freetoken", "fixture", "grounded", "test"})


def llm_env_choice() -> str:
    return (os.environ.get("CITEHOP_LLM") or "").strip().lower()


def reject_nonlocal_llm(choice: str | None = None) -> None:
    """Cloud LLM APIs are not a backend. Extraction is Ollama or FreeToken only."""
    value = llm_env_choice() if choice is None else choice
    if value in _LOCAL_LLM_ENV:
        return
    raise LLMError(
        f"CITEHOP_LLM={value!r} is not supported. Extraction uses only local "
        "Ollama or FreeToken models (pick one on the Models tab)."
    )


def select_backend() -> LLMBackend:
    choice = llm_env_choice()
    reject_nonlocal_llm(choice)
    if choice in ("fixture", "grounded", "test"):
        return GroundedFixtureLLM()

    from citehop.models import load_settings

    settings = load_settings()
    backend = (settings or {}).get("backend") if choice in ("", None) else choice
    if backend and backend not in ("freetoken", "ollama"):
        raise LLMError(
            f"Backend {backend!r} is not supported. Extraction uses only local "
            "Ollama or FreeToken models (pick one on the Models tab)."
        )
    if backend == "freetoken":
        if not settings or settings.get("backend") != "freetoken":
            raise LLMError("Select a FreeToken model on the Models tab.")
        return FreeTokenLLM(str(settings["model"]))
    if backend == "ollama" or (choice == "ollama"):
        model = os.environ.get("CITEHOP_OLLAMA_MODEL") or (settings or {}).get("model")
        if not model:
            raise LLMError("Select an Ollama or FreeToken model on the Models tab.")
        num_gpu = None
        if settings and settings.get("backend") == "ollama" and settings.get("model") == model:
            num_gpu = settings.get("num_gpu")
        ollama = OllamaLLM(model=str(model), num_gpu=num_gpu)
        if not ollama.available():
            raise BackendUnavailable(
                f"Ollama is not reachable at {ollama.host}. "
                "Start Ollama, or pick a FreeToken model on the Models tab."
            )
        return ollama
    raise LLMError("Select a model on the Models tab before extracting.")


class GroundedFixtureLLM:
    """Deterministic extractor used in tests. Reads schema + paper from the prompt."""

    name = "fixture"

    def complete(
        self, prompt: str, should_stop: Callable[[], bool] | None = None
    ) -> tuple[str, int]:
        check_cancelled(should_stop)
        schema = json.loads(extract_marked_section(prompt, SCHEMA_BEGIN, SCHEMA_END))
        paper = extract_marked_section(prompt, PAPER_BEGIN, PAPER_END)
        pass_id = extract_marked_section(prompt, PASS_BEGIN, PASS_END)
        types = schema.get("claim_types") or []
        if not types:
            raise LLMError("Fixture LLM: SCHEMA_JSON has no claim_types")
        claims = _fixture_claims(types, paper)
        if pass_id == "B" and len(claims) > 1:
            claims = claims[:-1]
        elif pass_id == "B" and claims:
            # Slightly shorter quote so alignment still pairs by proximity.
            q = claims[0].get("quoted_source_span") or ""
            if len(q) > 24:
                claims[0] = dict(claims[0])
                claims[0]["quoted_source_span"] = q[:-8].rstrip()
        payload = json.dumps({"claims": claims}, ensure_ascii=False)
        tokens = max(1, (len(prompt) + len(payload)) // 4)
        return payload, tokens


def _fixture_claims(types: list[dict[str, Any]], paper: str) -> list[dict[str, Any]]:
    sentences = _sentences(paper)
    out: list[dict[str, Any]] = []
    used: set[int] = set()
    for ctype in types:
        type_tokens = _tokens(
            " ".join(
                [
                    ctype.get("type_id") or "",
                    ctype.get("display_name") or "",
                    ctype.get("description") or "",
                ]
            )
        )
        best: tuple[int, int] | None = None
        for i, sent in enumerate(sentences):
            if i in used:
                continue
            overlap = len(type_tokens & _tokens(sent))
            if overlap == 0:
                continue
            if best is None or overlap > best[1]:
                best = (i, overlap)
        if best is None:
            continue
        i, _ = best
        used.add(i)
        sent = sentences[i]
        fields = _fill_fields(ctype.get("structured_fields") or [], sent)
        out.append(
            {
                "claim_type": ctype["type_id"],
                "claim_text": sent.strip()[:240],
                "structured_fields": fields,
                "quoted_source_span": sent.strip(),
                "confidence_self_reported": "high",
            }
        )
    return out


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z][a-z]{2,}", text.lower().replace("_", " ")))


def _fill_fields(field_defs: list[dict[str, Any]], sentence: str) -> dict[str, Any]:
    numbers = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", sentence)]
    n_i = 0
    out: dict[str, Any] = {}
    for field in field_defs:
        key = field["key"]
        ftype = field["type"]
        if ftype == "number":
            out[key] = numbers[n_i] if n_i < len(numbers) else None
            n_i += 1
        elif ftype == "boolean":
            out[key] = bool(re.search(r"\b(true|yes)\b", sentence, re.I))
        elif ftype == "enum":
            values = field.get("enum_values") or []
            picked = None
            low = sentence.lower()
            for val in values:
                if val.lower() in low:
                    picked = val
                    break
            out[key] = picked or (values[0] if values else None)
        else:
            out[key] = sentence.strip()[:80]
    return out


class OllamaLLM:
    name = "ollama"

    def __init__(self, model: str | None = None, num_gpu: int | None = None) -> None:
        self.host = os.environ.get("CITEHOP_OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
        if "://" not in self.host:
            self.host = f"http://{self.host}"
        self.model = model or os.environ.get("CITEHOP_OLLAMA_MODEL")
        if not self.model:
            raise LLMError("Select an Ollama model on the Models tab.")
        self.num_gpu = num_gpu

    def available(self) -> bool:
        try:
            r = requests.get(f"{self.host}/api/tags", timeout=1.5)
            return r.status_code == 200
        except requests.RequestException:
            return False

    def complete(
        self, prompt: str, should_stop: Callable[[], bool] | None = None
    ) -> tuple[str, int]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
            "format": "json",
            "keep_alive": -1,
        }
        if self.num_gpu is not None:
            payload["options"] = {"num_gpu": int(self.num_gpu)}
        try:
            with _cancellable_request(
                "POST",
                f"{self.host}/api/chat",
                should_stop=should_stop,
                timeout=(10, 600),
                json=payload,
                stream=True,
            ) as r:
                if r.status_code >= 500 or r.status_code in (404, 408, 429):
                    raise BackendUnavailable(
                        f"Ollama HTTP {r.status_code} at {self.host}: {r.text[:400]}. "
                        "Extraction paused; resume when the backend is up."
                    )
                if r.status_code >= 400:
                    raise _ollama_client_error(r.status_code, r.text)
                return _read_ollama_stream(r, prompt, should_stop)
        except GenerationCancelled:
            raise
        except BackendUnavailable:
            raise
        except LLMError:
            raise
        except requests.RequestException as exc:
            raise BackendUnavailable(
                f"Ollama is not reachable at {self.host}: {exc}. "
                "Extraction paused; resume when the backend is up."
            ) from exc


class FreeTokenLLM:
    name = "freetoken"

    def __init__(self, model: str) -> None:
        from citehop.models import freetoken_engine

        self.model = model
        self.host = freetoken_engine()

    def complete(
        self, prompt: str, should_stop: Callable[[], bool] | None = None
    ) -> tuple[str, int]:
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
            "temperature": 0,
        }
        try:
            with _cancellable_request(
                "POST",
                f"{self.host}/v1/chat/completions",
                should_stop=should_stop,
                timeout=(10, 600),
                json=body,
                stream=True,
            ) as r:
                if r.status_code >= 500 or r.status_code in (404, 408, 429):
                    raise BackendUnavailable(
                        f"FreeToken HTTP {r.status_code}: {r.text[:400]}. "
                        "Extraction paused; resume when the backend is up."
                    )
                if r.status_code >= 400:
                    raise LLMError(f"FreeToken HTTP {r.status_code}: {r.text[:400]}")
                return _read_openai_stream(r, prompt, should_stop)
        except GenerationCancelled:
            raise
        except BackendUnavailable:
            raise
        except LLMError:
            raise
        except requests.RequestException as exc:
            raise BackendUnavailable(
                f"FreeToken is not reachable: {exc}. "
                "Load the model on the Models tab, then resume extraction."
            ) from exc


def _ollama_client_error(status: int, body: str) -> LLMError:
    blob = body
    try:
        data = json.loads(body)
        if isinstance(data, dict) and isinstance(data.get("error"), str):
            inner = json.loads(data["error"])
            data = inner
        err = data.get("error") if isinstance(data, dict) else None
        if isinstance(err, dict):
            msg = err.get("message") or body[:300]
            if err.get("type") == "exceed_context_size_error" or "context size" in str(msg).lower():
                needed = err.get("n_prompt_tokens")
                ctx = err.get("n_ctx")
                detail = ""
                if needed and ctx:
                    detail = f" Prompt used ~{needed} tokens; this model’s window is {ctx}."
                return ContextTooLong(
                    "Paper text exceeds this model's context window." + detail
                )
            blob = str(msg)
    except (json.JSONDecodeError, TypeError, AttributeError):
        pass
    if "exceed_context_size" in body or "exceeds the available context size" in body:
        return ContextTooLong("Paper text exceeds this model's context window.")
    return LLMError(f"Ollama HTTP {status}: {blob[:400]}")


def parse_claims_json(raw: str) -> list[dict[str, Any]]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise LLMError("Model output was not JSON") from None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise LLMError(f"Model output was not JSON: {exc}") from exc
    if isinstance(data, list):
        claims = data
    elif isinstance(data, dict):
        claims = data.get("claims")
        if claims is None:
            raise LLMError("JSON object has no 'claims' array")
    else:
        raise LLMError("JSON root must be an object or array")
    if not isinstance(claims, list):
        raise LLMError("'claims' must be a list")
    return claims
