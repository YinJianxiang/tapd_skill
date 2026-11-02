"""
查询 / 更新 TAPD「测试子需求」状态。

list：按处理人、父需求、状态、未上线等条件列出测试子需求。
update：将测试子需求 status 改为中文状态名或英文 key（如 已上线 / status_19）。
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
QA_FIELDS = "id,name,status,owner,parent_id,iteration_id,workitem_type_id,modified,created"
ONLINE_STATUS_LABEL = "已上线"
CANCELLED_STATUS_LABEL = "需求作废"


def workspace_root() -> Path:
    """Locate local configuration without depending on an IDE directory."""
    candidates = list(Path(__file__).resolve().parents) + [Path.cwd()]
    for parent in candidates:
        if (parent / ".tapd").is_dir():
            return parent
    for parent in candidates:
        if (parent / "skills" / "tapd-plus").is_dir():
            return parent
    raise RuntimeError("Cannot locate project root (.tapd or skills/tapd-plus)")


DEFAULT_CONFIG_FILE = workspace_root() / ".tapd" / "project_config.json"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if not spec or not spec.loader:
        raise RuntimeError(f"加载模块失败: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def find_skill_script(*parts: str) -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent.joinpath(*parts)
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"未找到脚本: {'/'.join(parts)}")


def load_tapd_client():
    return load_module(
        "tapd_client_stdlib",
        find_skill_script("tapd-plus", "scripts", "tapd_client_stdlib.py"),
    )


def load_init_mod():
    return load_module(
        "init_tapd_config",
        find_skill_script("tapd-config-init", "scripts", "init_tapd_config.py"),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="查询或更新 TAPD 测试子需求状态")
    parser.add_argument("--payload-file", help="UTF-8 JSON，推荐传中文")
    parser.add_argument("--config-file", help="项目配置，默认 .tapd/project_config.json")
    parser.add_argument("--workspace-id", dest="workspace_id")
    parser.add_argument(
        "--action",
        choices=("list", "update", "status-map"),
        help="list=查询，update=更新状态，status-map=仅返回状态映射",
    )
    parser.add_argument("--story-id", dest="story_id", help="测试子需求 ID（更新时必填；查询时可指定）")
    parser.add_argument("--story-ids", dest="story_ids", help="多个测试子需求 ID，逗号分隔")
    parser.add_argument("--story-url", dest="story_url", help="测试子需求或父需求链接")
    parser.add_argument("--story-name", dest="story_name", help="按标题匹配（唯一命中时可用）")
    parser.add_argument("--parent-id", dest="parent_id", help="按父需求 ID 过滤")
    parser.add_argument("--owner", help="处理人昵称；默认当前用户 / defaults.reporter")
    parser.add_argument("--status", help="目标或过滤状态：中文名或英文 key，如 已上线 / status_19")
    parser.add_argument(
        "--not-online",
        action="store_true",
        help="仅列出未上线（排除「已上线」；默认同时排除「需求作废」）",
    )
    parser.add_argument(
        "--include-cancelled",
        action="store_true",
        help="not-online 时保留「需求作废」",
    )
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--dry-run", action="store_true", help="update 时只预览不提交")
    parser.add_argument("--show-raw-response", action="store_true")
    return parser.parse_args()


def read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as f:
        obj = json.load(f)
    if not isinstance(obj, dict):
        raise ValueError(f"JSON 顶层必须是对象: {path}")
    return obj


def load_config(args: argparse.Namespace) -> Dict[str, Any]:
    path = Path(args.config_file).resolve() if args.config_file else DEFAULT_CONFIG_FILE
    if not path.is_file():
        return {}
    return read_json(path)


def load_payload(args: argparse.Namespace) -> Dict[str, Any]:
    if not args.payload_file:
        return {}
    return read_json(Path(args.payload_file).resolve())


def pick(payload: Dict[str, Any], config: Dict[str, Any], key: str, default: Any = None) -> Any:
    if key in payload and payload.get(key) not in (None, ""):
        return payload.get(key)
    defaults = config.get("defaults") if isinstance(config.get("defaults"), dict) else {}
    if key in defaults and defaults.get(key) not in (None, ""):
        return defaults.get(key)
    if key in config and config.get(key) not in (None, ""):
        return config.get(key)
    return default


def parse_story_id_from_url(url: str) -> str:
    text = (url or "").strip()
    if not text:
        return ""
    m = re.search(r"/stories/view/(\d+)", text)
    return m.group(1) if m else ""


def split_ids(raw: Any) -> List[str]:
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


def resolve_workspace_id(args: argparse.Namespace, payload: Dict[str, Any], config: Dict[str, Any]) -> str:
    wid = (
        str(args.workspace_id or "").strip()
        or str(payload.get("workspace_id") or "").strip()
        or str(config.get("workspace_id") or "").strip()
    )
    if not wid:
        raise ValueError("缺少 workspace_id（配置 .tapd/project_config.json 或参数传入）")
    return wid


def resolve_owner(
    args: argparse.Namespace,
    payload: Dict[str, Any],
    config: Dict[str, Any],
    client: Any,
) -> str:
    explicit = (
        str(args.owner or "").strip()
        or str(payload.get("owner") or "").strip()
        or str(payload.get("qa_owner") or "").strip()
    )
    if explicit:
        return explicit
    env_nick = str(os.environ.get("CURRENT_USER_NICK") or "").strip()
    if env_nick:
        return env_nick
    # tapd_env 已由 client 加载到环境
    if hasattr(client, "load_local_tapd_env"):
        client.load_local_tapd_env()
        env_nick = str(os.environ.get("CURRENT_USER_NICK") or "").strip()
        if env_nick:
            return env_nick
    reporter = pick(payload, config, "reporter", "")
    return str(reporter or "").strip()


def find_qa_type_id(type_map: Dict[str, Dict[str, Any]]) -> str:
    for tid, info in type_map.items():
        name = str(info.get("name") or "")
        english = str(info.get("english_name") or "").strip().upper()
        if english == "QA" or "测试" in name:
            return tid
    raise ValueError("项目未配置「测试子需求」类别（workitem_type）")


def unwrap_stories(resp: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for row in resp.get("data") or []:
        if not isinstance(row, dict):
            continue
        item = row.get("Story", row)
        if isinstance(item, dict) and item.get("id"):
            rows.append(item)
    return rows


def status_maps(client: Any, workspace_id: str, qa_type_id: str) -> Tuple[Dict[str, str], Dict[str, str], Dict[str, str]]:
    """返回 (en->zh, zh->en, last_steps en->zh)。"""
    if hasattr(client, "get_workflows_status_map"):
        sm_resp = client.get_workflows_status_map(int(workspace_id), qa_type_id, system="story")
    else:
        sm_resp = client.request(
            "GET",
            "workflows/status_map",
            params={
                "workspace_id": workspace_id,
                "system": "story",
                "workitem_type_id": qa_type_id,
            },
        )
    en_to_zh: Dict[str, str] = {}
    data = sm_resp.get("data") or {}
    if isinstance(data, dict):
        for k, v in data.items():
            en_to_zh[str(k)] = str(v)
    zh_to_en = {v: k for k, v in en_to_zh.items()}

    if hasattr(client, "get_workflows_last_steps"):
        ls_resp = client.get_workflows_last_steps(
            int(workspace_id), system="story", workitem_type_id=qa_type_id
        )
    else:
        ls_resp = client.request(
            "GET",
            "workflows/last_steps",
            params={
                "workspace_id": workspace_id,
                "system": "story",
                "workitem_type_id": qa_type_id,
            },
        )
    last_steps: Dict[str, str] = {}
    ls_data = ls_resp.get("data") or {}
    if isinstance(ls_data, dict):
        for k, v in ls_data.items():
            last_steps[str(k)] = str(v if not isinstance(v, dict) else v.get("name") or k)
    return en_to_zh, zh_to_en, last_steps


def resolve_status_key(
    raw: str,
    en_to_zh: Dict[str, str],
    zh_to_en: Dict[str, str],
) -> str:
    text = str(raw or "").strip()
    if not text:
        raise ValueError("status 不能为空")
    if text in en_to_zh:
        return text
    if text in zh_to_en:
        return zh_to_en[text]
    # 模糊：去掉空白后比中文
    compact = text.replace(" ", "")
    for zh, en in zh_to_en.items():
        if zh.replace(" ", "") == compact:
            return en
    known = "、".join(f"{zh}({en})" for en, zh in sorted(en_to_zh.items(), key=lambda x: x[0]))
    raise ValueError(f"未知状态「{text}」。可选：{known}")


def story_url(workspace_id: str, story_id: str) -> str:
    return f"{DEFAULT_TAPD_BASE_URL}/{workspace_id}/prong/stories/view/{story_id}"


def brief_story(
    story: Dict[str, Any],
    workspace_id: str,
    en_to_zh: Dict[str, str],
    type_map: Dict[str, Dict[str, Any]],
    init_mod: Any,
) -> Dict[str, Any]:
    sid = str(story.get("id") or "").strip()
    status = str(story.get("status") or "").strip()
    type_id = str(story.get("workitem_type_id") or "").strip()
    type_info = type_map.get(type_id) or {}
    owner = ""
    if hasattr(init_mod, "first_owner"):
        owner = init_mod.first_owner(story.get("owner"))
    else:
        owner = str(story.get("owner") or "").split(";")[0].strip()
    return {
        "id": sid,
        "name": str(story.get("name") or "").strip(),
        "status": status,
        "status_label": en_to_zh.get(status, status),
        "owner": owner,
        "parent_id": str(story.get("parent_id") or "").strip(),
        "iteration_id": str(story.get("iteration_id") or "").strip(),
        "workitem_type_id": type_id,
        "workitem_type_name": str(type_info.get("name") or "").strip(),
        "modified": str(story.get("modified") or "").strip(),
        "url": story_url(workspace_id, sid) if sid else "",
        "is_online": en_to_zh.get(status) == ONLINE_STATUS_LABEL,
        "is_cancelled": en_to_zh.get(status) == CANCELLED_STATUS_LABEL,
    }


def fetch_qa_stories(
    client: Any,
    workspace_id: str,
    *,
    qa_type_id: str,
    story_ids: Optional[List[str]] = None,
    parent_id: str = "",
    owner: str = "",
    status_key: str = "",
    limit: int = 50,
) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {
        "workspace_id": workspace_id,
        "entity_type": "stories",
        "workitem_type_id": qa_type_id,
        "fields": QA_FIELDS,
        "limit": max(1, min(int(limit or 50), 200)),
        "page": 1,
    }
    if story_ids:
        long_ids = [client.to_long_id(sid, int(workspace_id)) for sid in story_ids]
        params["id"] = ",".join(long_ids)
    if parent_id:
        params["parent_id"] = client.to_long_id(parent_id, int(workspace_id))
    if owner:
        params["owner"] = owner
    if status_key:
        params["status"] = status_key

    if hasattr(client, "get_stories"):
        resp = client.get_stories(int(workspace_id), params)
    else:
        resp = client.request("GET", "stories", params=params)
    return unwrap_stories(resp)


def action_status_map(client: Any, workspace_id: str, qa_type_id: str) -> Dict[str, Any]:
    en_to_zh, _, last_steps = status_maps(client, workspace_id, qa_type_id)
    return {
        "action": "status-map",
        "workspace_id": workspace_id,
        "workitem_type_id": qa_type_id,
        "status_map": en_to_zh,
        "last_steps": last_steps,
        "message": "测试子需求工作流状态映射",
    }


def action_list(
    client: Any,
    init_mod: Any,
    workspace_id: str,
    qa_type_id: str,
    type_map: Dict[str, Dict[str, Any]],
    *,
    story_ids: List[str],
    parent_id: str,
    owner: str,
    status_filter: str,
    not_online: bool,
    include_cancelled: bool,
    limit: int,
) -> Dict[str, Any]:
    en_to_zh, zh_to_en, last_steps = status_maps(client, workspace_id, qa_type_id)
    status_key = ""
    if status_filter:
        status_key = resolve_status_key(status_filter, en_to_zh, zh_to_en)

    stories = fetch_qa_stories(
        client,
        workspace_id,
        qa_type_id=qa_type_id,
        story_ids=story_ids or None,
        parent_id=parent_id,
        owner=owner,
        status_key=status_key if not not_online else "",
        limit=limit,
    )

    items = [brief_story(s, workspace_id, en_to_zh, type_map, init_mod) for s in stories]

    if not_online:
        filtered = []
        for item in items:
            if item.get("is_online"):
                continue
            if item.get("is_cancelled") and not include_cancelled:
                continue
            filtered.append(item)
        items = filtered
    elif status_key:
        items = [i for i in items if i.get("status") == status_key]

    return {
        "action": "list",
        "workspace_id": workspace_id,
        "filters": {
            "owner": owner,
            "parent_id": parent_id,
            "status": status_filter,
            "not_online": not_online,
            "include_cancelled": include_cancelled,
            "story_ids": story_ids,
        },
        "status_map": en_to_zh,
        "last_steps": last_steps,
        "count": len(items),
        "items": items,
    }


def resolve_update_targets(
    client: Any,
    init_mod: Any,
    workspace_id: str,
    qa_type_id: str,
    type_map: Dict[str, Dict[str, Any]],
    *,
    story_ids: List[str],
    story_name: str,
    parent_id: str,
) -> List[Dict[str, Any]]:
    if story_ids:
        rows = fetch_qa_stories(
            client,
            workspace_id,
            qa_type_id=qa_type_id,
            story_ids=story_ids,
            limit=max(len(story_ids), 10),
        )
        if not rows:
            raise ValueError(f"未找到测试子需求: {','.join(story_ids)}")
        return rows

    if story_name:
        params = {
            "workspace_id": workspace_id,
            "entity_type": "stories",
            "workitem_type_id": qa_type_id,
            "name": story_name,
            "fields": QA_FIELDS,
            "limit": 20,
        }
        if parent_id:
            params["parent_id"] = client.to_long_id(parent_id, int(workspace_id))
        resp = client.get_stories(int(workspace_id), params) if hasattr(client, "get_stories") else client.request("GET", "stories", params=params)
        rows = unwrap_stories(resp)
        exact = [r for r in rows if str(r.get("name") or "").strip() == story_name]
        candidates = exact or rows
        if len(candidates) == 0:
            raise ValueError(f"未找到标题为「{story_name}」的测试子需求")
        if len(candidates) > 1:
            raise ValueError(
                "标题命中多条测试子需求，请改用 story_id："
                + "、".join(str(r.get("id")) for r in candidates[:5])
            )
        return candidates

    if parent_id:
        rows = fetch_qa_stories(
            client,
            workspace_id,
            qa_type_id=qa_type_id,
            parent_id=parent_id,
            limit=20,
        )
        if not rows:
            raise ValueError(f"父需求 {parent_id} 下没有测试子需求")
        if len(rows) > 1:
            raise ValueError(
                "父需求下有多条测试子需求，请指定 story_id："
                + "、".join(str(r.get("id")) for r in rows)
            )
        return rows

    raise ValueError("update 需要 --story-id / --story-ids / --story-name / --parent-id 之一")


def action_update(
    client: Any,
    init_mod: Any,
    workspace_id: str,
    qa_type_id: str,
    type_map: Dict[str, Dict[str, Any]],
    *,
    story_ids: List[str],
    story_name: str,
    parent_id: str,
    target_status: str,
    dry_run: bool,
    show_raw: bool,
) -> Dict[str, Any]:
    if not target_status:
        raise ValueError("update 需要 --status（中文名或英文 key）")

    en_to_zh, zh_to_en, _ = status_maps(client, workspace_id, qa_type_id)
    target_key = resolve_status_key(target_status, en_to_zh, zh_to_en)
    targets = resolve_update_targets(
        client,
        init_mod,
        workspace_id,
        qa_type_id,
        type_map,
        story_ids=story_ids,
        story_name=story_name,
        parent_id=parent_id,
    )

    results: List[Dict[str, Any]] = []
    for story in targets:
        before = brief_story(story, workspace_id, en_to_zh, type_map, init_mod)
        sid = before["id"]
        current = before["status"]
        item: Dict[str, Any] = {
            "id": sid,
            "name": before["name"],
            "url": before["url"],
            "from_status": current,
            "from_status_label": before["status_label"],
            "to_status": target_key,
            "to_status_label": en_to_zh.get(target_key, target_key),
        }
        if current == target_key:
            item["action"] = "skipped_same"
            item["message"] = "已是目标状态"
            results.append(item)
            continue
        if dry_run:
            item["action"] = "would_update"
            results.append(item)
            continue

        long_id = client.to_long_id(sid, int(workspace_id))
        if hasattr(client, "update_story"):
            resp = client.update_story(int(workspace_id), long_id, {"status": target_key})
        else:
            resp = client.request(
                "POST",
                "stories",
                data={"workspace_id": workspace_id, "id": long_id, "status": target_key},
            )
        if resp.get("status") != 1:
            item["action"] = "failed"
            item["error"] = resp.get("info") or resp
            if show_raw:
                item["raw"] = resp
            results.append(item)
            continue

        item["action"] = "updated"
        if show_raw:
            item["raw"] = resp
        # 回读最新状态（若响应含 Story）
        data = resp.get("data")
        updated = {}
        if isinstance(data, dict):
            updated = data.get("Story", data) if "Story" in data or data.get("id") else {}
        if isinstance(updated, dict) and updated.get("status"):
            item["status"] = str(updated.get("status"))
            item["status_label"] = en_to_zh.get(item["status"], item["status"])
        else:
            item["status"] = target_key
            item["status_label"] = en_to_zh.get(target_key, target_key)
        results.append(item)

    ok = all(r.get("action") in ("updated", "skipped_same", "would_update") for r in results)
    return {
        "action": "update",
        "workspace_id": workspace_id,
        "dry_run": dry_run,
        "target_status": target_key,
        "target_status_label": en_to_zh.get(target_key, target_key),
        "count": len(results),
        "results": results,
        "ok": ok,
    }


def main() -> int:
    args = parse_args()
    payload = load_payload(args)
    config = load_config(args)
    client = load_tapd_client()
    if hasattr(client, "load_local_tapd_env"):
        client.load_local_tapd_env()
    init_mod = load_init_mod()

    try:
        workspace_id = resolve_workspace_id(args, payload, config)
        type_map = init_mod.fetch_workitem_type_map(client, workspace_id)
        qa_type_id = find_qa_type_id(type_map)

        action = (
            str(args.action or "").strip()
            or str(payload.get("action") or "").strip()
            or "list"
        ).lower()
        if action in ("get", "query", "查询"):
            action = "list"
        if action in ("set", "set-status", "更新", "改状态"):
            action = "update"
        if action in ("map", "statuses", "状态映射"):
            action = "status-map"

        story_ids = split_ids(args.story_ids or payload.get("story_ids"))
        single = (
            str(args.story_id or "").strip()
            or str(payload.get("story_id") or "").strip()
            or parse_story_id_from_url(str(args.story_url or payload.get("story_url") or ""))
        )
        if single and single not in story_ids:
            story_ids.insert(0, single)

        parent_id = (
            str(args.parent_id or "").strip()
            or str(payload.get("parent_id") or "").strip()
        )
        story_name = (
            str(args.story_name or "").strip()
            or str(payload.get("story_name") or "").strip()
            or str(payload.get("story") or "").strip()
        )
        status_raw = str(args.status or payload.get("status") or "").strip()
        not_online = bool(args.not_online or payload.get("not_online"))
        include_cancelled = bool(args.include_cancelled or payload.get("include_cancelled"))
        dry_run = bool(args.dry_run or payload.get("dry_run"))
        limit = int(payload.get("limit") or args.limit or 50)

        if action == "status-map":
            out = action_status_map(client, workspace_id, qa_type_id)
        elif action == "list":
            owner = resolve_owner(args, payload, config, client)
            # list 默认带 owner；显式传空字符串可查全部
            if "owner" in payload and payload.get("owner") in ("", None):
                owner = ""
            elif args.owner == "":
                owner = ""
            out = action_list(
                client,
                init_mod,
                workspace_id,
                qa_type_id,
                type_map,
                story_ids=story_ids,
                parent_id=parent_id,
                owner=owner,
                status_filter=status_raw,
                not_online=not_online,
                include_cancelled=include_cancelled,
                limit=limit,
            )
        elif action == "update":
            out = action_update(
                client,
                init_mod,
                workspace_id,
                qa_type_id,
                type_map,
                story_ids=story_ids,
                story_name=story_name,
                parent_id=parent_id,
                target_status=status_raw,
                dry_run=dry_run,
                show_raw=bool(args.show_raw_response),
            )
        else:
            raise ValueError(f"不支持的 action: {action}（list / update / status-map）")

        print(json.dumps(out, ensure_ascii=False, indent=2))
        if action == "update" and not out.get("ok", True):
            return 1
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
