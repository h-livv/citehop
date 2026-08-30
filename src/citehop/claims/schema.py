"""Project claim schema: load, validate, save. No domain vocabulary in this module."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

FIELD_TYPES = ("number", "string", "enum", "boolean")
_ID = re.compile(r"^[a-z][a-z0-9_]*$")


class SchemaError(ValueError):
    """Malformed or incomplete schema. Fail loudly; never invent defaults."""


def templates_dir() -> Path:
    return Path(__file__).resolve().parent / "templates"


def list_templates() -> list[dict[str, Any]]:
    out = []
    root = templates_dir()
    if not root.is_dir():
        return out
    for path in sorted(root.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        out.append(
            {
                "template_id": path.stem,
                "path": str(path),
                "schema_id": data.get("schema_id"),
                "project_domain_label": data.get("project_domain_label") or "",
                "claim_type_count": len(data.get("claim_types") or []),
            }
        )
    return out


def load_template(template_id: str) -> dict[str, Any]:
    path = templates_dir() / f"{template_id}.json"
    if not path.is_file():
        known = ", ".join(t["template_id"] for t in list_templates()) or "(none)"
        raise SchemaError(f"Unknown schema template {template_id!r}. Known: {known}")
    return load_schema_file(path)


def empty_schema(schema_id: str = "untitled") -> dict[str, Any]:
    return {
        "schema_id": schema_id,
        "project_domain_label": "",
        "claim_types": [],
    }


def load_schema_file(path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise SchemaError(f"Schema file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SchemaError(f"Schema is not valid JSON ({path}): {exc}") from exc
    return validate_schema(data)


def save_schema_file(path: Path, schema: dict[str, Any]) -> None:
    data = validate_schema(schema)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def validate_schema(data: Any) -> dict[str, Any]:
    """Structural validation. Domain labels are never inspected for branching."""
    if not isinstance(data, dict):
        raise SchemaError("Schema must be a JSON object")
    schema_id = data.get("schema_id")
    if not isinstance(schema_id, str) or not schema_id.strip():
        raise SchemaError("schema_id is required")
    label = data.get("project_domain_label", "")
    if label is None:
        label = ""
    if not isinstance(label, str):
        raise SchemaError("project_domain_label must be a string (display only)")
    raw_types = data.get("claim_types")
    if raw_types is None:
        raise SchemaError("claim_types is required (use an empty list if none yet)")
    if not isinstance(raw_types, list):
        raise SchemaError("claim_types must be a list")
    seen: set[str] = set()
    claim_types = []
    for i, item in enumerate(raw_types):
        claim_types.append(_validate_claim_type(item, i, seen))
    return {
        "schema_id": schema_id.strip(),
        "project_domain_label": label,
        "claim_types": claim_types,
    }


def validate_schema_for_run(data: Any) -> dict[str, Any]:
    schema = validate_schema(data)
    if not schema["claim_types"]:
        raise SchemaError(
            "Schema has no claim types. Add at least one claim type before running extraction."
        )
    return schema


def type_ids(schema: dict[str, Any]) -> list[str]:
    return [ct["type_id"] for ct in schema.get("claim_types") or []]


def fields_for_type(schema: dict[str, Any], type_id: str) -> list[dict[str, Any]]:
    for ct in schema.get("claim_types") or []:
        if ct["type_id"] == type_id:
            return list(ct.get("structured_fields") or [])
    raise SchemaError(f"claim_type {type_id!r} is not in this schema")


def check_schema_edit(
    old: dict[str, Any],
    new: dict[str, Any],
    referenced_type_ids: set[str],
) -> None:
    """Block edits that would orphan or silently reinterpret existing claims.

    Allowed while claims exist: add types, add fields, change display_name /
    description, remove unused types, remove field keys (stored extras remain).
    Blocked: remove or rename a type_id that any claim in the project uses;
    change the JSON type of a field key on a type that already has claims.
    """
    old_types = {ct["type_id"]: ct for ct in old.get("claim_types") or []}
    new_types = {ct["type_id"]: ct for ct in new.get("claim_types") or []}
    missing = sorted(referenced_type_ids - set(new_types))
    if missing:
        raise SchemaError(
            "Cannot remove claim type(s) "
            + ", ".join(repr(t) for t in missing)
            + " while claims of those types exist. Leave the type in the schema "
            "(or start a new project)."
        )
    for tid in referenced_type_ids:
        if tid not in old_types or tid not in new_types:
            continue
        old_fields = {
            f["key"]: f["type"] for f in (old_types[tid].get("structured_fields") or [])
        }
        new_fields = {
            f["key"]: f["type"] for f in (new_types[tid].get("structured_fields") or [])
        }
        for key, ftype in old_fields.items():
            if key in new_fields and new_fields[key] != ftype:
                raise SchemaError(
                    f"Cannot change {tid}.{key} from {ftype} to {new_fields[key]} "
                    "while claims of that type exist."
                )


def _validate_claim_type(item: Any, index: int, seen: set[str]) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise SchemaError(f"claim_types[{index}] must be an object")
    type_id = item.get("type_id")
    if not isinstance(type_id, str) or not _ID.match(type_id):
        raise SchemaError(
            f"claim_types[{index}].type_id must be snake_case starting with a letter "
            f"(got {type_id!r})"
        )
    if type_id in seen:
        raise SchemaError(f"Duplicate claim type_id {type_id!r}")
    seen.add(type_id)
    display = item.get("display_name")
    if not isinstance(display, str) or not display.strip():
        raise SchemaError(f"claim_types[{index}] ({type_id}) needs display_name")
    description = item.get("description")
    if not isinstance(description, str) or not description.strip():
        raise SchemaError(
            f"claim_types[{index}] ({type_id}) needs description "
            "(this text is shown to the extraction model)"
        )
    raw_fields = item.get("structured_fields")
    if raw_fields is None:
        raise SchemaError(
            f"claim_types[{index}] ({type_id}) needs structured_fields "
            "(use an empty list if this type has no extra fields)"
        )
    if not isinstance(raw_fields, list):
        raise SchemaError(f"claim_types[{index}].structured_fields must be a list")
    # Zero fields is allowed: the type still extracts claim_text + quoted_source_span.
    keys: set[str] = set()
    fields = []
    for j, field in enumerate(raw_fields):
        fields.append(_validate_field(field, type_id, j, keys))
    return {
        "type_id": type_id,
        "display_name": display.strip(),
        "description": description.strip(),
        "structured_fields": fields,
    }


def _validate_field(field: Any, type_id: str, index: int, keys: set[str]) -> dict[str, Any]:
    if not isinstance(field, dict):
        raise SchemaError(f"{type_id}.structured_fields[{index}] must be an object")
    key = field.get("key")
    if not isinstance(key, str) or not _ID.match(key):
        raise SchemaError(
            f"{type_id}.structured_fields[{index}].key must be snake_case starting with a letter"
        )
    if key in keys:
        raise SchemaError(f"{type_id} has duplicate field key {key!r}")
    keys.add(key)
    ftype = field.get("type")
    if ftype not in FIELD_TYPES:
        raise SchemaError(
            f"{type_id}.{key}.type must be one of {FIELD_TYPES} (got {ftype!r})"
        )
    enum_values = field.get("enum_values")
    if ftype == "enum":
        if not isinstance(enum_values, list) or not enum_values:
            raise SchemaError(f"{type_id}.{key} is enum but enum_values is missing or empty")
        if not all(isinstance(v, str) and v for v in enum_values):
            raise SchemaError(f"{type_id}.{key}.enum_values must be non-empty strings")
        enum_values = list(enum_values)
    else:
        if enum_values not in (None, [], ()):
            raise SchemaError(f"{type_id}.{key} has enum_values but type is {ftype!r}, not enum")
        enum_values = None
    out: dict[str, Any] = {"key": key, "type": ftype}
    if enum_values is not None:
        out["enum_values"] = enum_values
    return out


def clone_schema(schema: dict[str, Any], new_id: str) -> dict[str, Any]:
    cloned = deepcopy(validate_schema(schema))
    cloned["schema_id"] = new_id
    return cloned
