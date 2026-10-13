from __future__ import annotations

import re
import uuid
from pathlib import Path

_VALID = re.compile(r"^(profile\.md|lessons\.md|projects/[A-Za-z0-9_-]+\.md)$")

TEMPLATES = {
    "INDEX.md": (
        "# 目次\n\n"
        "- profile.md: 自分について\n"
        "- lessons.md: 訂正や失敗から得た注意点\n"
    ),
    "profile.md": "# プロフィール\n\n",
    "lessons.md": "# 注意点\n\n",
}


class MemoryPathError(ValueError):
    """書き込みが許可されていない記憶ファイルを指定した。"""


def is_valid_memory_path(rel: str) -> bool:
    return bool(_VALID.fullmatch(rel))


class MemoryStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def ensure(self) -> list[str]:
        created: list[str] = []
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "projects").mkdir(exist_ok=True)
        for name, body in TEMPLATES.items():
            path = self.root / name
            if not path.exists():
                path.write_text(body, encoding="utf-8")
                created.append(name)
        return created

    def load_core(self) -> str:
        parts = []
        for name in ("INDEX.md", "profile.md"):
            path = self.root / name
            body = path.read_text(encoding="utf-8") if path.exists() else TEMPLATES[name]
            parts.append(f"## {name}\n{body.strip()}")
        return "\n\n".join(parts)

    def append(self, rel: str, text: str, summary: str | None = None) -> str:
        if not is_valid_memory_path(rel):
            raise MemoryPathError(f"書き込めない記憶ファイルです: {rel}")
        flat = " ".join(text.split())
        if not flat:
            raise ValueError("空の記憶は追加できません")
        summary = " ".join((summary or "").split())[:80] or flat[:40]
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(f"# {path.stem}\n\n", encoding="utf-8")
            self._ensure_index_line(rel, summary)
        entry_id = uuid.uuid4().hex[:8]
        with path.open("a", encoding="utf-8") as f:
            f.write(f"- {flat} <!-- id:{entry_id} -->\n")
        return entry_id

    def undo(self, rel: str, entry_id: str) -> bool:
        if not is_valid_memory_path(rel):
            raise MemoryPathError(f"書き込めない記憶ファイルです: {rel}")
        path = self.root / rel
        if not path.exists():
            return False
        marker = f"<!-- id:{entry_id} -->"
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        kept = [line for line in lines if marker not in line]
        if len(kept) == len(lines):
            return False
        if rel.startswith("projects/") and "".join(kept).strip() == f"# {path.stem}":
            path.unlink()
            self._remove_index_line(rel)
            return True
        path.write_text("".join(kept), encoding="utf-8")
        return True

    def _remove_index_line(self, rel: str) -> None:
        index = self.root / "INDEX.md"
        if not index.exists():
            return
        prefix = f"- {rel}:"
        lines = index.read_text(encoding="utf-8").splitlines(keepends=True)
        kept = [l for l in lines if not l.startswith(prefix)]
        if len(kept) != len(lines):
            index.write_text("".join(kept), encoding="utf-8")

    def _ensure_index_line(self, rel: str, summary: str) -> None:
        index = self.root / "INDEX.md"
        body = index.read_text(encoding="utf-8") if index.exists() else TEMPLATES["INDEX.md"]
        if f"- {rel}:" in body:
            return
        if not body.endswith("\n"):
            body += "\n"
        index.write_text(body + f"- {rel}: {summary}\n", encoding="utf-8")
