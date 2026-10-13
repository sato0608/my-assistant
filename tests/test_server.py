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


def test_apply_invalid_batch_writes_nothing(setup):
    store, static = setup
    before = (store.root / "lessons.md").read_text(encoding="utf-8")
    client = TestClient(create_app(FakeAssistant(), store, static))
    r = client.post(
        "/api/memory/apply",
        json={
            "items": [
                {"file": "lessons.md", "text": "有効な項目"},
                {"file": "../x.md", "text": "不正なパス"},
            ]
        },
    )
    assert r.status_code == 400
    assert (store.root / "lessons.md").read_text(encoding="utf-8") == before


def test_apply_empty_text_batch_writes_nothing(setup):
    store, static = setup
    before = (store.root / "lessons.md").read_text(encoding="utf-8")
    client = TestClient(create_app(FakeAssistant(), store, static))
    r = client.post(
        "/api/memory/apply",
        json={
            "items": [
                {"file": "lessons.md", "text": "有効な項目"},
                {"file": "lessons.md", "text": "   "},
            ]
        },
    )
    assert r.status_code == 400
    assert (store.root / "lessons.md").read_text(encoding="utf-8") == before


def test_apply_multiple_valid_items(setup):
    store, static = setup
    client = TestClient(create_app(FakeAssistant(), store, static))
    r = client.post(
        "/api/memory/apply",
        json={
            "items": [
                {"file": "lessons.md", "text": "一つ目"},
                {"file": "profile.md", "text": "二つ目"},
            ]
        },
    )
    assert r.status_code == 200
    applied = r.json()["applied"]
    assert [a["text"] for a in applied] == ["一つ目", "二つ目"]
    assert all(a["id"] for a in applied)
    assert "一つ目" in (store.root / "lessons.md").read_text(encoding="utf-8")
    assert "二つ目" in (store.root / "profile.md").read_text(encoding="utf-8")


def test_apply_rolls_back_when_write_fails_midway(setup, monkeypatch):
    store, static = setup
    original_append = store.append
    calls = {"n": 0}

    def flaky_append(rel, text, summary=None):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("書き込みに失敗")
        return original_append(rel, text, summary)

    monkeypatch.setattr(store, "append", flaky_append)
    client = TestClient(create_app(FakeAssistant(), store, static))
    r = client.post(
        "/api/memory/apply",
        json={
            "items": [
                {"file": "lessons.md", "text": "ロールバック対象"},
                {"file": "profile.md", "text": "失敗する項目"},
            ]
        },
    )
    assert r.status_code == 500
    assert "ロールバック対象" not in (store.root / "lessons.md").read_text(encoding="utf-8")


def test_host_not_in_allowlist_is_forbidden(setup):
    store, static = setup
    client = TestClient(create_app(FakeAssistant(), store, static, allowed_hosts={"testserver"}))
    assert client.get("/").status_code == 200
    assert client.get("/", headers={"Host": "evil.example"}).status_code == 403
    r = client.post("/api/reset", headers={"Host": "evil.example"})
    assert r.status_code == 403
    assert "detail" in r.json()


def test_cross_origin_post_is_forbidden(setup):
    store, static = setup
    fake = FakeAssistant()
    client = TestClient(create_app(fake, store, static, allowed_hosts={"testserver"}))
    r = client.post("/api/reset", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    assert fake.reset_called is False
    assert client.post("/api/reset", headers={"Origin": "http://testserver"}).status_code == 200
    assert fake.reset_called is True


def test_no_allowlist_means_no_host_restriction(setup):
    store, static = setup
    client = TestClient(create_app(FakeAssistant(), store, static))
    assert client.get("/", headers={"Host": "evil.example"}).status_code == 200
