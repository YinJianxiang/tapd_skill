"""桥接调用 tapd-close-bug/scripts/close_bug.py。"""

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


def find_close_bug_script() -> Path:
    root = workspace_root()
    candidate = root / ".cursor" / "skills" / "tapd-close-bug" / "scripts" / "close_bug.py"
    if candidate.is_file():
        return candidate
    raise FileNotFoundError(f"未找到 close_bug.py: {candidate}")


def build_close_payload(
    bug_ids: List[str],
    *,
    comment: Optional[str] = None,
) -> Dict[str, Any]:
    ids = [str(x).strip() for x in bug_ids if str(x).strip()]
    if not ids:
        raise ValueError("bug_ids 不能为空")
    payload: Dict[str, Any] = {
        "bug_ids": ids,
        "status": "closed",
        "require_resolved": True,
    }
    if comment and str(comment).strip():
        payload["comment"] = str(comment).strip()
    return payload


def close_bugs(
    bug_ids: List[str],
    *,
    open_id: Optional[str] = None,
    comment: Optional[str] = None,
) -> Tuple[int, Dict[str, Any], str]:
    """
    批量关闭 resolved Bug（目标状态 closed）。
    返回 (returncode, parsed_json_or_empty, raw_stdout)。
    """
    root = workspace_root()
    script = find_close_bug_script()
    payload = build_close_payload(bug_ids, comment=comment)

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
        prefix="feishu_close_",
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


def format_close_result(parsed: Dict[str, Any], *, raw: str = "") -> str:
    if not parsed:
        return f"关闭失败：无有效返回。{raw[:200]}".strip()

    if parsed.get("batch"):
        summary = parsed.get("summary") or {}
        results = parsed.get("results") or []
        lines = [
            (
                "批量关闭完成："
                f"成功 {summary.get('success', 0)}，"
                f"跳过 {summary.get('skipped', 0)}，"
                f"失败 {summary.get('failed', 0)}"
            )
        ]
        for idx, item in enumerate(results, 1):
            title = str(item.get("title") or item.get("bug_id") or "?").strip()
            if item.get("error"):
                lines.append(f"{idx}) 失败 {title}：{item.get('error')}")
            elif item.get("skipped"):
                reason = str(item.get("message") or item.get("reason") or "已跳过")
                lines.append(f"{idx}) 跳过 {title}：{reason}")
            elif item.get("success"):
                bid = str(item.get("bug_id") or "").strip()
                url = str(item.get("bug_url") or "").strip()
                owner = str(item.get("new_owner") or item.get("owner_on_close") or "").strip()
                line = f"{idx}) 已关闭 {title}"
                if bid:
                    line += f"（{bid}）"
                if owner:
                    line += f"\n   处理人：{owner}"
                if url:
                    line += f"\n   {url}"
                if item.get("comment_error"):
                    line += f"\n   备注：关闭成功，但评论失败：{item.get('comment_error')}"
                lines.append(line)
            else:
                lines.append(f"{idx}) 失败 {title}")
        return "\n".join(lines)

    if parsed.get("error"):
        return f"关闭失败：{parsed.get('error')}"

    title = str(parsed.get("title") or parsed.get("bug_id") or "?").strip()
    if parsed.get("skipped"):
        return f"跳过 {title}：{parsed.get('message') or parsed.get('reason') or '已跳过'}"

    if parsed.get("success"):
        bid = str(parsed.get("bug_id") or "").strip()
        url = str(parsed.get("bug_url") or "").strip()
        owner = str(parsed.get("new_owner") or parsed.get("owner_on_close") or "").strip()
        parts = [f"已关闭 {title}"]
        if bid:
            parts.append(f"（{bid}）")
        if owner:
            parts.append(f"\n处理人：{owner}")
        if url:
            parts.append(f"\n{url}")
        if parsed.get("comment_error"):
            parts.append(f"\n备注：关闭成功，但评论失败：{parsed.get('comment_error')}")
        return " ".join(parts[:2]) + "".join(parts[2:])

    return f"关闭失败：{title}。{raw[:200]}".strip()
