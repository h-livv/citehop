"""Build extraction prompts from a project's schema.

This is the only module that translates schema *content* into model-facing text.
The rest of the pipeline treats the prompt as an opaque string plus the stored
paper text used for provenance.
"""

from __future__ import annotations

import json
from typing import Any

SCHEMA_BEGIN = "<<<SCHEMA_JSON>>>"
SCHEMA_END = "<<<END_SCHEMA_JSON>>>"
PAPER_BEGIN = "<<<PAPER_TEXT>>>"
PAPER_END = "<<<END_PAPER_TEXT>>>"
PASS_BEGIN = "<<<PASS_ID>>>"
PASS_END = "<<<END_PASS_ID>>>"

INSTRUCTION = """You extract structured claims from the paper text.

Rules:
- Use ONLY the claim types listed in SCHEMA_JSON. Do not invent types or fields.
- structured_fields keys must be exactly the keys listed for that claim_type.
- Every claim MUST include quoted_source_span: copy a substring of PAPER_TEXT character-for-character. Do not paraphrase the quote, fix hyphenation, or add ellipses.
- Do not use outside knowledge. If PAPER_TEXT does not support a claim, omit it.
- confidence_self_reported must be one of: high, medium, low.
- Return one JSON object and nothing else (no markdown, no commentary).

Return a single JSON object:
{"claims": [
  {
    "claim_type": "<type_id from schema>",
    "claim_text": "<short paraphrase>",
    "structured_fields": {},
    "quoted_source_span": "<verbatim substring of PAPER_TEXT>",
    "confidence_self_reported": "high"
  }
]}
"""


def build_extraction_prompt(
    schema: dict[str, Any],
    paper_text: str,
    pass_id: str,
) -> str:
    """Assemble a dual-pass prompt. `pass_id` is A or B (independent runs)."""
    types_block = []
    for item in schema["claim_types"]:
        fields = []
        for field in item["structured_fields"]:
            spec = f"{field['key']}: {field['type']}"
            if field["type"] == "enum":
                spec += f" one of {list(field['enum_values'])}"
            fields.append(spec)
        field_line = "; ".join(fields) if fields else "(no extra fields)"
        types_block.append(
            f"- {item['type_id']} ({item['display_name']}): {item['description']} "
            f"Fields: {field_line}"
        )
    catalog = "\n".join(types_block)
    schema_json = json.dumps(
        {
            "claim_types": [
                {
                    "type_id": ct["type_id"],
                    "display_name": ct["display_name"],
                    "description": ct["description"],
                    "structured_fields": ct["structured_fields"],
                }
                for ct in schema["claim_types"]
            ]
        },
        ensure_ascii=False,
        indent=2,
    )
    return (
        f"{INSTRUCTION}\n"
        f"Claim types to extract:\n{catalog}\n\n"
        f"{PASS_BEGIN}{pass_id}{PASS_END}\n"
        f"{SCHEMA_BEGIN}\n{schema_json}\n{SCHEMA_END}\n"
        f"{PAPER_BEGIN}\n{paper_text}\n{PAPER_END}\n"
    )


def extract_marked_section(prompt: str, begin: str, end: str) -> str:
    start = prompt.find(begin)
    stop = prompt.find(end)
    if start < 0 or stop < 0 or stop <= start:
        raise ValueError(f"Prompt missing markers {begin} … {end}")
    return prompt[start + len(begin) : stop].strip()
