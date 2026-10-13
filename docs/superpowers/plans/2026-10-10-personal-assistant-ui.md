# 自分専用 AI アシスタント UI 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Claude Agent SDK をラップした、メモリ(Markdown)付きのローカル Web チャットアプリを作る。

**Architecture:** FastAPI サーバーが Claude Agent SDK(Python)を呼び、応答を NDJSON でブラウザへストリーミングする。記憶は `memory/*.md` に置き、`INDEX.md` と `profile.md` だけを毎回システムプロンプトに入れる。Claude には読み取りツールのみ許可し、記憶の書き込みはユーザーが承認した後にサーバー側(`memory.py`)だけが行う。

**Tech Stack:** Python 3.10+、`claude-agent-sdk`、FastAPI、uvicorn、pytest、httpx(テスト用)、ビルド不要の素の HTML/JS

**Spec:** `docs/superpowers/specs/2026-10-10-personal-assistant-ui-design.md`

## Global Constraints

- 言語は Python に統一する。フロントはフレームワークなしの1ファイル(`static/index.html`)
- サーバーは `127.0.0.1` のみで待ち受ける。認証・複数ユーザー対応は作らない
- 書き込み先は `memory/` 配下のみ。許可フォルダ(研究・開発)は読み取り専用
- 記憶ファイルとして書き込めるのは `profile.md`、`lessons.md`、`projects/<英数字・-・_>.md` の3種類だけ
- 記憶は Markdown ファイル。データベースとベクトル検索は作らない
- 毎回システムプロンプトに入れる記憶は `INDEX.md` と `profile.md` のみ
- 記憶の追記はユーザーの承認が必須で、承認後も取り消せる
- Claude に許可するツールは `Read` / `Grep` / `Glob` のみ(許可フォルダ内に限る)
- 回答言語は日本語
- Git / Notion / Web 検索との接続、メモリの自動整理は作らない(スコープ外)

## ファイル構成

プロジェクトルートは、この `docs/` を含むディレクトリ。

```
config.example.json        設定の見本(config.json は .gitignore)
requirements.txt
.gitignore
README.md
assistant/
  __init__.py
  __main__.py              サーバー起動 (python -m assistant)
  config.py                config.json の読み込み
  memory.py                MemoryStore: 読み込み・追記・取り消し・パス検証
  proposals.py             記憶の追記案(Proposal)のパース
  agent.py                 パス制限・システムプロンプト・Assistant クラス
  server.py                FastAPI アプリ
  cli.py                   コマンドラインで1往復する確認用
static/
  index.html               チャット画面
tests/
  test_config.py
  test_memory.py
  test_proposals.py
  test_agent.py
  test_server.py
memory/                    実行時に自動生成(.gitignore)
```

---

### Task 1: プロジェクトの土台と設定の読み込み

**Files:**
- Create: `requirements.txt`, `.gitignore`, `config.example.json`
- Create: `assistant/__init__.py`, `assistant/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Config`(frozen dataclass: `memory_dir: Path`, `allowed_roots: tuple[Path, ...]`, `host: str`, `port: int`)、`load_config(path: Path) -> Config`

- [ ] **Step 1: 環境を作る**

```bash
git init
python3 -m venv .venv
source .venv/bin/activate
cat > requirements.txt <<'EOF'
claude-agent-sdk
fastapi
uvicorn
pytest
httpx
EOF
pip install -r requirements.txt
```

- [ ] **Step 2: SDK の API を確認する(計画の前提を実測する)**

```bash
python - <<'EOF'
from claude_agent_sdk import (
    ClaudeSDKClient, ClaudeAgentOptions, query,
    AssistantMessage, ResultMessage, TextBlock, ToolUseBlock,
    PermissionResultAllow, PermissionResultDeny,
)
o = ClaudeAgentOptions(
    system_prompt="x", cwd=".", disallowed_tools=["Bash"], max_turns=1,
    can_use_tool=None,
)
for m in ("connect", "disconnect", "query", "receive_response", "interrupt"):
    assert hasattr(ClaudeSDKClient, m), m
print("OK")
EOF
```

Expected: `OK`。ImportError や AssertionError が出た場合は、この後のタスクで使う名前(`agent.py` の import とクライアントのメソッド呼び出し)を、実際の SDK に合わせてから進める。

- [ ] **Step 3: 依存を固定して .gitignore と設定の見本を作る**

```bash
pip freeze > requirements.lock
cat > .gitignore <<'EOF'
.venv/
__pycache__/
*.pyc
.pytest_cache/
config.json
memory/
EOF
cat > config.example.json <<'EOF'
{
  "memory_dir": "memory",
  "allowed_roots": [],
  "host": "127.0.0.1",
  "port": 8000
}
EOF
mkdir -p assistant tests static
touch assistant/__init__.py
```

- [ ] **Step 4: 失敗するテストを書く**

`tests/test_config.py`:

```python
import json

from assistant.config import load_config


def test_load_config_resolves_relative_paths(tmp_path):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(
        json.dumps(
            {
                "memory_dir": "memory",
                "allowed_roots": ["code", str(tmp_path / "abs")],
                "port": 9000,
            }
        ),
        encoding="utf-8",
    )
    cfg = load_config(cfg_file)
    assert cfg.memory_dir == (tmp_path / "memory").resolve()
    assert cfg.allowed_roots == (
        (tmp_path / "code").resolve(),
        (tmp_path / "abs").resolve(),
    )
    assert cfg.port == 9000
    assert cfg.host == "127.0.0.1"


def test_load_config_defaults(tmp_path):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text("{}", encoding="utf-8")
    cfg = load_config(cfg_file)
    assert cfg.memory_dir == (tmp_path / "memory").resolve()
    assert cfg.allowed_roots == ()
    assert cfg.port == 8000
```

- [ ] **Step 5: テストが失敗することを確認する**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'assistant.config'`)

- [ ] **Step 6: 実装する**

`assistant/config.py`:

```python
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


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

    return Config(
        memory_dir=resolve(raw.get("memory_dir", "memory")),
        allowed_roots=tuple(resolve(p) for p in raw.get("allowed_roots", [])),
        host=raw.get("host", "127.0.0.1"),
        port=int(raw.get("port", 8000)),
    )
```

- [ ] **Step 7: テストが通ることを確認する**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS(2 passed)

- [ ] **Step 8: コミット**

```bash
git add -A
git commit -m "chore: プロジェクトの土台と設定の読み込みを追加"
```

---

### Task 2: メモリストア(読み込み・追記・取り消し)

**Files:**
- Create: `assistant/memory.py`
- Test: `tests/test_memory.py`

**Interfaces:**
- Produces:
  - `MemoryPathError(ValueError)`
  - `is_valid_memory_path(rel: str) -> bool`
  - `MemoryStore(root: Path)` with:
    - `.root: Path`
    - `.ensure() -> list[str]`(作成したファイル名の一覧。すべて存在すれば `[]`)
    - `.load_core() -> str`(`INDEX.md` と `profile.md` を連結した文字列)
    - `.append(rel: str, text: str, summary: str | None = None) -> str`(エントリ ID を返す)
    - `.undo(rel: str, entry_id: str) -> bool`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_memory.py`:

```python
import pytest

from assistant.memory import MemoryPathError, MemoryStore, is_valid_memory_path


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(tmp_path / "memory")
    s.ensure()
    return s


def read(store, rel):
    return (store.root / rel).read_text(encoding="utf-8")


def test_ensure_creates_templates(tmp_path):
    s = MemoryStore(tmp_path / "memory")
    created = s.ensure()
    assert "INDEX.md" in created
    assert (s.root / "profile.md").exists()
    assert (s.root / "lessons.md").exists()
    assert (s.root / "projects").is_dir()
    assert s.ensure() == []


def test_load_core_contains_index_and_profile(store):
    text = store.load_core()
    assert "# 目次" in text
    assert "# プロフィール" in text


@pytest.mark.parametrize(
    "rel, ok",
    [
        ("profile.md", True),
        ("lessons.md", True),
        ("projects/pdr-research.md", True),
        ("../secret.md", False),
        ("projects/../x.md", False),
        ("projects/a/b.md", False),
        ("notes.txt", False),
        ("INDEX.md", False),
        ("/etc/passwd", False),
    ],
)
def test_is_valid_memory_path(rel, ok):
    assert is_valid_memory_path(rel) is ok


def test_append_adds_line_with_id(store):
    entry_id = store.append("lessons.md", "PDRの角度はラジアンで扱う")
    body = read(store, "lessons.md")
    assert "- PDRの角度はラジアンで扱う" in body
    assert f"<!-- id:{entry_id} -->" in body


def test_append_flattens_newlines(store):
    store.append("lessons.md", "1行目\n2行目")
    assert "- 1行目 2行目" in read(store, "lessons.md")


def test_append_new_project_file_updates_index(store):
    store.append("projects/pdr-research.md", "粒子数は500で固定", summary="卒業研究PDR")
    assert read(store, "projects/pdr-research.md").startswith("# pdr-research")
    assert "- projects/pdr-research.md: 卒業研究PDR" in read(store, "INDEX.md")


def test_append_does_not_duplicate_index_line(store):
    store.append("projects/a.md", "x", summary="s")
    store.append("projects/a.md", "y", summary="s")
    assert read(store, "INDEX.md").count("projects/a.md") == 1


def test_append_rejects_invalid_path(store):
    with pytest.raises(MemoryPathError):
        store.append("../x.md", "t")


def test_append_rejects_empty_text(store):
    with pytest.raises(ValueError):
        store.append("lessons.md", "   \n ")


def test_undo_removes_only_that_entry(store):
    keep = store.append("lessons.md", "残す")
    drop = store.append("lessons.md", "消す")
    assert store.undo("lessons.md", drop) is True
    body = read(store, "lessons.md")
    assert "残す" in body and f"id:{keep}" in body
    assert "消す" not in body
    assert store.undo("lessons.md", drop) is False
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `python -m pytest tests/test_memory.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'assistant.memory'`)

- [ ] **Step 3: 実装する**

`assistant/memory.py`:

```python
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
    return bool(_VALID.match(rel))


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
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(f"# {path.stem}\n\n", encoding="utf-8")
            self._ensure_index_line(rel, summary or flat[:40])
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
        path.write_text("".join(kept), encoding="utf-8")
        return True

    def _ensure_index_line(self, rel: str, summary: str) -> None:
        index = self.root / "INDEX.md"
        body = index.read_text(encoding="utf-8") if index.exists() else TEMPLATES["INDEX.md"]
        if f"- {rel}:" in body:
            return
        if not body.endswith("\n"):
            body += "\n"
        index.write_text(body + f"- {rel}: {summary}\n", encoding="utf-8")
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `python -m pytest tests/test_memory.py -v`
Expected: PASS(全件)

- [ ] **Step 5: コミット**

```bash
git add assistant/memory.py tests/test_memory.py
git commit -m "feat: Markdown メモリストアを追加"
```

---

### Task 3: 記憶の追記案のパース

**Files:**
- Create: `assistant/proposals.py`
- Test: `tests/test_proposals.py`

**Interfaces:**
- Consumes: `is_valid_memory_path(rel: str) -> bool`(Task 2)
- Produces:
  - `Proposal`(frozen dataclass: `file: str`, `text: str`, `summary: str | None = None`、メソッド `to_dict() -> dict`)
  - `MAX_PROPOSALS = 5`
  - `parse_proposals(raw: str) -> list[Proposal]`(壊れた入力は `[]`)

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_proposals.py`:

```python
from assistant.proposals import MAX_PROPOSALS, Proposal, parse_proposals


def test_parse_valid_json_inside_code_fence():
    raw = '```json\n[{"file": "lessons.md", "text": "角度はラジアン", "summary": null}]\n```'
    assert parse_proposals(raw) == [Proposal("lessons.md", "角度はラジアン", None)]


def test_parse_keeps_summary():
    raw = '[{"file": "projects/pdr.md", "text": "粒子数は500", "summary": "PDR研究"}]'
    assert parse_proposals(raw)[0].summary == "PDR研究"


def test_invalid_path_is_dropped():
    raw = '[{"file": "../x.md", "text": "悪い"}, {"file": "profile.md", "text": "良い"}]'
    result = parse_proposals(raw)
    assert [p.file for p in result] == ["profile.md"]


def test_blank_text_is_dropped():
    assert parse_proposals('[{"file": "profile.md", "text": "  "}]') == []


def test_garbage_returns_empty():
    assert parse_proposals("記憶に残すことはありません") == []
    assert parse_proposals("[not json]") == []
    assert parse_proposals("") == []


def test_cap_at_max():
    items = ",".join('{"file": "lessons.md", "text": "t%d"}' % i for i in range(10))
    assert len(parse_proposals(f"[{items}]")) == MAX_PROPOSALS


def test_to_dict():
    assert Proposal("lessons.md", "a", "b").to_dict() == {
        "file": "lessons.md",
        "text": "a",
        "summary": "b",
    }
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `python -m pytest tests/test_proposals.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'assistant.proposals'`)

- [ ] **Step 3: 実装する**

`assistant/proposals.py`:

```python
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
    except json.JSONDecodeError:
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
        if not (isinstance(summary, str) and summary.strip()):
            summary = None
        result.append(Proposal(file, text.strip(), summary))
        if len(result) >= MAX_PROPOSALS:
            break
    return result
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `python -m pytest tests/test_proposals.py -v`
Expected: PASS(7 passed)

- [ ] **Step 5: コミット**

```bash
git add assistant/proposals.py tests/test_proposals.py
git commit -m "feat: 記憶の追記案のパースを追加"
```

---

### Task 4: エージェント層(パス制限・システムプロンプト・Assistant)

**Files:**
- Create: `assistant/agent.py`
- Test: `tests/test_agent.py`

**Interfaces:**
- Consumes: `Config`(Task 1)、`MemoryStore.load_core()`(Task 2)、`parse_proposals` / `Proposal`(Task 3)
- Produces:
  - `is_path_allowed(path: str, roots: list[Path] | tuple[Path, ...], cwd: Path) -> bool`
  - `tool_target_path(tool_name: str, tool_input: dict, cwd: Path) -> str`
  - `make_can_use_tool(roots, cwd)` → 非同期コールバック `(tool_name, tool_input, context) -> PermissionResultAllow | PermissionResultDeny`
  - `build_system_prompt(core_memory: str, memory_dir: Path, allowed_roots) -> str`
  - `build_options(config: Config, system_prompt: str) -> ClaudeAgentOptions`
  - `message_to_events(msg) -> list[dict]`(イベント形式は下記)
  - `Assistant(config, store, client_factory=None, oneshot=None)` with:
    - `async stream(message: str) -> AsyncIterator[dict]`
    - `async interrupt() -> None`
    - `async reset() -> None`
    - `async propose(user_message: str, assistant_text: str) -> list[Proposal]`
- イベント形式: `{"type": "text", "text": str}` / `{"type": "tool", "name": str, "target": str}` / `{"type": "error", "message": str}`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_agent.py`:

```python
import asyncio
import os

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)

from assistant.agent import (
    Assistant,
    build_options,
    build_system_prompt,
    is_path_allowed,
    make_can_use_tool,
    message_to_events,
    tool_target_path,
)
from assistant.config import Config
from assistant.memory import MemoryStore


# ---- パス制限 ----------------------------------------------------------

def test_path_inside_root_is_allowed(tmp_path):
    root = tmp_path / "code"
    root.mkdir()
    (root / "a.py").write_text("x")
    assert is_path_allowed(str(root / "a.py"), [root], tmp_path)


def test_path_outside_root_is_denied(tmp_path):
    root = tmp_path / "code"
    root.mkdir()
    assert not is_path_allowed(str(tmp_path / "secret.txt"), [root], tmp_path)


def test_parent_traversal_is_denied(tmp_path):
    root = tmp_path / "code"
    root.mkdir()
    assert not is_path_allowed(str(root / ".." / "secret.txt"), [root], tmp_path)


def test_relative_path_resolves_against_cwd(tmp_path):
    root = tmp_path / "code"
    root.mkdir()
    assert is_path_allowed("a.py", [root], root)
    assert not is_path_allowed("a.py", [root], tmp_path)


def test_symlink_escape_is_denied(tmp_path):
    root = tmp_path / "code"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / "link"
    os.symlink(outside, link)
    assert not is_path_allowed(str(link / "x.txt"), [root], tmp_path)


def test_tool_target_path(tmp_path):
    assert tool_target_path("Read", {"file_path": "/a/b"}, tmp_path) == "/a/b"
    assert tool_target_path("Grep", {"pattern": "x", "path": "/c"}, tmp_path) == "/c"
    assert tool_target_path("Glob", {"pattern": "*.py"}, tmp_path) == str(tmp_path)


def test_can_use_tool_allows_read_inside_and_denies_others(tmp_path):
    root = tmp_path / "code"
    root.mkdir()
    cb = make_can_use_tool([root], root)

    allowed = asyncio.run(cb("Read", {"file_path": str(root / "a.py")}, None))
    assert isinstance(allowed, PermissionResultAllow)

    outside = asyncio.run(cb("Read", {"file_path": str(tmp_path / "x")}, None))
    assert isinstance(outside, PermissionResultDeny)

    bash = asyncio.run(cb("Bash", {"command": "ls"}, None))
    assert isinstance(bash, PermissionResultDeny)

    write = asyncio.run(cb("Write", {"file_path": str(root / "a.py")}, None))
    assert isinstance(write, PermissionResultDeny)


# ---- システムプロンプトとオプション -----------------------------------

def test_system_prompt_contains_memory_and_roots(tmp_path):
    prompt = build_system_prompt("## profile.md\n卒業研究はPDR", tmp_path / "memory", [tmp_path / "code"])
    assert "卒業研究はPDR" in prompt
    assert str(tmp_path / "memory") in prompt
    assert str(tmp_path / "code") in prompt


def test_build_options(tmp_path):
    cfg = Config(memory_dir=tmp_path / "memory", allowed_roots=(tmp_path / "code",))
    opts = build_options(cfg, "SYSTEM")
    assert opts.system_prompt == "SYSTEM"
    assert str(opts.cwd) == str(cfg.memory_dir)
    assert "Bash" in opts.disallowed_tools
    assert opts.can_use_tool is not None


# ---- メッセージ変換 ----------------------------------------------------

def make_result(is_error=False, errors=None):
    return ResultMessage(
        subtype="error" if is_error else "success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=is_error,
        num_turns=1,
        session_id="s",
        errors=errors,
    )


def test_message_to_events_text_and_tool():
    msg = AssistantMessage(
        content=[
            TextBlock(text="こんにちは"),
            ToolUseBlock(id="1", name="Read", input={"file_path": "/a/b.md"}),
        ],
        model="test",
    )
    assert message_to_events(msg) == [
        {"type": "text", "text": "こんにちは"},
        {"type": "tool", "name": "Read", "target": "/a/b.md"},
    ]


def test_message_to_events_result_ok_is_silent():
    assert message_to_events(make_result()) == []


def test_message_to_events_result_error():
    events = message_to_events(make_result(is_error=True, errors=["認証に失敗"]))
    assert events == [{"type": "error", "message": "認証に失敗"}]


# ---- Assistant ---------------------------------------------------------

class FakeClient:
    instances: list["FakeClient"] = []

    def __init__(self, options):
        self.options = options
        self.connected = False
        self.sent: list[str] = []
        self.interrupted = False
        FakeClient.instances.append(self)

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def query(self, message):
        self.sent.append(message)

    async def interrupt(self):
        self.interrupted = True

    async def receive_response(self):
        yield AssistantMessage(content=[TextBlock(text="了解")], model="test")
        yield make_result()


@pytest.fixture
def assistant(tmp_path):
    FakeClient.instances = []
    cfg = Config(memory_dir=tmp_path / "memory", allowed_roots=())
    store = MemoryStore(cfg.memory_dir)
    store.ensure()
    store.append("profile.md", "卒業研究はPDRの改良")

    async def oneshot(prompt, system_prompt):
        oneshot.calls.append((prompt, system_prompt))
        return '[{"file": "lessons.md", "text": "覚えておく"}]'

    oneshot.calls = []
    a = Assistant(cfg, store, client_factory=FakeClient, oneshot=oneshot)
    return a, oneshot


async def collect(agen):
    return [ev async for ev in agen]


def test_stream_injects_memory_and_yields_events(assistant):
    a, _ = assistant
    events = asyncio.run(collect(a.stream("やあ")))
    assert events == [{"type": "text", "text": "了解"}]
    client = FakeClient.instances[0]
    assert client.sent == ["やあ"]
    assert "卒業研究はPDRの改良" in client.options.system_prompt


def test_stream_reuses_client_until_reset(assistant):
    a, _ = assistant

    async def scenario():
        await collect(a.stream("1"))
        await collect(a.stream("2"))
        assert len(FakeClient.instances) == 1
        await a.reset()
        assert FakeClient.instances[0].connected is False
        await collect(a.stream("3"))
        assert len(FakeClient.instances) == 2

    asyncio.run(scenario())


def test_interrupt_calls_client(assistant):
    a, _ = assistant

    async def scenario():
        await collect(a.stream("1"))
        await a.interrupt()

    asyncio.run(scenario())
    assert FakeClient.instances[0].interrupted is True


def test_interrupt_without_client_is_noop(assistant):
    a, _ = assistant
    asyncio.run(a.interrupt())


def test_propose_uses_oneshot_and_parses(assistant):
    a, oneshot = assistant
    result = asyncio.run(a.propose("質問", "回答"))
    assert [p.file for p in result] == ["lessons.md"]
    prompt, _system = oneshot.calls[0]
    assert "質問" in prompt and "回答" in prompt
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `python -m pytest tests/test_agent.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'assistant.agent'`)

- [ ] **Step 3: 実装する**

`assistant/agent.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    query,
)

from .config import Config
from .memory import MemoryStore
from .proposals import Proposal, parse_proposals

READ_TOOLS = ("Read", "Grep", "Glob")
BLOCKED_TOOLS = ["Bash", "Write", "Edit", "NotebookEdit", "WebFetch", "WebSearch"]

PROPOSE_SYSTEM_PROMPT = """あなたは記憶の整理係です。ユーザーとアシスタントのやり取りを読み、
今後も役立つ事実だけを抽出して、JSON 配列だけを出力してください(説明文は不要)。

各要素の形式:
{"file": "<保存先>", "text": "<1行で書く事実>", "summary": "<新規 projects ファイルの場合のみ短い説明、それ以外は null>"}

保存先は次の3種類だけです。
- "profile.md": ユーザー本人について(専攻、好み、返答スタイルなど)
- "lessons.md": ユーザーが訂正した内容や、次回気をつけること
- "projects/<英数字とハイフン>.md": 特定の研究・開発プロジェクトの背景や決定事項

ルール:
- 一時的な話題や、すでに明らかな一般知識は含めない
- 残すべきことがなければ [] だけを出力する
- 最大5件まで
"""


# ---- パス制限 ------------------------------------------------------------

def is_path_allowed(path: str, roots, cwd: Path) -> bool:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = Path(cwd) / p
    p = p.resolve()
    for root in roots:
        r = Path(root).resolve()
        if p == r or r in p.parents:
            return True
    return False


def tool_target_path(tool_name: str, tool_input: dict, cwd: Path) -> str:
    if tool_name == "Read":
        return tool_input.get("file_path") or ""
    return tool_input.get("path") or str(cwd)


def make_can_use_tool(roots, cwd: Path):
    async def can_use_tool(tool_name, tool_input, context):
        if tool_name not in READ_TOOLS:
            return PermissionResultDeny(message=f"{tool_name} は許可されていません")
        target = tool_target_path(tool_name, tool_input, cwd)
        if not is_path_allowed(target, roots, cwd):
            return PermissionResultDeny(message=f"許可フォルダの外です: {target}")
        return PermissionResultAllow()

    return can_use_tool


# ---- システムプロンプトとオプション ---------------------------------------

def build_system_prompt(core_memory: str, memory_dir: Path, allowed_roots) -> str:
    roots = "\n".join(f"  - {r}" for r in allowed_roots) or "  - (なし)"
    return f"""あなたはユーザー専用のアシスタントです。主に卒業研究(研究支援)とソフトウェア開発(開発支援)を手伝います。
日本語で簡潔に答えてください。

## できること・できないこと
- 使えるのはファイルの読み取りと検索(Read / Grep / Glob)だけです。書き込みやコマンド実行はできません。
- 読めるのは次のフォルダの中だけです。
  - 記憶フォルダ: {memory_dir}
{roots}
- 記憶フォルダの projects/*.md は、話題に関係するときに自分で読んでください。

## 記憶(常に参照する内容)
{core_memory}
"""


def build_options(config: Config, system_prompt: str) -> ClaudeAgentOptions:
    roots = [config.memory_dir, *config.allowed_roots]
    return ClaudeAgentOptions(
        system_prompt=system_prompt,
        cwd=str(config.memory_dir),
        can_use_tool=make_can_use_tool(roots, config.memory_dir),
        disallowed_tools=list(BLOCKED_TOOLS),
        max_turns=20,
    )


# ---- メッセージ変換 -------------------------------------------------------

def message_to_events(msg) -> list[dict]:
    events: list[dict] = []
    if isinstance(msg, AssistantMessage):
        for block in msg.content:
            if isinstance(block, TextBlock):
                events.append({"type": "text", "text": block.text})
            elif isinstance(block, ToolUseBlock):
                target = ""
                for key in ("file_path", "pattern", "path"):
                    if block.input.get(key):
                        target = str(block.input[key])
                        break
                events.append({"type": "tool", "name": block.name, "target": target})
    elif isinstance(msg, ResultMessage) and msg.is_error:
        message = "; ".join(msg.errors) if msg.errors else "Claude の呼び出しでエラーが発生しました"
        events.append({"type": "error", "message": message})
    return events


# ---- Assistant ------------------------------------------------------------

OneShot = Callable[[str, str], Awaitable[str]]


class Assistant:
    def __init__(
        self,
        config: Config,
        store: MemoryStore,
        client_factory=None,
        oneshot: OneShot | None = None,
    ):
        self._config = config
        self._store = store
        self._client_factory = client_factory or ClaudeSDKClient
        self._oneshot = oneshot or self._sdk_oneshot
        self._client = None

    async def _ensure_client(self):
        if self._client is None:
            prompt = build_system_prompt(
                self._store.load_core(),
                self._config.memory_dir,
                self._config.allowed_roots,
            )
            client = self._client_factory(build_options(self._config, prompt))
            await client.connect()
            self._client = client
        return self._client

    async def stream(self, message: str) -> AsyncIterator[dict]:
        client = await self._ensure_client()
        await client.query(message)
        async for msg in client.receive_response():
            for event in message_to_events(msg):
                yield event

    async def interrupt(self) -> None:
        if self._client is not None:
            await self._client.interrupt()

    async def reset(self) -> None:
        if self._client is not None:
            client, self._client = self._client, None
            await client.disconnect()

    async def propose(self, user_message: str, assistant_text: str) -> list[Proposal]:
        prompt = f"## ユーザー\n{user_message}\n\n## アシスタント\n{assistant_text}"
        raw = await self._oneshot(prompt, PROPOSE_SYSTEM_PROMPT)
        return parse_proposals(raw)

    async def _sdk_oneshot(self, prompt: str, system_prompt: str) -> str:
        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            cwd=str(self._config.memory_dir),
            max_turns=1,
            disallowed_tools=BLOCKED_TOOLS + list(READ_TOOLS),
        )
        texts: list[str] = []
        async for msg in query(prompt=prompt, options=options):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        texts.append(block.text)
        return "".join(texts)
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `python -m pytest tests/test_agent.py -v`
Expected: PASS(全件)。`test_symlink_escape_is_denied` は macOS / Linux 前提。

- [ ] **Step 5: 全テストを実行する**

Run: `python -m pytest -v`
Expected: PASS(Task 1〜4 の全件)

- [ ] **Step 6: コミット**

```bash
git add assistant/agent.py tests/test_agent.py
git commit -m "feat: パス制限付きの Assistant 層を追加"
```

---

### Task 5: コマンドラインで1往復する(実際の Claude を使った確認)

**Files:**
- Create: `assistant/cli.py`

**Interfaces:**
- Consumes: `load_config`(Task 1)、`MemoryStore`(Task 2)、`Assistant`(Task 4)
- Produces: `python -m assistant.cli "<メッセージ>"` で1往復して終了するコマンド

- [ ] **Step 1: 設定ファイルを用意する**

```bash
cp config.example.json config.json
```

`config.json` の `allowed_roots` に、読ませたい研究フォルダの絶対パスを1〜2個入れる(例: `"/Users/あなた/Documents/research"`)。

- [ ] **Step 2: 実装する**

`assistant/cli.py`:

```python
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from .agent import Assistant
from .config import load_config
from .memory import MemoryStore


async def main(message: str) -> None:
    config = load_config(Path("config.json"))
    store = MemoryStore(config.memory_dir)
    store.ensure()
    assistant = Assistant(config, store)
    texts: list[str] = []
    try:
        async for event in assistant.stream(message):
            if event["type"] == "text":
                texts.append(event["text"])
                print(event["text"])
            elif event["type"] == "tool":
                print(f"[{event['name']}] {event['target']}")
            elif event["type"] == "error":
                print(f"[エラー] {event['message']}", file=sys.stderr)
        proposals = await assistant.propose(message, "".join(texts))
        for p in proposals:
            print(f"[記憶の提案] {p.file}: {p.text}")
    finally:
        await assistant.reset()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit('使い方: python -m assistant.cli "メッセージ"')
    asyncio.run(main(sys.argv[1]))
```

- [ ] **Step 3: 実際に1往復する**

```bash
python -m assistant.cli "あなたは何ができますか?短く教えてください"
```

Expected: 日本語の回答が表示され、エラーなしで終了する。認証エラーが出た場合は、Claude Code にログイン済みか、`ANTHROPIC_API_KEY` を設定済みかを確認する。

- [ ] **Step 4: 許可フォルダの制限を確認する**

```bash
python -m assistant.cli "/etc/hosts の中身を読んで表示してください"
```

Expected: 内容は表示されず、「許可フォルダの外」という趣旨の説明になる。許可フォルダ内のファイルを指定した場合は読める。

読めてしまった場合は、`Read` が `can_use_tool` を通らず自動許可されている。その場合は `build_options` に `allowed_tools=[]` と `permission_mode="default"` を明示して再確認し、それでも読めるなら PreToolUse フック(`hooks`)でパスを検査する方式に切り替えてから進める。

- [ ] **Step 5: コミット**

```bash
git add assistant/cli.py
git commit -m "feat: 確認用の CLI を追加"
```

---

### Task 6: Web サーバー

**Files:**
- Create: `assistant/server.py`, `assistant/__main__.py`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `Assistant`(Task 4)、`MemoryStore` / `MemoryPathError`(Task 2)、`Proposal`(Task 3)
- Produces:
  - `create_app(assistant, store, static_dir: Path) -> FastAPI`
  - `POST /api/chat` `{message}` → NDJSON ストリーム。イベントは Task 4 の `text` / `tool` / `error` に加え、`{"type":"proposals","items":[{file,text,summary}]}` と最後の `{"type":"done"}`
  - `POST /api/memory/apply` `{items:[{file,text,summary?}]}` → `{"applied":[{file,text,id}]}`(不正なパスは 400)
  - `POST /api/memory/undo` `{file,id}` → `{"ok": bool}`
  - `POST /api/interrupt` → `{"ok": true}`
  - `POST /api/reset` → `{"ok": true}`
  - `GET /` → `static/index.html`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_server.py`:

```python
import json

import pytest
from fastapi.testclient import TestClient

from assistant.memory import MemoryStore
from assistant.proposals import Proposal
from assistant.server import create_app


class FakeAssistant:
    def __init__(self, events=None, proposals=None, fail=False):
        self.events = events if events is not None else [{"type": "text", "text": "こんにちは"}]
        self.proposals = proposals or []
        self.fail = fail
        self.reset_called = False
        self.interrupt_called = False
        self.proposed_with = None

    async def stream(self, message):
        if self.fail:
            raise RuntimeError("接続に失敗")
        for ev in self.events:
            yield ev

    async def propose(self, user_message, assistant_text):
        self.proposed_with = (user_message, assistant_text)
        return self.proposals

    async def reset(self):
        self.reset_called = True

    async def interrupt(self):
        self.interrupt_called = True


@pytest.fixture
def setup(tmp_path):
    store = MemoryStore(tmp_path / "memory")
    store.ensure()
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<html>chat</html>", encoding="utf-8")
    return store, static


def parse_lines(body: str):
    return [json.loads(line) for line in body.splitlines() if line.strip()]


def test_index_served(setup):
    store, static = setup
    client = TestClient(create_app(FakeAssistant(), store, static))
    r = client.get("/")
    assert r.status_code == 200
    assert "chat" in r.text


def test_chat_streams_events_proposals_and_done(setup):
    store, static = setup
    fake = FakeAssistant(proposals=[Proposal("lessons.md", "覚える")])
    client = TestClient(create_app(fake, store, static))
    r = client.post("/api/chat", json={"message": "やあ"})
    events = parse_lines(r.text)
    assert [e["type"] for e in events] == ["text", "proposals", "done"]
    assert events[1]["items"] == [{"file": "lessons.md", "text": "覚える", "summary": None}]
    assert fake.proposed_with == ("やあ", "こんにちは")


def test_chat_error_is_reported_and_stream_ends(setup):
    store, static = setup
    client = TestClient(create_app(FakeAssistant(fail=True), store, static))
    events = parse_lines(client.post("/api/chat", json={"message": "x"}).text)
    assert events[0]["type"] == "error"
    assert "接続に失敗" in events[0]["message"]
    assert events[-1]["type"] == "done"


def test_chat_without_text_skips_proposal(setup):
    store, static = setup
    fake = FakeAssistant(events=[])
    client = TestClient(create_app(fake, store, static))
    events = parse_lines(client.post("/api/chat", json={"message": "x"}).text)
    assert [e["type"] for e in events] == ["done"]
    assert fake.proposed_with is None


def test_apply_and_undo(setup):
    store, static = setup
    client = TestClient(create_app(FakeAssistant(), store, static))
    r = client.post(
        "/api/memory/apply",
        json={"items": [{"file": "lessons.md", "text": "角度はラジアン"}]},
    )
    assert r.status_code == 200
    applied = r.json()["applied"][0]
    assert "角度はラジアン" in (store.root / "lessons.md").read_text(encoding="utf-8")

    r = client.post("/api/memory/undo", json={"file": "lessons.md", "id": applied["id"]})
    assert r.json() == {"ok": True}
    assert "角度はラジアン" not in (store.root / "lessons.md").read_text(encoding="utf-8")


def test_apply_rejects_invalid_path(setup):
    store, static = setup
    client = TestClient(create_app(FakeAssistant(), store, static))
    r = client.post("/api/memory/apply", json={"items": [{"file": "../x.md", "text": "t"}]})
    assert r.status_code == 400


def test_reset_and_interrupt(setup):
    store, static = setup
    fake = FakeAssistant()
    client = TestClient(create_app(fake, store, static))
    assert client.post("/api/reset").json() == {"ok": True}
    assert client.post("/api/interrupt").json() == {"ok": True}
    assert fake.reset_called and fake.interrupt_called
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `python -m pytest tests/test_server.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'assistant.server'`)

- [ ] **Step 3: サーバーを実装する**

`assistant/server.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from .memory import MemoryPathError, MemoryStore


class ChatRequest(BaseModel):
    message: str


class ProposalIn(BaseModel):
    file: str
    text: str
    summary: str | None = None


class ApplyRequest(BaseModel):
    items: list[ProposalIn]


class UndoRequest(BaseModel):
    file: str
    id: str


def _line(event: dict) -> str:
    return json.dumps(event, ensure_ascii=False) + "\n"


def create_app(assistant, store: MemoryStore, static_dir: Path) -> FastAPI:
    app = FastAPI()

    @app.get("/")
    async def index():
        return FileResponse(Path(static_dir) / "index.html")

    @app.post("/api/chat")
    async def chat(req: ChatRequest):
        async def gen():
            texts: list[str] = []
            try:
                async for event in assistant.stream(req.message):
                    if event["type"] == "text":
                        texts.append(event["text"])
                    yield _line(event)
                full_text = "".join(texts)
                if full_text:
                    proposals = await assistant.propose(req.message, full_text)
                    yield _line(
                        {"type": "proposals", "items": [p.to_dict() for p in proposals]}
                    )
            except Exception as e:  # 会話を壊さず、画面にエラーを返す
                yield _line({"type": "error", "message": f"{type(e).__name__}: {e}"})
            yield _line({"type": "done"})

        return StreamingResponse(gen(), media_type="application/x-ndjson")

    @app.post("/api/memory/apply")
    async def apply(req: ApplyRequest):
        applied = []
        for item in req.items:
            try:
                entry_id = store.append(item.file, item.text, item.summary)
            except (MemoryPathError, ValueError) as e:
                raise HTTPException(status_code=400, detail=str(e))
            applied.append({"file": item.file, "text": item.text, "id": entry_id})
        return {"applied": applied}

    @app.post("/api/memory/undo")
    async def undo(req: UndoRequest):
        try:
            ok = store.undo(req.file, req.id)
        except MemoryPathError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"ok": ok}

    @app.post("/api/interrupt")
    async def interrupt():
        await assistant.interrupt()
        return {"ok": True}

    @app.post("/api/reset")
    async def reset():
        await assistant.reset()
        return {"ok": True}

    return app
```

`assistant/__main__.py`:

```python
from __future__ import annotations

from pathlib import Path

import uvicorn

from .agent import Assistant
from .config import load_config
from .memory import MemoryStore
from .server import create_app


def main() -> None:
    config = load_config(Path("config.json"))
    store = MemoryStore(config.memory_dir)
    created = store.ensure()
    if created:
        print(f"記憶ファイルを新規作成しました: {', '.join(created)}")
    assistant = Assistant(config, store)
    static_dir = Path(__file__).resolve().parent.parent / "static"
    app = create_app(assistant, store, static_dir)
    uvicorn.run(app, host=config.host, port=config.port)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `python -m pytest tests/test_server.py -v`
Expected: PASS(7 passed)

- [ ] **Step 5: 全テストを実行する**

Run: `python -m pytest -v`
Expected: PASS(全件)

- [ ] **Step 6: コミット**

```bash
git add assistant/server.py assistant/__main__.py tests/test_server.py
git commit -m "feat: FastAPI サーバーを追加"
```

---

### Task 7: チャット画面と、通しの動作確認

**Files:**
- Create: `static/index.html`, `README.md`

**Interfaces:**
- Consumes: Task 6 の全 API

- [ ] **Step 1: チャット画面を書く**

`static/index.html`:

```html
<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>マイアシスタント</title>
<style>
  :root { --bg:#fff; --fg:#1d1d1f; --muted:#6e6e73; --line:#d2d2d7; --me:#e8f0fe; --ai:#f5f5f7; --accent:#0a66c2; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#1c1c1e; --fg:#f5f5f7; --muted:#98989d; --line:#3a3a3c; --me:#1e3a5f; --ai:#2c2c2e; --accent:#64a8f0; }
  }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--fg); font:16px/1.6 -apple-system, "Hiragino Sans", sans-serif; display:flex; flex-direction:column; height:100vh; }
  header { padding:10px 16px; border-bottom:1px solid var(--line); display:flex; justify-content:space-between; align-items:center; }
  header h1 { font-size:16px; margin:0; }
  #log { flex:1; overflow-y:auto; padding:16px; }
  .msg { max-width:760px; margin:0 auto 12px; padding:10px 14px; border-radius:12px; white-space:pre-wrap; overflow-wrap:anywhere; }
  .me { background:var(--me); }
  .ai { background:var(--ai); }
  .tool, .err { max-width:760px; margin:0 auto 8px; font-size:13px; color:var(--muted); }
  .err { color:#c0392b; }
  .proposals { max-width:760px; margin:0 auto 12px; border:1px dashed var(--line); border-radius:12px; padding:10px 14px; }
  .proposals h2 { font-size:13px; margin:0 0 6px; color:var(--muted); }
  .proposals label { display:block; font-size:14px; }
  .proposals code { color:var(--muted); font-size:12px; }
  form { border-top:1px solid var(--line); padding:12px 16px; display:flex; gap:8px; max-width:792px; width:100%; margin:0 auto; }
  textarea { flex:1; resize:none; padding:10px; border:1px solid var(--line); border-radius:10px; background:var(--bg); color:var(--fg); font:inherit; }
  button { padding:8px 14px; border:1px solid var(--line); border-radius:10px; background:var(--bg); color:var(--fg); font:inherit; cursor:pointer; }
  button.primary { background:var(--accent); color:#fff; border-color:var(--accent); }
  button:disabled { opacity:.5; cursor:default; }
</style>
</head>
<body>
<header>
  <h1>マイアシスタント</h1>
  <button id="reset" type="button">新しい会話</button>
</header>
<div id="log"></div>
<form id="form">
  <textarea id="input" rows="2" placeholder="メッセージを入力(Enterで送信、Shift+Enterで改行)"></textarea>
  <button id="send" class="primary" type="submit">送信</button>
  <button id="stop" type="button" hidden>中断</button>
</form>

<script>
const log = document.getElementById("log");
const form = document.getElementById("form");
const input = document.getElementById("input");
const sendBtn = document.getElementById("send");
const stopBtn = document.getElementById("stop");
const IDLE_MS = 90000;

let controller = null;
let idleTimer = null;

function add(cls, text) {
  const el = document.createElement("div");
  el.className = cls;
  el.textContent = text;
  log.appendChild(el);
  log.scrollTop = log.scrollHeight;
  return el;
}

async function post(path, body) {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error(path + " " + r.status);
  return r.json();
}

function setBusy(busy) {
  sendBtn.disabled = busy;
  stopBtn.hidden = !busy;
  if (!busy) { clearTimeout(idleTimer); idleTimer = null; }
}

function armIdleTimer() {
  clearTimeout(idleTimer);
  idleTimer = setTimeout(() => {
    add("err", "応答が " + IDLE_MS / 1000 + " 秒ありません。中断しました。");
    interrupt();
  }, IDLE_MS);
}

async function interrupt() {
  try { await post("/api/interrupt"); } catch (_) {}
  if (controller) controller.abort();
}

function showProposals(items) {
  if (!items.length) return;
  const box = document.createElement("div");
  box.className = "proposals";
  const h = document.createElement("h2");
  h.textContent = "記憶に残しますか?";
  box.appendChild(h);

  const checks = items.map((item) => {
    const label = document.createElement("label");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = true;
    label.appendChild(cb);
    label.append(" " + item.text + " ");
    const code = document.createElement("code");
    code.textContent = item.file;
    label.appendChild(code);
    box.appendChild(label);
    return { cb, item };
  });

  const save = document.createElement("button");
  save.textContent = "選んだものを記憶する";
  save.type = "button";
  save.onclick = async () => {
    const chosen = checks.filter((c) => c.cb.checked).map((c) => c.item);
    if (!chosen.length) { box.remove(); return; }
    save.disabled = true;
    try {
      const { applied } = await post("/api/memory/apply", { items: chosen });
      box.innerHTML = "";
      const h2 = document.createElement("h2");
      h2.textContent = "記憶しました(次の「新しい会話」から反映されます)";
      box.appendChild(h2);
      applied.forEach((a) => {
        const row = document.createElement("div");
        row.append(a.text + " ");
        const undo = document.createElement("button");
        undo.textContent = "取り消す";
        undo.type = "button";
        undo.onclick = async () => {
          await post("/api/memory/undo", { file: a.file, id: a.id });
          row.remove();
        };
        row.appendChild(undo);
        box.appendChild(row);
      });
    } catch (e) {
      save.disabled = false;
      add("err", "記憶の保存に失敗しました: " + e.message);
    }
  };
  const skip = document.createElement("button");
  skip.textContent = "今回は残さない";
  skip.type = "button";
  skip.onclick = () => box.remove();
  box.append(save, " ", skip);

  log.appendChild(box);
  log.scrollTop = log.scrollHeight;
}

async function send(text) {
  add("msg me", text);
  setBusy(true);
  controller = new AbortController();
  let aiEl = null;
  try {
    armIdleTimer();
    const r = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text }),
      signal: controller.signal,
    });
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      armIdleTimer();
      buf += decoder.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (!line) continue;
        const ev = JSON.parse(line);
        if (ev.type === "text") {
          if (!aiEl) aiEl = add("msg ai", "");
          aiEl.textContent += ev.text;
          log.scrollTop = log.scrollHeight;
        } else if (ev.type === "tool") {
          aiEl = null;
          add("tool", "🔧 " + ev.name + ": " + ev.target);
        } else if (ev.type === "error") {
          add("err", "エラー: " + ev.message);
        } else if (ev.type === "proposals") {
          showProposals(ev.items);
        }
      }
    }
  } catch (e) {
    if (e.name !== "AbortError") add("err", "通信エラー: " + e.message);
  } finally {
    controller = null;
    setBusy(false);
    input.focus();
  }
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text || sendBtn.disabled) return;
  input.value = "";
  send(text);
});
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    form.requestSubmit();
  }
});
stopBtn.addEventListener("click", interrupt);
document.getElementById("reset").addEventListener("click", async () => {
  await post("/api/reset");
  log.innerHTML = "";
  add("tool", "新しい会話を始めました(記憶を読み直しました)");
});
</script>
</body>
</html>
```

- [ ] **Step 2: README を書く**

`README.md`:

````markdown
# マイアシスタント

Claude Agent SDK をラップした、記憶付きのローカル Web アシスタント。

## セットアップ

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.json config.json   # allowed_roots に読ませたいフォルダを書く
```

認証は Claude Code にログイン済みであるか、`ANTHROPIC_API_KEY` を環境変数に設定してあれば動く。

## 起動

```bash
python -m assistant
```

ブラウザで http://127.0.0.1:8000 を開く。

## テスト

```bash
python -m pytest
```

## 記憶

`memory/` に Markdown で保存される(Git 管理外)。エディタで直接編集してよい。
追記は会話後に画面で承認した分だけ書き込まれ、「新しい会話」から反映される。
````

- [ ] **Step 3: サーバーを起動する**

```bash
python -m assistant
```

Expected: `Uvicorn running on http://127.0.0.1:8000` が表示される。

- [ ] **Step 4: ブラウザで手動確認する**

http://127.0.0.1:8000 を開き、次を順に確認する。

1. 「卒業研究のフォルダにあるファイルの一覧を教えて」と送る → `🔧 Glob` などの表示が出て、回答がストリーミングされる
2. 「私の専攻は情報系で、返事は箇条書きより文章が好きです」と送る → 回答後に「記憶に残しますか?」が出る
3. 「選んだものを記憶する」を押す → `memory/profile.md` に1行追記される(`cat memory/profile.md` で確認)
4. 「取り消す」を押す → その行が `memory/profile.md` から消える
5. もう一度記憶してから「新しい会話」を押し、「私について知っていることは?」と送る → 記憶した内容が回答に反映される
6. 長い質問を送り、回答中に「中断」を押す → 応答が止まり、その後も続けて会話できる
7. 「/etc/hosts を読んで」と送る → 読めずに断られる

すべて期待どおりなら完了。中断(6)の後に次の質問が応答しない場合は、`Assistant.interrupt()` の後に `reset()` を呼んで新しいクライアントで再開する処理を、`server.py` の `/api/interrupt` に足す。

- [ ] **Step 5: 全テストを実行して、コミットする**

```bash
python -m pytest -v
git add static/index.html README.md
git commit -m "feat: チャット画面と README を追加"
```

---

## Self-Review(計画をスペックと照合した結果)

**スペック網羅**

| スペックの項目 | 対応するタスク |
|---|---|
| チャット画面(ブラウザ) | Task 7 |
| Markdown メモリ(INDEX / profile / projects / lessons) | Task 2 |
| 毎回 INDEX と profile だけ読み込む | Task 2(`load_core`)、Task 4(`build_system_prompt`) |
| 許可フォルダの読み取り専用 | Task 4(`make_can_use_tool`)、Task 5 Step 4 で実測 |
| 追記案の承認と取り消し | Task 3、Task 6(`apply` / `undo`)、Task 7 |
| 自己強化(訂正の記録) | Task 4(`PROPOSE_SYSTEM_PROMPT` の `lessons.md`) |
| エラー表示と再送 | Task 6(`error` イベント)、Task 7 |
| 許可外フォルダの拒否と理由表示 | Task 4(Deny メッセージ)、Task 5 |
| `memory/` が空・壊れている場合 | Task 2(`ensure`)、Task 6(`__main__` の通知) |
| タイムアウトと中断ボタン | Task 6(`/api/interrupt`)、Task 7(90 秒の無応答で自動中断) |
| テスト(memory / agent / 全体) | Task 2、4、6、7 |

**既知の前提(実測で確認する箇所)**
- `Read` が `can_use_tool` を通らず自動許可される可能性 → Task 5 Step 4 に確認手順と代替案を記載
- 中断後の `ClaudeSDKClient` の挙動 → Task 7 Step 4 の確認6に対処を記載
- SDK のクラス・メソッド名 → Task 1 Step 2 で実測

**意図的にスペックから変えた点**
- なし
