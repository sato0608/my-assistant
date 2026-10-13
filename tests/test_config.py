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


import pytest


@pytest.mark.parametrize("host", ["0.0.0.0", "", "192.168.1.5", "::1"])
def test_load_config_rejects_non_local_host(tmp_path, host):
    f = tmp_path / "config.json"
    f.write_text(json.dumps({"host": host}), encoding="utf-8")
    with pytest.raises(ValueError, match="host"):
        load_config(f)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost"])
def test_load_config_accepts_local_host(tmp_path, host):
    f = tmp_path / "config.json"
    f.write_text(json.dumps({"host": host}), encoding="utf-8")
    assert load_config(f).host == host
