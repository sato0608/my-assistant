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


def test_summary_flattened_and_capped():
    raw = '[{"file": "projects/a.md", "text": "t", "summary": "a\\nb\\n' + "x" * 200 + '"}]'
    (p,) = parse_proposals(raw)
    assert "\n" not in p.summary and len(p.summary) == 80
    assert p.summary.startswith("a b ")


def test_blank_summary_becomes_none():
    (p,) = parse_proposals('[{"file": "projects/a.md", "text": "t", "summary": " \\n "}]')
    assert p.summary is None


def test_text_capped_at_300():
    (p,) = parse_proposals('[{"file": "lessons.md", "text": "' + "y" * 1000 + '"}]')
    assert len(p.text) == 300


def test_deeply_nested_json_returns_empty():
    assert parse_proposals("[" * 5000 + "]" * 5000) == []
