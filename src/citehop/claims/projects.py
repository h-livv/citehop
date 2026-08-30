"""Project records: id, corpus link, schema path, extraction db. Display labels never branch."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from citehop.config import PROJECTS_DIR
from citehop.store import utcnow

from .schema import (
    SchemaError,
    check_schema_edit,
    clone_schema,
    empty_schema,
    load_schema_file,
    load_template,
    save_schema_file,
    validate_schema,
)

_SLUG = re.compile(r"[^a-z0-9]+")


class ProjectError(ValueError):
    pass


def slugify(name: str) -> str:
    s = _SLUG.sub("-", name.strip().lower()).strip("-")
    return s or "project"


class ProjectStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root else PROJECTS_DIR
        self.root.mkdir(parents=True, exist_ok=True)

    def project_dir(self, project_id: str) -> Path:
        return self.root / project_id

    def schema_path(self, project_id: str) -> Path:
        return self.project_dir(project_id) / "schema.json"

    def db_path(self, project_id: str) -> Path:
        return self.project_dir(project_id) / "extraction.db"

    def meta_path(self, project_id: str) -> Path:
        return self.project_dir(project_id) / "project.json"

    def list_projects(self) -> list[dict[str, Any]]:
        out = []
        if not self.root.is_dir():
            return out
        for path in sorted(self.root.iterdir()):
            if path.is_dir() and (path / "project.json").is_file():
                out.append(self.get_project(path.name))
        return out

    def get_project(self, project_id: str) -> dict[str, Any]:
        path = self.meta_path(project_id)
        if not path.is_file():
            raise ProjectError(f"Unknown project {project_id!r}")
        data = json.loads(path.read_text(encoding="utf-8"))
        data["project_dir"] = str(self.project_dir(project_id))
        data["schema_path"] = str(self.schema_path(project_id))
        data["extraction_db"] = str(self.db_path(project_id))
        return data

    def create_project(
        self,
        display_name: str,
        corpus_dir: str | Path,
        *,
        template_id: str | None = None,
        token_budget: int = 500_000,
        time_budget_seconds: int | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        name = (display_name or "").strip()
        if not name:
            raise ProjectError("display_name is required")
        corpus = Path(corpus_dir).expanduser().resolve()
        if not corpus.is_dir():
            raise ProjectError(f"Corpus directory not found: {corpus}")
        pid = project_id or slugify(name)
        dest = self.project_dir(pid)
        n = 2
        while dest.exists():
            pid = f"{slugify(name)}-{n}"
            dest = self.project_dir(pid)
            n += 1
        dest.mkdir(parents=True, exist_ok=True)
        if template_id:
            schema = clone_schema(load_template(template_id), f"{pid}-schema")
        else:
            schema = empty_schema(f"{pid}-schema")
        save_schema_file(self.schema_path(pid), schema)
        meta = {
            "project_id": pid,
            "display_name": name,
            "corpus_dir": str(corpus),
            "token_budget": int(token_budget),
            "time_budget_seconds": time_budget_seconds,
            "created_at": utcnow(),
        }
        self._write_meta(pid, meta)
        return self.get_project(pid)

    def update_project(self, project_id: str, **fields: Any) -> dict[str, Any]:
        meta = self.get_project(project_id)
        allowed = {"display_name", "corpus_dir", "token_budget", "time_budget_seconds"}
        for key, value in fields.items():
            if key not in allowed:
                raise ProjectError(f"Cannot update {key!r}")
            if key == "corpus_dir" and value:
                path = Path(value).expanduser().resolve()
                if not path.is_dir():
                    raise ProjectError(f"Corpus directory not found: {path}")
                value = str(path)
            meta[key] = value
        keep = {
            "project_id": meta["project_id"],
            "display_name": meta["display_name"],
            "corpus_dir": meta["corpus_dir"],
            "token_budget": meta["token_budget"],
            "time_budget_seconds": meta.get("time_budget_seconds"),
            "created_at": meta.get("created_at"),
        }
        self._write_meta(project_id, keep)
        return self.get_project(project_id)

    def load_schema(self, project_id: str) -> dict[str, Any]:
        return load_schema_file(self.schema_path(project_id))

    def save_schema(self, project_id: str, schema: dict[str, Any]) -> dict[str, Any]:
        from .store import ClaimStore

        data = validate_schema(schema)
        path = self.schema_path(project_id)
        old = None
        if path.is_file():
            try:
                old = load_schema_file(path)
            except SchemaError:
                old = None
        used: set[str] = set()
        db = self.db_path(project_id)
        if db.is_file():
            store = ClaimStore(db)
            try:
                used = store.referenced_type_ids(project_id)
            finally:
                store.close()
        if old is not None:
            check_schema_edit(old, data, used)
        save_schema_file(path, data)
        return data

    def _write_meta(self, project_id: str, meta: dict[str, Any]) -> None:
        path = self.meta_path(project_id)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(path)


def require_schema_for_run(store: ProjectStore, project_id: str) -> dict[str, Any]:
    from .schema import validate_schema_for_run

    path = store.schema_path(project_id)
    if not path.is_file():
        raise SchemaError(f"No schema.json for project {project_id}")
    return validate_schema_for_run(json.loads(path.read_text(encoding="utf-8")))
