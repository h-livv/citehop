"""Local extraction models: Ollama tags and FreeToken weights.

Ollama GPU offload uses Machina's saved max `num_gpu` (gpu-layers.json /
model-params.json). This module does not expose sampling parameters.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import requests

from citehop.config import CONFIG_DIR

OLLAMA_HOST = os.environ.get("CITEHOP_OLLAMA_HOST") or os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434"
if "://" not in OLLAMA_HOST:
    OLLAMA_HOST = f"http://{OLLAMA_HOST}"
OLLAMA_HOST = OLLAMA_HOST.rstrip("/")

MACHINA_CACHE_VERSION = 2
SETTINGS_PATH = CONFIG_DIR / "model.json"


def machina_config_dir() -> Path:
    raw = os.environ.get("MACHINA_CONFIG_DIR")
    return Path(raw).expanduser() if raw else Path.home() / ".config" / "machina"


def _settings_path() -> Path:
    override = os.environ.get("CITEHOP_MODEL_SETTINGS")
    return Path(override) if override else SETTINGS_PATH


def load_settings() -> dict[str, Any] | None:
    path = _settings_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not data.get("backend") or not data.get("model"):
        return None
    return data


def save_settings(settings: dict[str, Any]) -> dict[str, Any]:
    backend = settings.get("backend")
    model = (settings.get("model") or "").strip()
    if backend not in ("ollama", "freetoken") or not model:
        raise ValueError("Select an Ollama or FreeToken model")
    out = {
        "backend": backend,
        "model": model,
        "path": settings.get("path"),
        "num_gpu": settings.get("num_gpu"),
        "num_gpu_total": settings.get("num_gpu_total"),
    }
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return out


def settings_label(settings: dict[str, Any] | None) -> str:
    if not settings:
        return "No model selected"
    backend = settings.get("backend") or ""
    name = settings.get("model") or ""
    if backend == "ollama" and settings.get("num_gpu") is not None:
        total = settings.get("num_gpu_total")
        shown = f"{settings['num_gpu']}/{total}" if total else str(settings["num_gpu"])
        return f"ollama  {name}  ·  num_gpu {shown}"
    return f"{backend}  {name}"


def freetoken_desktop() -> dict[str, Any]:
    path = Path.home() / ".config" / "freetoken" / "desktop.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def freetoken_models_dir() -> Path:
    env = os.environ.get("CITEHOP_FREETOKEN_DIR")
    if env:
        return Path(env).expanduser()
    desktop = freetoken_desktop()
    raw = desktop.get("models_dir")
    if isinstance(raw, str) and raw.strip():
        return Path(raw)
    return Path(f"/run/media/{os.environ.get('USER', 'h-livv')}/Vault/freetoken")


def freetoken_daemon() -> str:
    env = os.environ.get("CITEHOP_FREETOKEN_DAEMON")
    if env:
        return env.rstrip("/")
    desktop = freetoken_desktop()
    port = desktop.get("daemon_port") or 1900
    host = desktop.get("serverHost") or "127.0.0.1"
    return f"http://{host}:{int(port)}"


def freetoken_engine() -> str:
    env = os.environ.get("CITEHOP_FREETOKEN_HOST")
    if env:
        return env.rstrip("/")
    desktop = freetoken_desktop()
    port = desktop.get("enginePort") or 1919
    host = desktop.get("serverHost") or "127.0.0.1"
    return f"http://{host}:{int(port)}"


def remembered_gpu_layers(name: str) -> tuple[int, int | None] | None:
    """Machina's max num_gpu for this Ollama tag, if cached."""
    cache = _read_json(machina_config_dir() / "gpu-layers.json")
    for key in _gpu_cache_keys(name):
        entry = cache.get(key)
        parsed = _parse_gpu_entry(entry)
        if parsed:
            return parsed
    params = _read_json(machina_config_dir() / "model-params.json")
    models = params.get("models") if isinstance(params.get("models"), dict) else {}
    for candidate in _param_keys(name):
        raw = models.get(candidate)
        if isinstance(raw, dict):
            layers = raw.get("num_gpu")
            if isinstance(layers, int) and layers > 0:
                return layers, None
    return None


def _gpu_cache_keys(name: str) -> list[str]:
    bare = name[:-7] if name.endswith(":latest") else name
    keys = [f"ollama:{name}", f"ollama:{bare}", name, bare]
    return list(dict.fromkeys(keys))


def _param_keys(name: str) -> list[str]:
    bare = name[:-7] if name.endswith(":latest") else name
    return list(dict.fromkeys([name, bare]))


def _parse_gpu_entry(entry: Any) -> tuple[int, int | None] | None:
    if not isinstance(entry, dict):
        return None
    layers = entry.get("layers")
    if not isinstance(layers, int) or layers <= 0:
        return None
    total = entry.get("total")
    total_i = total if isinstance(total, int) else None
    version = entry.get("v")
    if isinstance(version, int) and version >= MACHINA_CACHE_VERSION:
        return layers, total_i
    if total_i is not None and layers != total_i:
        return layers, total_i
    return None


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def list_models() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    rows.extend(_list_ollama())
    rows.extend(_list_freetoken())
    return rows


def _list_ollama() -> list[dict[str, Any]]:
    try:
        r = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=1.5)
    except requests.RequestException:
        return []
    if r.status_code >= 400:
        return []
    data = r.json() if r.content else {}
    loaded = _ollama_loaded_names()
    rows = []
    for item in data.get("models") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("model") or "")
        if not name:
            continue
        gpu = remembered_gpu_layers(name)
        rows.append(
            {
                "backend": "ollama",
                "model": name,
                "path": None,
                "size_b": item.get("size") if isinstance(item.get("size"), int) else None,
                "family": ((item.get("details") or {}) or {}).get("family"),
                "num_gpu": gpu[0] if gpu else None,
                "num_gpu_total": gpu[1] if gpu else None,
                "loaded": name in loaded or any(name in n or n in name for n in loaded),
            }
        )
    return rows


def _ollama_loaded_names() -> set[str]:
    try:
        r = requests.get(f"{OLLAMA_HOST}/api/ps", timeout=1.0)
    except requests.RequestException:
        return set()
    if r.status_code >= 400:
        return set()
    data = r.json() if r.content else {}
    names = set()
    for item in data.get("models") or []:
        if isinstance(item, dict):
            tag = str(item.get("name") or item.get("model") or "")
            if tag:
                names.add(tag)
    return names


def _list_freetoken() -> list[dict[str, Any]]:
    root = freetoken_models_dir()
    if not root.is_dir():
        return []
    status = _freetoken_status()
    active = ""
    if status.get("running") and status.get("model"):
        active = Path(str(status["model"])).name
    rows = []
    try:
        children = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return []
    for child in children:
        if not (child / "config.json").is_file():
            continue
        rows.append(
            {
                "backend": "freetoken",
                "model": child.name,
                "path": str(child),
                "size_b": _dir_size(child),
                "family": "freetoken",
                "num_gpu": None,
                "num_gpu_total": None,
                "loaded": child.name == active,
            }
        )
    return rows


def _dir_size(path: Path) -> int | None:
    total = 0
    try:
        for child in path.rglob("*"):
            if child.is_file():
                total += child.stat().st_size
    except OSError:
        return None
    return total or None


def ollama_reachable() -> bool:
    try:
        r = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=1.0)
        return r.status_code == 200
    except requests.RequestException:
        return False


def freetoken_daemon_reachable() -> bool:
    try:
        r = requests.get(f"{freetoken_daemon()}/health", timeout=1.0)
        return r.status_code == 200
    except requests.RequestException:
        return False


def _freetoken_status() -> dict[str, Any]:
    try:
        r = requests.get(f"{freetoken_daemon()}/engine/status", timeout=1.5)
    except requests.RequestException:
        return {}
    if r.status_code >= 400:
        return {}
    data = r.json() if r.content else {}
    return data if isinstance(data, dict) else {}


def select_model(row: dict[str, Any]) -> dict[str, Any]:
    """Persist the choice and apply Machina GPU layers for Ollama."""
    backend = row.get("backend")
    model = (row.get("model") or "").strip()
    path = row.get("path")
    settings: dict[str, Any] = {"backend": backend, "model": model, "path": path}
    if backend == "ollama":
        gpu = remembered_gpu_layers(model)
        if gpu:
            settings["num_gpu"] = gpu[0]
            settings["num_gpu_total"] = gpu[1]
        elif row.get("num_gpu"):
            settings["num_gpu"] = row["num_gpu"]
            settings["num_gpu_total"] = row.get("num_gpu_total")
    return save_settings(settings)


def prepare_extraction() -> dict[str, Any]:
    """Load the selected model into VRAM. Called from the extraction worker."""
    settings = load_settings()
    if not settings:
        raise RuntimeError("Select a model on the Models tab before extracting.")
    if settings["backend"] == "ollama":
        return load_ollama(settings["model"], settings.get("num_gpu"))
    if settings["backend"] == "freetoken":
        path = settings.get("path") or str(freetoken_models_dir() / settings["model"])
        return load_freetoken(path)
    raise RuntimeError(f"Unknown backend {settings['backend']!r}")


def load_ollama(name: str, num_gpu: int | None = None) -> dict[str, Any]:
    if not ollama_reachable():
        raise RuntimeError(f"Ollama is not reachable at {OLLAMA_HOST}")
    _stop_freetoken_quiet()
    if num_gpu is None:
        cached = remembered_gpu_layers(name)
        num_gpu = cached[0] if cached else None
    body: dict[str, Any] = {
        "model": name,
        "prompt": "",
        "keep_alive": -1,
        "stream": False,
    }
    if num_gpu is not None:
        body["options"] = {"num_gpu": int(num_gpu)}
    timeout = 600.0
    try:
        r = requests.post(f"{OLLAMA_HOST}/api/generate", json=body, timeout=timeout)
    except requests.RequestException as exc:
        raise RuntimeError(f"Ollama load failed: {exc}") from exc
    if r.status_code >= 400:
        raise RuntimeError(f"Ollama load HTTP {r.status_code}: {r.text[:300]}")
    extra = f" num_gpu={num_gpu}" if num_gpu is not None else " (Ollama auto offload; no Machina cache)"
    return {"ok": True, "backend": "ollama", "model": name, "message": f"Loaded {name}{extra}"}


def load_freetoken(model_path: str, timeout_s: float = 600.0) -> dict[str, Any]:
    if not freetoken_daemon_reachable():
        raise RuntimeError(
            f"FreeToken daemon is not reachable at {freetoken_daemon()}. "
            "Open FreeToken Desktop first."
        )
    _unload_ollama_quiet()
    path = str(Path(model_path).expanduser())
    status = _freetoken_status()
    running = bool(status.get("running") or status.get("starting"))
    current = str(status.get("model") or "")
    daemon = freetoken_daemon()
    engine_port = int(freetoken_desktop().get("enginePort") or 1919)
    body = {"model": path, "port": engine_port}
    try:
        if running and current and Path(current).resolve() == Path(path).resolve():
            _wait_freetoken_engine(timeout_s)
            return {"ok": True, "backend": "freetoken", "model": Path(path).name, "message": f"Engine already on {Path(path).name}"}
        if running:
            r = requests.post(f"{daemon}/engine/switch", json={**body, "force": True}, timeout=40)
        else:
            r = requests.post(f"{daemon}/engine/start", json=body, timeout=40)
    except requests.RequestException as exc:
        raise RuntimeError(f"FreeToken engine request failed: {exc}") from exc
    if r.status_code >= 400:
        raise RuntimeError(f"FreeToken HTTP {r.status_code}: {r.text[:300]}")
    _wait_freetoken_engine(timeout_s)
    return {"ok": True, "backend": "freetoken", "model": Path(path).name, "message": f"FreeToken engine started for {Path(path).name}"}


def _wait_freetoken_engine(timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    last = ""
    engine = freetoken_engine()
    while time.monotonic() < deadline:
        status = _freetoken_status()
        if status:
            last = json.dumps(status)
            if status.get("running"):
                try:
                    h = requests.get(f"{engine}/health", timeout=1.5)
                    if h.status_code == 200:
                        return
                except requests.RequestException:
                    pass
                try:
                    h = requests.get(f"{engine}/v1/models", timeout=1.5)
                    if h.status_code == 200:
                        return
                except requests.RequestException:
                    pass
            if status.get("lastExitCode") not in (None, 0, -15) and not status.get("starting"):
                raise RuntimeError(f"FreeToken engine exited: {status.get('lastExitReason') or last}")
        time.sleep(1.5)
    raise RuntimeError(f"FreeToken engine did not become ready in {int(timeout_s)}s. {last}")


def _stop_freetoken_quiet() -> None:
    try:
        requests.post(f"{freetoken_daemon()}/engine/stop", json={"force": True}, timeout=8)
    except requests.RequestException:
        return


def _unload_ollama_quiet() -> None:
    for name in _ollama_loaded_names():
        try:
            requests.post(
                f"{OLLAMA_HOST}/api/generate",
                json={"model": name, "prompt": "", "keep_alive": 0, "stream": False},
                timeout=8,
            )
        except requests.RequestException:
            continue
