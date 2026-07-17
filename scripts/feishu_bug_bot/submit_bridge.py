"""桥接调用 tapd-submit-bug/scripts/submit_bug.py。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def workspace_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / ".cursor").is_dir() and (parent / ".tapd").is_dir():
            return parent
        if (parent / ".cursor").is_dir():
            return parent
    raise RuntimeError("无法定位项目根目录")


def find_submit_bug_script() -> Path:
    root = workspace_root()
    candidate = root / ".cursor" / "skills" / "tapd-submit-bug" / "scripts" / "submit_bug.py"
    if candidate.is_file():
        return candidate
    raise FileNotFoundError(f"未找到 submit_bug.py: {candidate}")


def build_payload_from_draft(draft: Dict[str, Any]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "title": str(draft.get("title") or "").strip(),
        "summary": str(draft.get("summary") or "").strip(),
        "description": str(draft.get("description") or "").strip(),
        "module": str(draft.get("module") or "").strip(),
        "priority": str(draft.get("priority") or "medium").strip(),
        "severity": str(draft.get("severity") or "normal").strip(),
    }
    label = str(draft.get("label") or "").strip()
    if label:
        payload["label"] = label
    owner = str(draft.get("current_owner") or "").strip()
    if owner:
        payload["current_owner"] = owner
    return payload


def submit_bug(
    draft: Dict[str, Any],
    *,
    image_paths: Optional[List[str]] = None,
    dry_run: bool = False,
    open_id: Optional[str] = None,
) -> Tuple[int, Dict[str, Any], str]:
    """
    返回 (returncode, parsed_json_or_empty, raw_stdout)。
    传入 open_id 时使用该用户合并后的 project_config 与 TAPD 凭证。
    """
    root = workspace_root()
    script = find_submit_bug_script()
    payload = build_payload_from_draft(draft)

    config_path: Optional[Path] = None
    temp_config = False
    if open_id:
        from user_config_store import resolve_tapd_env, tapd_env_to_process_env, write_temp_project_config

        config_path = write_temp_project_config(open_id)
        temp_config = True
        tapd_overrides = tapd_env_to_process_env(resolve_tapd_env(open_id))
    else:
        config_path = root / ".tapd" / "project_config.json"
        tapd_overrides = {}

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".json",
        prefix="feishu_bug_",
        delete=False,
        dir=str(root / ".tapd"),
    ) as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        payload_path = Path(f.name)

    cmd = [
        sys.executable,
        str(script),
        "--payload-file",
        str(payload_path),
        "--config-file",
        str(config_path),
    ]
    for p in image_paths or []:
        if p and Path(p).is_file():
            cmd.extend(["--file", str(p)])
    if dry_run:
        cmd.append("--dry-run")

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(tapd_overrides)

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=180,
        )
    finally:
        if temp_config and config_path is not None:
            try:
                config_path.unlink(missing_ok=True)  # type: ignore[call-arg]
            except TypeError:
                # Python < 3.8
                try:
                    if config_path.exists():
                        config_path.unlink()
                except OSError:
                    pass
            except OSError:
                pass
        try:
            payload_path.unlink(missing_ok=True)  # type: ignore[call-arg]
        except TypeError:
            try:
                if payload_path.exists():
                    payload_path.unlink()
            except OSError:
                pass
        except OSError:
            pass

    raw = (proc.stdout or "").strip() or (proc.stderr or "").strip()
    parsed: Dict[str, Any] = {}
    if proc.stdout:
        try:
            parsed = json.loads(proc.stdout.strip())
        except json.JSONDecodeError:
            for line in reversed(proc.stdout.strip().splitlines()):
                line = line.strip()
                if line.startswith("{"):
                    try:
                        parsed = json.loads(line)
                        break
                    except json.JSONDecodeError:
                        continue
    return proc.returncode, parsed, raw
