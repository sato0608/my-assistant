from __future__ import annotations

import json
from dataclasses import dataclass

from .memory import is_valid_memory_path

MAX_PROPOSALS = 5


@dataclass(frozen=True)
class Proposal:
    file: str
    text: str
    summary: str | None = None

    def to_dict(self) -> dict:
        return {"file": self.file, "text": self.text, "summary": self.summary}


def parse_proposals(raw: str) -> list[Proposal]:
    start = raw.find("[")
    end = raw.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        data = json.loads(raw[start : end + 1])
    except (ValueError, RecursionError):
        return []
    if not isinstance(data, list):
        return []

    result: list[Proposal] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        file = item.get("file")
        text = item.get("text")
        summary = item.get("summary")
        if not isinstance(file, str) or not is_valid_memory_path(file):
            continue
        if not isinstance(text, str) or not text.strip():
            continue
        flat_text = " ".join(text.split())[:300]
        if isinstance(summary, str):
            summary = " ".join(summary.split())[:80] or None
        else:
            summary = None
        result.append(Proposal(file, flat_text, summary))
        if len(result) >= MAX_PROPOSALS:
            break
    return result
