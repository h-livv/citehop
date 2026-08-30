"""Rate-limited HTTP client with exponential backoff on 429/5xx."""

from __future__ import annotations

import json
import random
import socket
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from .config import (
    BACKOFF_BASE_SEC,
    BACKOFF_CAP_SEC,
    DOWNLOAD_TIMEOUT_SEC,
    HOST_MIN_INTERVAL_SEC,
    HTTP_TIMEOUT_SEC,
    MAX_RETRIES,
    S2_API_KEY,
    USER_AGENT,
)
from .store import utcnow


class PermanentHttpError(Exception):
    def __init__(self, status_code: int, url: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.url = url


class FetchCancelled(Exception):
    """Pause/close aborted an in-flight corpus HTTP call. Caller must not mark the paper failed."""


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
        for name in ("raw", "_fp", "fp", "_connection", "connection", "sock", "_sock"):
            try:
                nxt = getattr(obj, name, None)
            except Exception:
                nxt = None
            if nxt is not None:
                stack.append(nxt)
    return found


def _close_http(obj: Any) -> None:
    if obj is None:
        return
    for sock in _walk_sockets(obj):
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            sock.close()
        except Exception:
            pass
    try:
        obj.close()
    except Exception:
        pass


class RateLimitedClient:
    def __init__(self, fetch_log: Path, cancelled: threading.Event | None = None):
        self.fetch_log = fetch_log
        fetch_log.parent.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "*/*",
            }
        )
        if S2_API_KEY:
            self.session.headers["x-api-key"] = S2_API_KEY
        self._last_request_at: dict[str, float] = {}
        self._cancelled = cancelled if cancelled is not None else threading.Event()
        self._active: requests.Response | None = None
        self._lock = threading.Lock()

    def abort(self) -> None:
        self._cancelled.set()
        with self._lock:
            resp = self._active
            self._active = None
            sess = self.session
        _close_http(resp)
        _close_http(sess)

    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def _raise_if_cancelled(self) -> None:
        if self._cancelled.is_set():
            raise FetchCancelled("Corpus fetch paused")

    def _sleep(self, seconds: float) -> None:
        deadline = time.monotonic() + max(0.0, seconds)
        while True:
            self._raise_if_cancelled()
            left = deadline - time.monotonic()
            if left <= 0:
                return
            time.sleep(min(0.05, left))

    def _wait_host(self, host: str) -> None:
        interval = HOST_MIN_INTERVAL_SEC.get(host, 0.5)
        last = self._last_request_at.get(host, 0.0)
        gap = interval - (time.monotonic() - last)
        if gap > 0:
            self._sleep(gap)

    def log(self, **event: Any) -> None:
        event.setdefault("timestamp", utcnow())
        with self.fetch_log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")

    def request(
        self,
        method: str,
        url: str,
        *,
        paper_id: str | None = None,
        action: str = "http",
        timeout: float | None = None,
        **kwargs: Any,
    ) -> requests.Response:
        host = urlparse(url).hostname or ""
        timeout = timeout if timeout is not None else HTTP_TIMEOUT_SEC
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            self._raise_if_cancelled()
            self._wait_host(host)
            self._last_request_at[host] = time.monotonic()
            try:
                resp = self.session.request(method, url, timeout=timeout, **kwargs)
                with self._lock:
                    self._active = resp
            except FetchCancelled:
                raise
            except Exception as exc:
                if self._cancelled.is_set():
                    raise FetchCancelled("Corpus fetch paused") from exc
                if not isinstance(exc, requests.RequestException):
                    raise
                last_error = exc
                self.log(
                    paper_id=paper_id,
                    action=action,
                    outcome="network_error",
                    url=url,
                    attempt=attempt + 1,
                    error=str(exc),
                )
                sleep = min(BACKOFF_CAP_SEC, BACKOFF_BASE_SEC ** (attempt + 1))
                sleep *= 0.5 + random.random()
                self._sleep(sleep)
                continue

            if resp.status_code == 429 or resp.status_code >= 500:
                retry_after = resp.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    sleep = min(BACKOFF_CAP_SEC, float(retry_after))
                else:
                    sleep = min(BACKOFF_CAP_SEC, BACKOFF_BASE_SEC ** (attempt + 1))
                    sleep *= 0.5 + random.random()
                self.log(
                    paper_id=paper_id,
                    action=action,
                    outcome="retry",
                    url=url,
                    status=resp.status_code,
                    attempt=attempt + 1,
                    sleep_sec=round(sleep, 2),
                )
                self._sleep(sleep)
                last_error = PermanentHttpError(resp.status_code, url, resp.text[:300])
                continue

            if resp.status_code in (404, 410, 400, 401, 403, 422):
                self.log(
                    paper_id=paper_id,
                    action=action,
                    outcome="http_error",
                    url=url,
                    status=resp.status_code,
                    attempt=attempt + 1,
                )
                raise PermanentHttpError(resp.status_code, url, resp.text[:500])

            self.log(
                paper_id=paper_id,
                action=action,
                outcome="ok",
                url=url,
                status=resp.status_code,
                attempt=attempt + 1,
                bytes=len(resp.content),
            )
            return resp

        self.log(
            paper_id=paper_id,
            action=action,
            outcome="failed_retries_exhausted",
            url=url,
            error=str(last_error),
        )
        raise last_error or RuntimeError(f"retries exhausted for {url}")

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        return self.request("GET", url, **kwargs)

    def get_json(self, url: str, **kwargs: Any) -> Any:
        resp = self.get(url, **kwargs)
        return resp.json()

    def download(self, url: str, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("timeout", DOWNLOAD_TIMEOUT_SEC)
        kwargs.setdefault("action", "download")
        return self.get(url, **kwargs)
