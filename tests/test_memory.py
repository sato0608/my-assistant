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
        ("profile.md\n", False),
        ("projects/a.md\n", False),
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


def test_append_flattens_and_caps_summary(store):
    store.append("projects/a.md", "x", "line1\nline2\n- fake: entry")
    idx = read(store, "INDEX.md")
    assert "- projects/a.md: line1 line2 - fake: entry\n" in idx
    store.append("projects/b.md", "x", "あ" * 200)
    idx = read(store, "INDEX.md")
    assert "- projects/b.md: " + "あ" * 80 + "\n" in idx
    assert "あ" * 81 not in idx


def test_undo_last_entry_removes_project_file_and_index_line(store):
    e = store.append("projects/p.md", "one", "概要")
    assert "- projects/p.md:" in read(store, "INDEX.md")
    assert store.undo("projects/p.md", e) is True
    assert not (store.root / "projects/p.md").exists()
    assert "projects/p.md" not in read(store, "INDEX.md")
    assert "profile.md" in read(store, "INDEX.md")


def test_undo_one_of_two_keeps_file_and_index(store):
    a = store.append("projects/p.md", "one", "概要")
    store.append("projects/p.md", "two")
    assert store.undo("projects/p.md", a) is True
    assert (store.root / "projects/p.md").exists()
    assert "- projects/p.md:" in read(store, "INDEX.md")
    assert "two" in read(store, "projects/p.md")


def test_undo_never_deletes_profile_or_lessons(store):
    e = store.append("lessons.md", "one")
    assert store.undo("lessons.md", e) is True
    assert (store.root / "lessons.md").exists()
    assert "- lessons.md:" in read(store, "INDEX.md")


def test_undo_keeps_hand_written_file_with_prose(store):
    path = store.root / "projects" / "thesis.md"
    path.write_text("# thesis\n\n研究の背景です。\n\n## 節\n本文\n", encoding="utf-8")
    store._ensure_index_line("projects/thesis.md", "手書き")
    e = store.append("projects/thesis.md", "追記")
    assert store.undo("projects/thesis.md", e) is True
    body = path.read_text(encoding="utf-8")
    assert "研究の背景です。" in body and "## 節" in body and "追記" not in body
    assert "- projects/thesis.md:" in read(store, "INDEX.md")


def test_undo_keeps_hand_written_star_bullets_file(store):
    path = store.root / "projects" / "notes.md"
    path.write_text("* item\n", encoding="utf-8")
    store._ensure_index_line("projects/notes.md", "手書き")
    e = store.append("projects/notes.md", "追記")
    assert store.undo("projects/notes.md", e) is True
    assert path.read_text(encoding="utf-8") == "* item\n"
    assert "- projects/notes.md:" in read(store, "INDEX.md")
