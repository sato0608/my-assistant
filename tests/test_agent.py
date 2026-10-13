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


# ---- fix round 1: パターン検査とエラー処理 --------------------------------

from assistant.agent import is_pattern_safe  # noqa: E402


@pytest.mark.parametrize(
    "pattern,expected",
    [
        (None, True),
        ("", True),
        ("**/*.py", True),
        ("*.py", True),
        ("/etc/*", False),
        ("~/x/*", False),
        ("~", False),
        ("../x/**", False),
        ("a/../b", False),
        ("a\\..\\b", False),
        ("..", False),
        ("a..b/*.py", True),
    ],
)
def test_is_pattern_safe(pattern, expected):
    assert is_pattern_safe(pattern) is expected


def test_can_use_tool_checks_glob_pattern_and_grep_glob(tmp_path):
    root = tmp_path / "code"
    root.mkdir()
    cb = make_can_use_tool([root], root)

    for bad in ("/etc/*", "../x/**", "~/x/*"):
        r = asyncio.run(cb("Glob", {"pattern": bad}, None))
        assert isinstance(r, PermissionResultDeny), bad
    ok = asyncio.run(cb("Glob", {"pattern": "**/*.py"}, None))
    assert isinstance(ok, PermissionResultAllow)

    bad = asyncio.run(cb("Grep", {"pattern": "x", "glob": "/abs/*"}, None))
    assert isinstance(bad, PermissionResultDeny)
    ok = asyncio.run(cb("Grep", {"pattern": "x", "glob": "*.py"}, None))
    assert isinstance(ok, PermissionResultAllow)


class BoomClient(FakeClient):
    async def receive_response(self):
        raise RuntimeError("boom")
        yield  # pragma: no cover


class ConnectFailClient(FakeClient):
    async def connect(self):
        raise RuntimeError("no connect")


def _make_assistant(tmp_path, factory):
    FakeClient.instances = []
    cfg = Config(memory_dir=tmp_path / "memory", allowed_roots=())
    store = MemoryStore(cfg.memory_dir)
    store.ensure()
    return Assistant(cfg, store, client_factory=factory)


def test_stream_error_yields_event_and_recovers(tmp_path):
    a = _make_assistant(tmp_path, BoomClient)

    async def scenario():
        events = await collect(a.stream("1"))
        assert len(events) == 1
        assert events[0]["type"] == "error" and "boom" in events[0]["message"]
        assert len(FakeClient.instances) == 1
        assert FakeClient.instances[0].connected is False
        await collect(a.stream("2"))
        assert len(FakeClient.instances) == 2

    asyncio.run(scenario())


def test_stream_connect_failure_yields_error_and_caches_nothing(tmp_path):
    a = _make_assistant(tmp_path, ConnectFailClient)
    events = asyncio.run(collect(a.stream("1")))
    assert len(events) == 1 and events[0]["type"] == "error"
    assert "no connect" in events[0]["message"]
    assert a._client is None


# ---- fix round 2: ブレース展開と早期終了 ----------------------------------

from assistant.agent import expand_braces  # noqa: E402


@pytest.mark.parametrize(
    "pattern",
    ["**/*.py", "*.{py,md}", "{src,tests}/**/*.py", "a..b/*.py"],
)
def test_brace_patterns_allowed(pattern):
    assert is_pattern_safe(pattern) is True


@pytest.mark.parametrize(
    "pattern",
    [
        "{../x,y}/*",
        "{/etc,y}/*",
        "{~,x}/*",
        "{a,{..,b}}/c",
        "{a,b",
        "{a,b}{a,b}{a,b}{a,b}{a,b}{a,b}{a,b}",
    ],
)
def test_brace_patterns_denied(pattern):
    assert is_pattern_safe(pattern) is False


def test_expand_braces():
    assert expand_braces("abc") == ["abc"]
    assert sorted(expand_braces("*.{py,md}")) == ["*.md", "*.py"]
    assert sorted(expand_braces("{a,{b,c}}/d")) == ["a/d", "b/d", "c/d"]
    assert sorted(expand_braces("{a,b}{1,2}")) == ["a1", "a2", "b1", "b2"]
    assert expand_braces("{a,b") is None
    assert expand_braces("a}") is None
    assert expand_braces("{a,b}{a,b}{a,b}", limit=7) is None
    assert len(expand_braces("{a,b}{a,b}{a,b}", limit=8)) == 8


def test_stream_error_cleanup_runs_even_if_consumer_stops_early(tmp_path):
    a = _make_assistant(tmp_path, BoomClient)

    async def scenario():
        agen = a.stream("x")
        ev = await agen.__anext__()
        assert ev["type"] == "error"
        await agen.aclose()
        assert a._client is None
        assert FakeClient.instances[0].connected is False
        await collect(a.stream("y"))
        assert len(FakeClient.instances) == 2

    asyncio.run(scenario())


def test_stream_connect_failure_early_stop_caches_nothing(tmp_path):
    a = _make_assistant(tmp_path, ConnectFailClient)

    async def scenario():
        agen = a.stream("x")
        ev = await agen.__anext__()
        assert ev["type"] == "error"
        await agen.aclose()
        assert a._client is None

    asyncio.run(scenario())


# ---- 共有判定関数と PreToolUse フック(権威的ゲート) ---------------------

from assistant.agent import decide_tool_use, make_pre_tool_use_hook  # noqa: E402


def test_decide_tool_use(tmp_path):
    root = tmp_path / "code"
    root.mkdir()
    ok, reason = decide_tool_use("Read", {"file_path": str(root / "a.py")}, [root], root)
    assert ok is True
    ok, reason = decide_tool_use("Read", {"file_path": "/etc/hosts"}, [root], root)
    assert ok is False and reason
    ok, _ = decide_tool_use("Bash", {"command": "ls"}, [root], root)
    assert ok is False
    ok, _ = decide_tool_use("Glob", {"pattern": "/etc/*"}, [root], root)
    assert ok is False
    ok, _ = decide_tool_use("Grep", {"pattern": "x", "path": str(root)}, [root], root)
    assert ok is True


def _hook(tmp_path):
    root = tmp_path / "code"
    root.mkdir()
    return root, make_pre_tool_use_hook([root], root)


def _call_hook(hook, tool, tool_input):
    data = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input, "tool_use_id": "t1"}
    return asyncio.run(hook(data, "t1", {"signal": None}))


def _decision(out):
    spec = out["hookSpecificOutput"]
    assert spec["hookEventName"] == "PreToolUse"
    return spec["permissionDecision"], spec.get("permissionDecisionReason")


def test_hook_denies_read_outside(tmp_path):
    _, hook = _hook(tmp_path)
    d, reason = _decision(_call_hook(hook, "Read", {"file_path": "/etc/hosts"}))
    assert d == "deny" and reason


def test_hook_denies_bash_and_glob_absolute(tmp_path):
    _, hook = _hook(tmp_path)
    assert _decision(_call_hook(hook, "Bash", {"command": "ls"}))[0] == "deny"
    assert _decision(_call_hook(hook, "Glob", {"pattern": "/etc/*"}))[0] == "deny"


def test_hook_allows_read_and_grep_inside(tmp_path):
    root, hook = _hook(tmp_path)
    assert _decision(_call_hook(hook, "Read", {"file_path": str(root / "a.md")}))[0] == "allow"
    assert _decision(_call_hook(hook, "Grep", {"pattern": "x", "path": str(root)}))[0] == "allow"


def test_build_options_raises_cli_json_buffer_limit(tmp_path):
    # SDK 既定の 1MB だと、大きなファイルの Read 結果で CLIJSONDecodeError になる
    from assistant.agent import MAX_BUFFER_SIZE

    cfg = Config(memory_dir=tmp_path / "memory", allowed_roots=())
    opts = build_options(cfg, "SYSTEM")
    assert MAX_BUFFER_SIZE > 1024 * 1024
    assert opts.max_buffer_size == MAX_BUFFER_SIZE


def test_build_options_sets_hook_and_empty_setting_sources(tmp_path):
    cfg = Config(memory_dir=tmp_path / "memory", allowed_roots=(tmp_path / "code",))
    opts = build_options(cfg, "SYSTEM")
    assert opts.setting_sources == []
    matchers = opts.hooks["PreToolUse"]
    assert len(matchers) == 1 and len(matchers[0].hooks) == 1


# ---- fail closed(不正な入力でも例外にせず拒否する) ---------------------

BAD_READ_PATHS = ["a\0b", "~nosuchuser_xyz/x", 123, "", None, ["x"]]


@pytest.mark.parametrize("bad", BAD_READ_PATHS)
def test_decide_tool_use_denies_malformed_read_path(tmp_path, bad):
    ok, reason = decide_tool_use("Read", {"file_path": bad}, [tmp_path], tmp_path)
    assert ok is False and reason


def test_decide_tool_use_denies_missing_read_path(tmp_path):
    ok, reason = decide_tool_use("Read", {}, [tmp_path], tmp_path)
    assert ok is False and reason


@pytest.mark.parametrize("bad_input", [None, "str", ["a"], 5])
def test_decide_tool_use_denies_non_dict_input(tmp_path, bad_input):
    ok, reason = decide_tool_use("Read", bad_input, [tmp_path], tmp_path)
    assert ok is False and reason


@pytest.mark.parametrize("path", ["a\0b", "~nosuchuser_xyz/x", 123])
def test_decide_tool_use_denies_malformed_grep_glob_path(tmp_path, path):
    for tool in ("Grep", "Glob"):
        ok, reason = decide_tool_use(tool, {"pattern": "x", "path": path}, [tmp_path], tmp_path)
        assert ok is False and reason


@pytest.mark.parametrize("bad", BAD_READ_PATHS)
def test_hook_denies_malformed_read_path_without_raising(tmp_path, bad):
    _, hook = _hook(tmp_path)
    assert _decision(_call_hook(hook, "Read", {"file_path": bad}))[0] == "deny"


@pytest.mark.parametrize("bad_input", [None, "str", ["a"]])
def test_hook_denies_non_dict_tool_input(tmp_path, bad_input):
    _, hook = _hook(tmp_path)
    assert _decision(_call_hook(hook, "Read", bad_input))[0] == "deny"


@pytest.mark.parametrize(
    "data",
    [{}, {"tool_name": "Read"}, {"tool_input": {}}, None, "junk", {"tool_name": 5, "tool_input": {}}],
)
def test_hook_denies_malformed_hook_input(tmp_path, data):
    _, hook = _hook(tmp_path)
    out = asyncio.run(hook(data, "t1", {"signal": None}))
    assert _decision(out)[0] == "deny"


# ---- final fix wave ------------------------------------------------------

def test_system_prompt_says_memory_is_proposed_on_screen(tmp_path):
    prompt = build_system_prompt("x", tmp_path / "memory", [])
    assert "記憶の追記は、返答のあとに画面へ提案され、ユーザーが承認すると保存されます。" in prompt
    assert "ユーザーにファイルを直接編集するよう案内しないでください。" in prompt


def test_oneshot_options_have_deny_all_hook_and_no_settings(tmp_path):
    from assistant.agent import build_oneshot_options

    cfg = Config(memory_dir=tmp_path / "memory", allowed_roots=(tmp_path / "code",))
    opts = build_oneshot_options(cfg, "SYS")
    assert opts.setting_sources == []
    matchers = opts.hooks["PreToolUse"]
    assert len(matchers) == 1 and len(matchers[0].hooks) == 1
    hook = matchers[0].hooks[0]
    out = asyncio.run(
        hook({"tool_name": "Read", "tool_input": {"file_path": str(tmp_path / "memory" / "a.md")}}, None, None)
    )
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_aclose_mid_stream_drops_and_disconnects_client(tmp_path):
    a = _make_assistant(tmp_path, FakeClient)

    async def scenario():
        agen = a.stream("x")
        await agen.__anext__()
        await agen.aclose()
        assert a._client is None
        assert FakeClient.instances[0].connected is False
        await collect(a.stream("y"))
        assert len(FakeClient.instances) == 2

    asyncio.run(scenario())


class OrderClient(FakeClient):
    log: list[str] = []

    async def query(self, message):
        OrderClient.log.append(f"query:{message}")

    async def receive_response(self):
        OrderClient.log.append("recv-start")
        await asyncio.sleep(0.01)
        yield AssistantMessage(content=[TextBlock(text="a")], model="test")
        await asyncio.sleep(0.01)
        OrderClient.log.append("recv-end")
        yield make_result()


def test_concurrent_streams_are_serialised(tmp_path):
    a = _make_assistant(tmp_path, OrderClient)
    OrderClient.log = []

    async def scenario():
        await asyncio.gather(collect(a.stream("1")), collect(a.stream("2")))

    asyncio.run(scenario())
    assert OrderClient.log == [
        "query:1", "recv-start", "recv-end", "query:2", "recv-start", "recv-end",
    ]


def test_reset_mid_stream_does_not_crash_stream(tmp_path):
    a = _make_assistant(tmp_path, FakeClient)

    async def scenario():
        agen = a.stream("x")
        await agen.__anext__()
        await a.reset()
        rest = [ev async for ev in agen]
        assert all(ev["type"] != "error" for ev in rest)
        assert a._client is None

    asyncio.run(scenario())
