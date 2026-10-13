from __future__ import annotations

import asyncio
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
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
# SDK ⇔ CLI 間の JSON 1 メッセージの上限。既定の 1MB だと、大きなファイル
# (CSV ログ・PDF など)を Read した結果で CLIJSONDecodeError になるため広げる。
MAX_BUFFER_SIZE = 32 * 1024 * 1024

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


def expand_braces(pattern: str, limit: int = 64) -> list[str] | None:
    """{a,b} 形式のブレースを(入れ子も含めて)展開する。

    波括弧が対応していない、または展開結果が limit 件を超える場合は None。
    """
    depth = 0
    start = -1
    for i, ch in enumerate(pattern):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                return None
            if depth == 0:
                inner = pattern[start + 1 : i]
                options: list[str] = []
                level = 0
                cur = ""
                for c in inner:
                    if c == "{":
                        level += 1
                    elif c == "}":
                        level -= 1
                    if c == "," and level == 0:
                        options.append(cur)
                        cur = ""
                    else:
                        cur += c
                options.append(cur)
                results: list[str] = []
                for opt in options:
                    sub = expand_braces(pattern[:start] + opt + pattern[i + 1 :], limit)
                    if sub is None:
                        return None
                    results.extend(sub)
                    if len(results) > limit:
                        return None
                return results
    if depth != 0:
        return None
    return [pattern]


def is_pattern_safe(pattern: str | None) -> bool:
    """Glob の pattern / Grep の glob が許可フォルダの外を指さないか検査する。"""
    if not pattern:
        return True
    alternatives = expand_braces(pattern)
    if alternatives is None:
        return False
    for alt in alternatives:
        if alt.startswith(("/", "~")):
            return False
        if ".." in alt.replace("\\", "/").split("/"):
            return False
    return True


def _decide_tool_use(tool_name, tool_input, roots, cwd: Path) -> tuple[bool, str]:
    if not isinstance(tool_name, str) or tool_name not in READ_TOOLS:
        return False, f"{tool_name} は許可されていません"
    if not isinstance(tool_input, dict):
        return False, "ツールの入力が不正です"
    pattern = None
    if tool_name == "Glob":
        pattern = tool_input.get("pattern")
    elif tool_name == "Grep":
        pattern = tool_input.get("glob")
    if pattern is not None and not isinstance(pattern, str):
        return False, "パターンが文字列ではありません"
    if not is_pattern_safe(pattern):
        return False, f"パターンに絶対パスまたは .. は使えません: {pattern}"
    if tool_name == "Read":
        target = tool_input.get("file_path")
        if not isinstance(target, str) or not target.strip():
            return False, "Read の file_path が空または不正です"
    else:
        target = tool_input.get("path")
        if target is None or target == "":
            target = str(cwd)
        elif not isinstance(target, str):
            return False, "path が文字列ではありません"
    if not is_path_allowed(target, roots, cwd):
        return False, f"許可フォルダの外です: {target}"
    return True, ""


def decide_tool_use(tool_name, tool_input, roots, cwd: Path) -> tuple[bool, str]:
    """ツール呼び出しを許可するか判定する唯一の関数(フックと can_use_tool が共用)。

    判定中の例外は拒否として扱う(fail closed)。
    """
    try:
        return _decide_tool_use(tool_name, tool_input, roots, cwd)
    except Exception as e:  # noqa: BLE001
        return False, f"判定エラーのため拒否しました ({type(e).__name__})"


def make_can_use_tool(roots, cwd: Path):
    async def can_use_tool(tool_name, tool_input, context):
        allowed, reason = decide_tool_use(tool_name, tool_input, roots, cwd)
        if allowed:
            return PermissionResultAllow()
        return PermissionResultDeny(message=reason)

    return can_use_tool


def make_pre_tool_use_hook(roots, cwd: Path):
    """PreToolUse フック。ユーザー設定の自動許可より先に評価される権威的ゲート。"""

    async def hook(input_data, tool_use_id, context):
        try:
            tool_name = input_data.get("tool_name")
            tool_input = input_data.get("tool_input")
            allowed, reason = decide_tool_use(tool_name, tool_input, roots, cwd)
        except Exception as e:  # noqa: BLE001
            allowed, reason = False, f"判定エラーのため拒否しました ({type(e).__name__})"
        spec = {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow" if allowed else "deny",
        }
        if not allowed:
            spec["permissionDecisionReason"] = reason
        return {"hookSpecificOutput": spec}

    return hook


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
- 記憶の追記は、返答のあとに画面へ提案され、ユーザーが承認すると保存されます。ユーザーにファイルを直接編集するよう案内しないでください。

## 記憶(常に参照する内容)
{core_memory}
"""


def build_options(config: Config, system_prompt: str) -> ClaudeAgentOptions:
    roots = [config.memory_dir, *config.allowed_roots]
    return ClaudeAgentOptions(
        system_prompt=system_prompt,
        cwd=str(config.memory_dir),
        # ユーザーの ~/.claude 設定(自動許可など)を読み込まない。[] は「何も読まない」
        # (None だと SDK 既定でユーザー設定が読まれる)。
        setting_sources=[],
        # PreToolUse フックが権威的ゲート。can_use_tool / disallowed_tools は追加の防御層。
        hooks={
            "PreToolUse": [
                HookMatcher(
                    matcher=None,
                    hooks=[make_pre_tool_use_hook(roots, config.memory_dir)],
                )
            ]
        },
        can_use_tool=make_can_use_tool(roots, config.memory_dir),
        disallowed_tools=list(BLOCKED_TOOLS),
        max_turns=20,
        max_buffer_size=MAX_BUFFER_SIZE,
    )


def build_oneshot_options(config: Config, system_prompt: str) -> ClaudeAgentOptions:
    """提案用ワンショット。ツールは全て拒否する(roots=[] のフックを多重防御として付ける)。"""
    return ClaudeAgentOptions(
        system_prompt=system_prompt,
        cwd=str(config.memory_dir),
        max_turns=1,
        setting_sources=[],
        hooks={
            "PreToolUse": [
                HookMatcher(
                    matcher=None,
                    hooks=[make_pre_tool_use_hook([], config.memory_dir)],
                )
            ]
        },
        disallowed_tools=BLOCKED_TOOLS + list(READ_TOOLS),
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
        self._lock: asyncio.Lock | None = None

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

    async def _drop(self, client) -> None:
        if client is None:
            return
        if self._client is client:
            self._client = None
        try:
            await client.disconnect()
        except Exception:
            pass

    async def stream(self, message: str) -> AsyncIterator[dict]:
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            client = None
            completed = False
            try:
                try:
                    client = await self._ensure_client()
                    await client.query(message)
                    async for msg in client.receive_response():
                        for event in message_to_events(msg):
                            yield event
                    completed = True
                except Exception as e:
                    # 先にクリーンアップする(consumer が error 受信後に止めても確実に実行される)
                    completed = True
                    await self._drop(client if client is not None else self._client)
                    yield {"type": "error", "message": f"{type(e).__name__}: {e}"}
            finally:
                if not completed:
                    # aclose() / CancelledError / GeneratorExit で途中終了した場合
                    await self._drop(client)

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
        options = build_oneshot_options(self._config, system_prompt)
        texts: list[str] = []
        async for msg in query(prompt=prompt, options=options):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        texts.append(block.text)
        return "".join(texts)
