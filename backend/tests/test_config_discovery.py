import pytest

from signal_deck.config_discovery import (
    UnknownProjectError,
    add_config_root,
    load_config_roots,
    scan_configs,
)


def test_load_config_roots_missing_file_returns_empty(tmp_path):
    assert load_config_roots(tmp_path / "config_roots.json") == {}


def test_add_config_root_creates_file_and_returns_roots(tmp_path):
    store = tmp_path / "config_roots.json"
    roots = add_config_root(store, "rustle", "/data/rustle-configs")
    assert roots == ["/data/rustle-configs"]
    assert load_config_roots(store) == {"rustle": ["/data/rustle-configs"]}


def test_add_config_root_appends_and_dedupes(tmp_path):
    store = tmp_path / "config_roots.json"
    add_config_root(store, "rustle", "/a")
    add_config_root(store, "rustle", "/b")
    roots = add_config_root(store, "rustle", "/a")
    assert roots == ["/a", "/b"]


def test_add_config_root_keeps_projects_separate(tmp_path):
    store = tmp_path / "config_roots.json"
    add_config_root(store, "rustle", "/a")
    add_config_root(store, "ticktrader", "/b")
    assert load_config_roots(store) == {"rustle": ["/a"], "ticktrader": ["/b"]}


def test_add_config_root_rejects_unknown_project(tmp_path):
    store = tmp_path / "config_roots.json"
    with pytest.raises(UnknownProjectError):
        add_config_root(store, "bogus", "/a")


def test_scan_configs_finds_nested_toml_files(tmp_path):
    root = tmp_path / "configs"
    (root / "strategies" / "nested").mkdir(parents=True)
    (root / "top.toml").write_text("a = 1")
    (root / "strategies" / "mid.toml").write_text("b = 2")
    (root / "strategies" / "nested" / "deep.toml").write_text("c = 3")
    (root / "strategies" / "nested" / "ignore.txt").write_text("not a config")

    found = scan_configs([str(root)])

    assert found == sorted(
        str(p)
        for p in [root / "top.toml", root / "strategies" / "mid.toml", root / "strategies" / "nested" / "deep.toml"]
    )


def test_scan_configs_merges_multiple_roots_and_skips_missing(tmp_path):
    root_a = tmp_path / "a"
    root_a.mkdir()
    (root_a / "one.toml").write_text("x = 1")
    root_b = tmp_path / "b"

    found = scan_configs([str(root_a), str(root_b), str(tmp_path / "does-not-exist")])

    assert found == [str(root_a / "one.toml")]


def test_scan_configs_empty_roots_returns_empty_list():
    assert scan_configs([]) == []
