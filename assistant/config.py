from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


ALLOWED_HOSTS = ("127.0.0.1", "localhost")


@dataclass(frozen=True)
class Config:
    memory_dir: Path
    allowed_roots: tuple[Path, ...]
    host: str = "127.0.0.1"
    port: int = 8000


def load_config(path: Path) -> Config:
    path = Path(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    base = path.parent

    def resolve(p: str) -> Path:
        q = Path(p).expanduser()
        return (q if q.is_absolute() else base / q).resolve()

    host = raw.get("host", "127.0.0.1")
    if host not in ALLOWED_HOSTS:
        raise ValueError(
            f"config.json の host は 127.0.0.1 または localhost だけ指定できます: {host!r}"
        )

    return Config(
        memory_dir=resolve(raw.get("memory_dir", "memory")),
        allowed_roots=tuple(resolve(p) for p in raw.get("allowed_roots", [])),
        host=host,
        port=int(raw.get("port", 8000)),
    )
