"""Shared TAPD paths, independent of IDE skill installation."""
from pathlib import Path


def workspace_root() -> Path:
    return Path(__file__).resolve().parents[2]


def skill_script(skill: str, filename: str) -> Path:
    path = workspace_root() / "skills" / skill / "scripts" / filename
    if not path.is_file():
        raise FileNotFoundError(f"Shared TAPD script not found: {path}")
    return path
