"""
TAPD 缺陷关闭脚本：
- 按 bug id / URL / 标题解析目标缺陷（支持批量）
- 更新 status 为 closed 或 resolved
- 可选追加关闭说明评论
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_TAPD_BASE_URL = "https://www.tapd.cn"
DONE_STATUS_SET = {"closed", "resolved"}


def should_skip_status_change(current_status: str, target_status: str) -> bool:
    """仅在同目标或无意义回退时跳过（resolved → closed 须执行）。"""
    current = str(current_status or "").strip().lower()
    target = str(target_status or "").strip().lower()
    if current == target:
        return True
    if target == "resolved" and current == "closed":
        return True
    return False
DEFAULT_CLOSE_STATUS = "closed"


def workspace_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / ".cursor").is_dir():
            return parent
    raise RuntimeError("无法定位项目根目录（未找到 .cursor 目录）")


def find_tapd_client_script() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "tapd-plus" / "scripts" / "tapd_client_stdlib.py"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("未找到 tapd 客户端脚本: tapd-plus/scripts/tapd_client_stdlib.py")


DEFAULT_CONFIG_FILE = workspace_root() / ".tapd" / "project_config.json"


def load_tapd_client():
    script_path = find_tapd_client_script()
    spec = importlib.util.spec_from_file_location("tapd_client_stdlib", script_path)
    if not spec or not spec.loader:
        raise RuntimeError("加载 tapd_client_stdlib 失败")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="关闭/解决 TAPD 缺陷（支持批量）")
    parser.add_argument("--payload-file", help="UTF-8 JSON 文件路径，推荐")
    parser.add_argument("--config-file", help="项目本地配置文件路径（可选）")
    parser.add_argument("--workspace-id", dest="workspace_id")
    parser.add_argument("--bug-id", dest="bug_id", help="单个缺陷 ID（长 id 或短 id）")
    parser.add_argument(
        "--bug-ids",
        dest="bug_ids",
        help="多个缺陷 ID，逗号分隔",
    )
    parser.add_argument("--bug-url", dest="bug_url", help="单个缺陷链接")
    parser.add_argument("--title", help="按标题匹配（仅唯一命中时可用）")
    parser.add_argument(
        "--status",
        choices=["closed", "resolved"],
        default=DEFAULT_CLOSE_STATUS,
        help="目标状态，默认 closed",
    )
    parser.add_argument("--comment", help="关闭说明（可选，写入评论；批量时作为默认说明）")
    parser.add_argument("--author", help="评论作者昵称，默认 defaults.reporter")
    parser.add_argument("--dry-run", action="store_true", help="仅校验，不实际关闭")
    parser.add_argument("--show-raw-response", action="store_true", help="输出 TAPD 原始响应")
    return parser.parse_args()


def load_config(args: argparse.Namespace) -> Dict[str, Any]:
    config_path = Path(args.config_file).resolve() if args.config_file else DEFAULT_CONFIG_FILE
    if not config_path.is_file():
        return {}
    with config_path.open("r", encoding="utf-8-sig") as f:
        config = json.load(f)
    if not isinstance(config, dict):
        raise ValueError("配置文件必须是 JSON 对象")
    return config


def pick_value(payload: Dict[str, Any], config: Dict[str, Any], key: str) -> Optional[Any]:
    if str(payload.get(key, "")).strip():
        return payload.get(key)
    defaults = config.get("defaults", {})
    if isinstance(defaults, dict) and str(defaults.get(key, "")).strip():
        return defaults.get(key)
    if str(config.get(key, "")).strip():
        return config.get(key)
    return None


def parse_bug_id_from_url(url: str) -> str:
    text = (url or "").strip()
    if not text:
        return ""
    m = re.search(r"/bugs/view/(\d+)", text)
    if m:
        return m.group(1)
    m = re.search(r"bug_id=(\d+)", text)
    if m:
        return m.group(1)
    return ""


def split_id_list(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        parts = [str(x).strip() for x in raw]
    else:
        text = str(raw).strip()
        if not text:
            return []
        parts = re.split(r"[,，;；\s]+", text)
    return [p for p in parts if p]


def normalize_bug_id(client: Any, workspace_id: str, bug_id: str) -> str:
    raw = str(bug_id or "").strip()
    if not raw:
        return ""
    try:
        wid = int(workspace_id)
    except ValueError:
        return raw
    return client.to_long_id(raw, wid)


def extract_bug_from_response(resp: Dict[str, Any]) -> Dict[str, Any]:
    data = resp.get("data")
    if isinstance(data, dict):
        bug = data.get("Bug", data)
        if isinstance(bug, dict):
            return bug
    if isinstance(data, list) and data:
        row = data[0]
        if isinstance(row, dict):
            bug = row.get("Bug", row)
            if isinstance(bug, dict):
                return bug
    return {}


def fetch_bug_by_id(client: Any, workspace_id: str, bug_id: str) -> Dict[str, Any]:
    long_id = normalize_bug_id(client, workspace_id, bug_id)
    resp = client.request(
        "GET",
        "bugs",
        params={"workspace_id": workspace_id, "id": long_id, "limit": 1},
    )
    bug = extract_bug_from_response(resp)
    if not bug:
        raise ValueError(f"未找到缺陷：{long_id}")
    return bug


def search_bug_by_title(client: Any, workspace_id: str, title: str) -> Dict[str, Any]:
    title_norm = (title or "").strip()
    if not title_norm:
        raise ValueError("title 不能为空")
    resp = client.request(
        "GET",
        "bugs",
        params={
            "workspace_id": workspace_id,
            "title": title_norm,
            "limit": 20,
            "fields": "id,title,status,current_owner",
        },
    )
    data = resp.get("data", [])
    candidates: List[Dict[str, Any]] = []
    if isinstance(data, list):
        for row in data:
            bug = row.get("Bug", row) if isinstance(row, dict) else {}
            if isinstance(bug, dict) and str(bug.get("id", "")).strip():
                candidates.append(bug)
    if not candidates:
        raise ValueError(f"未找到标题匹配的缺陷：{title_norm}")
    exact = [b for b in candidates if str(b.get("title", "")).strip() == title_norm]
    if len(exact) == 1:
        return exact[0]
    if len(candidates) == 1:
        return candidates[0]
    listing = "; ".join(
        f'{b.get("id")}={b.get("title")}' for b in candidates[:10]
    )
    raise ValueError(f"标题「{title_norm}」匹配到多个缺陷，请改用 bug_id 或链接。候选：{listing}")


def build_bug_url(workspace_id: str, bug_id: str) -> str:
    base_url = os.environ.get("TAPD_BASE_URL", DEFAULT_TAPD_BASE_URL).rstrip("/")
    return f"{base_url}/{workspace_id}/bugtrace/bugs/view/{bug_id}"


def load_payload(args: argparse.Namespace, config: Dict[str, Any]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    if args.payload_file:
        with Path(args.payload_file).resolve().open("r", encoding="utf-8-sig") as f:
            from_file = json.load(f)
        if not isinstance(from_file, dict):
            raise ValueError("payload 文件必须是 JSON 对象")
        payload.update(from_file)

    for key, val in [
        ("workspace_id", args.workspace_id),
        ("bug_id", args.bug_id),
        ("bug_url", args.bug_url),
        ("title", args.title),
        ("status", args.status),
        ("comment", args.comment),
        ("author", args.author),
    ]:
        if val is not None:
            payload[key] = val

    if args.bug_ids:
        payload["bug_ids"] = args.bug_ids

    for field in ["workspace_id", "author"]:
        value = pick_value(payload, config, field if field != "author" else "reporter")
        if value is not None and field == "workspace_id":
            payload["workspace_id"] = str(value)
        elif value is not None and field == "author" and not str(payload.get("author", "")).strip():
            payload["author"] = str(value)

    workspace_id = str(payload.get("workspace_id", "")).strip()
    if not workspace_id:
        raise ValueError("缺少 workspace_id：请配置 project_config.json 或在 payload 中提供")

    status = str(payload.get("status", DEFAULT_CLOSE_STATUS)).strip().lower()
    if status not in ("closed", "resolved"):
        raise ValueError("status 须为 closed 或 resolved")
    payload["status"] = status
    payload["workspace_id"] = workspace_id
    return payload


def collect_targets(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    解析批量/单个关闭目标。每项可含 bug_id / bug_url / title / comment（单条覆盖全局 comment）。
    """
    targets: List[Dict[str, Any]] = []

    bugs = payload.get("bugs")
    if isinstance(bugs, list):
        for item in bugs:
            if isinstance(item, str):
                text = item.strip()
                if not text:
                    continue
                if text.isdigit() or len(text) > 12:
                    targets.append({"bug_id": text})
                elif "/bugs/view/" in text or "bug_id=" in text:
                    targets.append({"bug_url": text})
                else:
                    targets.append({"title": text})
            elif isinstance(item, dict):
                targets.append(dict(item))

    for bid in split_id_list(payload.get("bug_ids")):
        targets.append({"bug_id": bid})

    if not targets:
        single: Dict[str, Any] = {}
        bug_id = str(payload.get("bug_id", "")).strip()
        if not bug_id:
            bug_id = parse_bug_id_from_url(str(payload.get("bug_url", "")).strip())
        if bug_id:
            single["bug_id"] = bug_id
        elif str(payload.get("bug_url", "")).strip():
            single["bug_url"] = str(payload.get("bug_url", "")).strip()
        elif str(payload.get("title", "")).strip():
            single["title"] = str(payload.get("title", "")).strip()
        if single:
            targets.append(single)

    if not targets:
        raise ValueError("请提供 bug_id、bug_ids、bugs、bug_url 或 title 之一")

    return targets


def resolve_target_bug(
    client: Any, workspace_id: str, target: Dict[str, Any]
) -> Tuple[str, Dict[str, Any]]:
    bug_id = str(target.get("bug_id", "")).strip()
    if not bug_id:
        bug_id = parse_bug_id_from_url(str(target.get("bug_url", "")).strip())
    if bug_id:
        long_id = normalize_bug_id(client, workspace_id, bug_id)
        bug = fetch_bug_by_id(client, workspace_id, long_id)
        return str(bug.get("id", long_id)), bug

    title = str(target.get("title", "")).strip()
    if title:
        bug = search_bug_by_title(client, workspace_id, title)
        return str(bug.get("id", "")).strip(), bug

    raise ValueError("每条目标须包含 bug_id、bug_url 或 title 之一")


def add_close_comment(
    client: Any,
    workspace_id: str,
    bug_id: str,
    author: str,
    comment: str,
) -> Optional[str]:
    text = (comment or "").strip()
    nick = (author or "").strip()
    if not text or not nick:
        return None
    resp = client.request(
        "POST",
        "comments",
        data={
            "workspace_id": workspace_id,
            "entry_id": bug_id,
            "entry_type": "bug",
            "author": nick,
            "description": text,
        },
    )
    data = resp.get("data", {})
    if isinstance(data, dict):
        c = data.get("Comment", data)
        if isinstance(c, dict) and c.get("id"):
            return str(c["id"])
    return None


def process_one_target(
    client: Any,
    payload: Dict[str, Any],
    target: Dict[str, Any],
    *,
    dry_run: bool,
    show_raw_response: bool,
) -> Dict[str, Any]:
    workspace_id = str(payload["workspace_id"])
    target_status = str(payload["status"]).strip().lower()
    global_comment = str(payload.get("comment", "")).strip()
    per_comment = str(target.get("comment", "")).strip()
    comment = per_comment or global_comment
    author = str(payload.get("author", "")).strip()

    bug_id, bug = resolve_target_bug(client, workspace_id, target)
    current_status = str(bug.get("status", "")).strip().lower()
    title = str(bug.get("title", "")).strip()
    owner = str(bug.get("current_owner", "")).strip()

    result: Dict[str, Any] = {
        "workspace_id": workspace_id,
        "bug_id": bug_id,
        "bug_url": build_bug_url(workspace_id, bug_id),
        "title": title,
        "current_status": current_status,
        "target_status": target_status,
        "current_owner": owner,
        "comment": comment or None,
        "author": author or None,
    }

    if should_skip_status_change(current_status, target_status):
        result.update(
            {
                "skipped": True,
                "reason": "already_done",
                "message": f"缺陷已是 {current_status} 状态，无需重复关闭",
            }
        )
        return result

    if dry_run:
        result["dry_run"] = True
        return result

    update_resp = client.request(
        "POST",
        "bugs",
        data={
            "workspace_id": workspace_id,
            "id": bug_id,
            "status": target_status,
        },
    )
    updated = extract_bug_from_response(update_resp)
    new_status = str(updated.get("status", target_status)).strip().lower()

    comment_id = add_close_comment(client, workspace_id, bug_id, author, comment)

    result.update(
        {
            "dry_run": False,
            "skipped": False,
            "new_status": new_status,
            "comment_id": comment_id,
            "success": new_status in DONE_STATUS_SET,
        }
    )
    if show_raw_response:
        result["raw_response"] = update_resp
    return result


def summarize_batch(results: List[Dict[str, Any]]) -> Dict[str, int]:
    summary = {"total": len(results), "success": 0, "skipped": 0, "failed": 0, "dry_run": 0}
    for r in results:
        if r.get("error"):
            summary["failed"] += 1
        elif r.get("dry_run"):
            summary["dry_run"] += 1
        elif r.get("skipped"):
            summary["skipped"] += 1
        elif r.get("success"):
            summary["success"] += 1
        else:
            summary["failed"] += 1
    return summary


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = parse_args()
    config = load_config(args)
    payload = load_payload(args, config)
    client = load_tapd_client()

    targets = collect_targets(payload)
    results: List[Dict[str, Any]] = []

    for target in targets:
        try:
            results.append(
                process_one_target(
                    client,
                    payload,
                    target,
                    dry_run=args.dry_run,
                    show_raw_response=args.show_raw_response,
                )
            )
        except Exception as exc:
            results.append(
                {
                    "error": str(exc),
                    "target": target,
                    "dry_run": bool(args.dry_run),
                }
            )

    if len(results) == 1 and "error" not in results[0] and not results[0].get("batch_forced"):
        out = results[0]
        if args.dry_run:
            out["dry_run"] = True
        print(json.dumps(out, ensure_ascii=False))
        return

    summary = summarize_batch(results)
    print(
        json.dumps(
            {
                "batch": True,
                "dry_run": bool(args.dry_run),
                "workspace_id": payload["workspace_id"],
                "target_status": payload["status"],
                "summary": summary,
                "results": results,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
