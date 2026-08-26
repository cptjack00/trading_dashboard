"""Config discovery for the New Run flow (#9): a local settings store of
root directories to scan for launchable `.toml` configs, per project.

ponytail: the store is one JSON file, read-and-rewritten whole on every add.
Fine at this scale (a handful of roots, added rarely by hand); a real
database is unwarranted.
"""

from __future__ import annotations

import json
from pathlib import Path

PROJECTS = ("rustle", "ticktrader")


class UnknownProjectError(ValueError):
    pass


def _check_project(project: str) -> None:
    if project not in PROJECTS:
        raise UnknownProjectError(project)


def load_config_roots(store_path: Path) -> dict[str, list[str]]:
    if not store_path.is_file():
        return {}
    try:
        data = json.loads(store_path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: list(v) for k, v in data.items() if isinstance(v, list)}


def add_config_root(store_path: Path, project: str, root: str) -> list[str]:
    """Add `root` to `project`'s scan list (deduped), persist, and return the
    updated list for that project.

    `root` is resolved to an absolute, normalized path before storing/deduping
    so a relative path doesn't silently depend on the server's cwd at scan
    time, and cosmetic variants (trailing slash, relative vs. absolute) of the
    same directory don't accumulate as separate entries.
    """
    _check_project(project)
    normalized = str(Path(root).resolve())
    roots = load_config_roots(store_path)
    project_roots = roots.setdefault(project, [])
    if normalized not in project_roots:
        project_roots.append(normalized)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(json.dumps(roots, indent=2))
    return project_roots


def scan_configs(roots: list[str]) -> list[str]:
    """Recursive, multi-level scan of `roots` for `*.toml` files. Roots that
    don't exist are skipped rather than raising."""
    found: set[str] = set()
    for root in roots:
        root_path = Path(root)
        if not root_path.is_dir():
            continue
        found.update(str(p) for p in root_path.rglob("*.toml") if p.is_file())
    return sorted(found)
