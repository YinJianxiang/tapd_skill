"""
初始化并升级 TAPD 本地配置：
- 不存在则创建
- 存在则递归补齐缺失字段（不覆盖已有值）
- 可选 --payload-file / --set 显式更新指定字段（会覆盖）
- 可选 --story / --story-id / --story-name：按需求写入 link_story，
  并按子需求类型回填 reporter / 前端 / 后端处理人
- defaults.name 由调用方语义简化后经 --set / payload 传入（脚本不做死映射）
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


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


DEFAULT_TEMPLATE = workspace_root() / ".tapd" / "project_config.template.json"
DEFAULT_TARGET = workspace_root() / ".tapd" / "project_config.json"
DEFAULT_ENV_TEMPLATE = workspace_root() / ".tapd" / "tapd_env.template.json"
DEFAULT_ENV_TARGET = workspace_root() / ".tapd" / "tapd_env.json"

STORY_PAYLOAD_KEYS = ("story", "story_id", "story_name", "story_url")
STORY_FIELDS = "id,name,status,owner,iteration_id,parent_id,children_id,workitem_type_id,version,module"
CHILD_FIELDS = "id,name,owner,parent_id,workitem_type_id,developer,module"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="初始化/升级 TAPD 项目配置")
    parser.add_argument("--template", default=str(DEFAULT_TEMPLATE), help="模板 JSON 路径")
    parser.add_argument("--target", default=str(DEFAULT_TARGET), help="目标配置 JSON 路径")
    parser.add_argument(
        "--payload-file",
        help="UTF-8 JSON 文件，递归合并并覆盖 project_config.json 中对应字段；"
        "也可含 story / story_id / story_name / story_url",
    )
    parser.add_argument(
        "--set",
        action="append",
        default=[],
        dest="set_items",
        metavar="KEY=VAL",
        help="点路径更新，如 defaults.name=批创；VAL 可为 JSON 字面量；可多次",
    )
    parser.add_argument(
        "--env-payload-file",
        help="UTF-8 JSON 文件，递归合并并覆盖 tapd_env.json 中对应字段",
    )
    parser.add_argument(
        "--story",
        help="需求标题、需求 ID 或 TAPD 需求链接（优先于 payload 中的 story）",
    )
    parser.add_argument("--story-id", dest="story_id", help="需求 ID（长 id 或短 id）")
    parser.add_argument("--story-name", dest="story_name", help="需求标题（精确优先，其次唯一模糊）")
    parser.add_argument("--story-url", dest="story_url", help="TAPD 需求链接")
    return parser.parse_args()


def read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as f:
        obj = json.load(f)
    if not isinstance(obj, dict):
        raise ValueError(f"JSON 顶层必须是对象: {path}")
    return obj


def merge_missing_keys(
    target: Dict[str, Any], template: Dict[str, Any], prefix: str = ""
) -> Tuple[Dict[str, Any], List[str]]:
    added: List[str] = []
    for key, tpl_val in template.items():
        key_path = f"{prefix}.{key}" if prefix else key
        if key not in target:
            target[key] = copy.deepcopy(tpl_val)
            added.append(key_path)
            continue

        cur_val = target[key]
        if isinstance(cur_val, dict) and isinstance(tpl_val, dict):
            _, nested_added = merge_missing_keys(cur_val, tpl_val, key_path)
            added.extend(nested_added)
    return target, added


def apply_updates(
    target: Dict[str, Any], updates: Dict[str, Any], prefix: str = ""
) -> Tuple[Dict[str, Any], List[str]]:
    updated: List[str] = []
    for key, new_val in updates.items():
        key_path = f"{prefix}.{key}" if prefix else key
        if isinstance(new_val, dict):
            if key not in target or not isinstance(target[key], dict):
                if key in target and target[key] == new_val:
                    continue
                target[key] = copy.deepcopy(new_val)
                updated.append(key_path)
                continue
            _, nested_updated = apply_updates(target[key], new_val, key_path)
            updated.extend(nested_updated)
            continue

        if key not in target or target[key] != new_val:
            target[key] = new_val
            updated.append(key_path)
    return target, updated


def parse_set_value(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def build_updates_from_set_items(set_items: List[str]) -> Dict[str, Any]:
    updates: Dict[str, Any] = {}
    for item in set_items:
        if "=" not in item:
            raise ValueError(f"无效的 --set 格式（应为 KEY=VAL）: {item}")
        path, raw_val = item.split("=", 1)
        path = path.strip()
        if not path:
            raise ValueError(f"无效的 --set 路径: {item}")
        set_nested_value(updates, path.split("."), parse_set_value(raw_val.strip()))
    return updates


def set_nested_value(root: Dict[str, Any], keys: List[str], value: Any) -> None:
    cur = root
    for key in keys[:-1]:
        nxt = cur.get(key)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[key] = nxt
        cur = nxt
    cur[keys[-1]] = value


def extract_story_ref(data: Dict[str, Any]) -> Dict[str, str]:
    ref: Dict[str, str] = {}
    for key in STORY_PAYLOAD_KEYS:
        val = str(data.get(key) or "").strip()
        if val:
            ref[key] = val
    return ref


def load_update_payload(args: argparse.Namespace) -> Tuple[Dict[str, Any], Dict[str, str]]:
    payload: Dict[str, Any] = {}
    story_ref: Dict[str, str] = {}
    if args.payload_file:
        payload_path = Path(args.payload_file).resolve()
        if not payload_path.is_file():
            raise FileNotFoundError(f"payload 文件不存在: {payload_path}")
        file_payload = read_json(payload_path)
        story_ref.update(extract_story_ref(file_payload))
        config_payload = {
            k: v for k, v in file_payload.items() if k not in STORY_PAYLOAD_KEYS
        }
        payload, _ = apply_updates(payload, config_payload)
    if args.set_items:
        set_payload = build_updates_from_set_items(args.set_items)
        payload, _ = apply_updates(payload, set_payload)
    cli_ref = {
        "story": str(getattr(args, "story", "") or "").strip(),
        "story_id": str(getattr(args, "story_id", "") or "").strip(),
        "story_name": str(getattr(args, "story_name", "") or "").strip(),
        "story_url": str(getattr(args, "story_url", "") or "").strip(),
    }
    for key, val in cli_ref.items():
        if val:
            story_ref[key] = val
    return payload, story_ref


def resolve_status(existed: bool, added: List[str], updated: List[str]) -> str:
    if not existed:
        if updated:
            return "created_and_updated"
        return "created"
    if added and updated:
        return "merged_and_updated"
    if added:
        return "merged"
    if updated:
        return "updated"
    return "skipped"


def write_json(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def init_project_config(
    target_path: Path,
    template_path: Path,
    update_payload: Dict[str, Any] | None = None,
) -> Tuple[str, List[str], List[str]]:
    if not template_path.is_file():
        raise FileNotFoundError(f"模板文件不存在: {template_path}")

    template_obj = read_json(template_path)
    existed = target_path.exists()
    if not existed:
        config_obj = copy.deepcopy(template_obj)
        added = sorted(f"{k}" for k in template_obj.keys())
    else:
        config_obj = read_json(target_path)
        config_obj, added = merge_missing_keys(config_obj, template_obj)

    updated: List[str] = []
    if update_payload:
        config_obj, updated = apply_updates(config_obj, update_payload)

    status = resolve_status(existed, added, updated)
    if status != "skipped":
        write_json(target_path, config_obj)
    return status, added, updated


def init_env_config(env_payload: Dict[str, Any] | None = None) -> Tuple[str, List[str], List[str]]:
    if not DEFAULT_ENV_TEMPLATE.is_file():
        return "skipped", [], []

    existed = DEFAULT_ENV_TARGET.exists()
    if not existed:
        env_obj = read_json(DEFAULT_ENV_TEMPLATE)
        added = sorted(f"{k}" for k in env_obj.keys())
    else:
        env_obj = read_json(DEFAULT_ENV_TARGET)
        env_tpl = read_json(DEFAULT_ENV_TEMPLATE)
        env_obj, added = merge_missing_keys(env_obj, env_tpl)

    updated: List[str] = []
    if env_payload:
        env_obj, updated = apply_updates(env_obj, env_payload)

    status = resolve_status(existed, added, updated)
    if status != "skipped":
        write_json(DEFAULT_ENV_TARGET, env_obj)
    return status, added, updated


def find_tapd_client_script() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "tapd-plus" / "scripts" / "tapd_client_stdlib.py"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("未找到 tapd 客户端脚本: tapd-plus/scripts/tapd_client_stdlib.py")


def load_tapd_client():
    script_path = find_tapd_client_script()
    spec = importlib.util.spec_from_file_location("tapd_client_stdlib", script_path)
    if not spec or not spec.loader:
        raise RuntimeError("加载 tapd_client_stdlib 失败")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def extract_entity(resp: Dict[str, Any], key: str) -> Dict[str, Any]:
    data = resp.get("data")
    if isinstance(data, dict):
        entity = data.get(key, data)
        if isinstance(entity, dict):
            return entity
    if isinstance(data, list) and data:
        row = data[0]
        if isinstance(row, dict):
            entity = row.get(key, row)
            if isinstance(entity, dict):
                return entity
    return {}


def unwrap_stories(resp: Dict[str, Any]) -> List[Dict[str, Any]]:
    stories: List[Dict[str, Any]] = []
    for row in resp.get("data", []) or []:
        story = row.get("Story", row) if isinstance(row, dict) else {}
        if isinstance(story, dict) and str(story.get("id", "")).strip():
            stories.append(story)
    return stories


def parse_story_url(url: str) -> Tuple[str, str]:
    text = (url or "").strip()
    if not text:
        return "", ""
    ws = ""
    sid = ""
    m = re.search(r"tapd\.cn/(\d+)/", text, flags=re.I)
    if m:
        ws = m.group(1)
    for pat in (r"/stories/view/(\d+)", r"story_id=(\d+)"):
        m = re.search(pat, text)
        if m:
            sid = m.group(1)
            break
    return ws, sid


def looks_like_story_id(text: str) -> bool:
    s = str(text or "").strip()
    return bool(s.isdigit() and len(s) >= 6)


def first_owner(raw: Any) -> str:
    parts = [p.strip() for p in str(raw or "").replace("；", ";").split(";")]
    for part in parts:
        if part:
            return part
    return ""


def parse_children_ids(raw: Any) -> List[str]:
    ids: List[str] = []
    for part in str(raw or "").split("|"):
        sid = part.strip()
        if sid and sid not in ids:
            ids.append(sid)
    return ids


def is_root_story(story: Dict[str, Any]) -> bool:
    parent_id = str(story.get("parent_id") or "").strip()
    return parent_id in ("", "0")


def type_text(type_info: Dict[str, Any]) -> str:
    name = str(type_info.get("name") or "").strip()
    english = str(type_info.get("english_name") or "").strip()
    return f"{name} {english}".strip()


def is_demand_type(story: Dict[str, Any], type_map: Dict[str, Dict[str, Any]]) -> bool:
    type_id = str(story.get("workitem_type_id") or "").strip()
    info = type_map.get(type_id) or {}
    blob = type_text(info).lower()
    if not blob:
        return is_root_story(story)
    if any(token in blob for token in ("子需求", "前端", "后端", "测试")):
        return False
    english = str(info.get("english_name") or "").strip().upper()
    if english in {"FE", "BE", "QA"}:
        return False
    return "需求" in str(info.get("name") or "") or english == "STORY"


def classify_child_role(
    story: Dict[str, Any],
    type_map: Dict[str, Dict[str, Any]],
) -> str:
    type_id = str(story.get("workitem_type_id") or "").strip()
    info = type_map.get(type_id) or {}
    type_name = str(info.get("name") or "")
    english = str(info.get("english_name") or "").strip().upper()
    story_name = str(story.get("name") or "")

    if english == "QA" or "测试" in type_name:
        return "测试"
    if english == "FE" or "前端" in type_name:
        return "前端"
    if english == "BE" or "后端" in type_name:
        return "后端"
    if "测试" in story_name:
        return "测试"
    if "前端" in story_name:
        return "前端"
    if "后端" in story_name:
        return "后端"
    return ""


def story_brief(
    story: Dict[str, Any],
    workspace_id: str,
    type_map: Dict[str, Dict[str, Any]],
    *,
    role: str = "",
) -> Dict[str, Any]:
    sid = str(story.get("id") or "").strip()
    type_id = str(story.get("workitem_type_id") or "").strip()
    info = type_map.get(type_id) or {}
    return {
        "id": sid,
        "name": str(story.get("name") or "").strip(),
        "owner": first_owner(story.get("owner")),
        "parent_id": str(story.get("parent_id") or "").strip(),
        "workitem_type_id": type_id,
        "workitem_type_name": str(info.get("name") or "").strip(),
        "role": role,
        "url": f"https://www.tapd.cn/{workspace_id}/prong/stories/view/{sid}" if sid else "",
    }


def fetch_workitem_type_map(client: Any, workspace_id: str) -> Dict[str, Dict[str, Any]]:
    resp = client.request(
        "GET",
        "workitem_types",
        params={"workspace_id": workspace_id, "limit": 50},
    )
    mapping: Dict[str, Dict[str, Any]] = {}
    for row in resp.get("data", []) or []:
        item = row.get("WorkitemType", row) if isinstance(row, dict) else {}
        if not isinstance(item, dict):
            continue
        tid = str(item.get("id") or "").strip()
        if tid:
            mapping[tid] = item
    return mapping


def fetch_story(client: Any, workspace_id: str, story_id: str) -> Dict[str, Any]:
    long_id = client.to_long_id(story_id, int(workspace_id))
    resp = client.request(
        "GET",
        "stories",
        params={
            "workspace_id": workspace_id,
            "id": long_id,
            "limit": 1,
            "fields": STORY_FIELDS,
        },
    )
    story = extract_entity(resp, "Story")
    if not story:
        raise ValueError(f"未找到需求：{long_id}")
    return story


def fetch_iteration(
    client: Any, workspace_id: str, iteration_id: str
) -> Dict[str, Any]:
    """Resolve a TAPD iteration by the iteration_id stored on a story."""
    iid = str(iteration_id or "").strip()
    if not iid or iid in {"0", "null", "None"}:
        return {}
    resp = client.request(
        "GET",
        "iterations",
        params={
            "workspace_id": workspace_id,
            "id": iid,
            "limit": 1,
        },
    )
    return extract_entity(resp, "Iteration")


def story_version(story: Dict[str, Any]) -> str:
    """TAPD exposes the story version as the version field value."""
    return str(story.get("version") or "").strip()


def search_stories_by_name(client: Any, workspace_id: str, name: str) -> List[Dict[str, Any]]:
    name_norm = (name or "").strip()
    if not name_norm:
        return []
    queries = [name_norm]
    if not (name_norm.startswith("%") and name_norm.endswith("%")):
        queries.append(f"%{name_norm}%")
    seen: Dict[str, Dict[str, Any]] = {}
    for query in queries:
        resp = client.request(
            "GET",
            "stories",
            params={
                "workspace_id": workspace_id,
                "name": query,
                "limit": 50,
                "fields": STORY_FIELDS,
            },
        )
        for story in unwrap_stories(resp):
            sid = str(story.get("id") or "").strip()
            if sid:
                seen[sid] = story
        if seen:
            break
    return list(seen.values())


def fetch_child_stories(
    client: Any,
    workspace_id: str,
    parent: Dict[str, Any],
) -> List[Dict[str, Any]]:
    parent_id = str(parent.get("id") or "").strip()
    resp = client.request(
        "GET",
        "stories",
        params={
            "workspace_id": workspace_id,
            "parent_id": parent_id,
            "limit": 50,
            "fields": CHILD_FIELDS,
        },
    )
    children = unwrap_stories(resp)
    if children:
        return children

    child_ids = parse_children_ids(parent.get("children_id"))
    if not child_ids:
        return []
    resp = client.request(
        "GET",
        "stories",
        params={
            "workspace_id": workspace_id,
            "id": ",".join(child_ids),
            "limit": 50,
            "fields": CHILD_FIELDS,
        },
    )
    return unwrap_stories(resp)


def resolve_root_story(
    client: Any,
    workspace_id: str,
    story: Dict[str, Any],
    type_map: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    current = story
    seen: set[str] = set()
    while not is_demand_type(current, type_map) and not is_root_story(current):
        sid = str(current.get("id") or "").strip()
        parent_id = str(current.get("parent_id") or "").strip()
        if not parent_id or parent_id in ("0", sid) or parent_id in seen:
            break
        seen.add(sid)
        current = fetch_story(client, workspace_id, parent_id)
    return current


def pick_unique_story(
    query: str,
    stories: List[Dict[str, Any]],
    type_map: Dict[str, Dict[str, Any]],
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    query_norm = (query or "").strip()
    exact = [s for s in stories if str(s.get("name") or "").strip() == query_norm]
    pool = exact or stories
    roots = [s for s in pool if is_root_story(s) or is_demand_type(s, type_map)]
    preferred = roots or pool
    if len(preferred) == 1:
        return preferred[0], preferred
    return None, preferred


def candidate_list(
    stories: List[Dict[str, Any]],
    workspace_id: str,
    type_map: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    return [
        story_brief(s, workspace_id, type_map, role=classify_child_role(s, type_map))
        for s in stories[:8]
    ]


def resolve_story_from_ref(
    client: Any,
    workspace_id: str,
    story_ref: Dict[str, str],
    type_map: Dict[str, Dict[str, Any]],
) -> Tuple[str, Optional[Dict[str, Any]], List[Dict[str, Any]], str]:
    """返回 (status, story, candidates, source)。status: matched / need_confirm / not_found。"""
    raw_url = story_ref.get("story_url") or ""
    raw_id = story_ref.get("story_id") or ""
    raw_name = story_ref.get("story_name") or ""
    raw_story = story_ref.get("story") or ""

    url_ws, url_id = parse_story_url(raw_url)
    if url_ws and not workspace_id:
        workspace_id = url_ws
    if url_id and not raw_id:
        raw_id = url_id

    if not raw_id and looks_like_story_id(raw_story):
        raw_id = raw_story
        raw_story = ""
    if not raw_id:
        _, maybe_id = parse_story_url(raw_story)
        if maybe_id:
            raw_id = maybe_id
            raw_story = ""
    if not raw_name and raw_story:
        raw_name = raw_story

    if raw_id:
        story = fetch_story(client, workspace_id, raw_id)
        return "matched", resolve_root_story(client, workspace_id, story, type_map), [], "story_id"

    if raw_name:
        stories = search_stories_by_name(client, workspace_id, raw_name)
        if not stories:
            return "not_found", None, [], "story_name"
        picked, pool = pick_unique_story(raw_name, stories, type_map)
        if picked is None:
            return "need_confirm", None, candidate_list(pool, workspace_id, type_map), "story_name"
        source = (
            "story_name_exact"
            if str(picked.get("name") or "").strip() == raw_name.strip()
            else "story_name_fuzzy"
        )
        return (
            "matched",
            resolve_root_story(client, workspace_id, picked, type_map),
            [],
            source,
        )

    return "not_found", None, [], "story"


def map_child_owners(
    children: List[Dict[str, Any]],
    type_map: Dict[str, Dict[str, Any]],
) -> Tuple[Dict[str, str], List[Dict[str, Any]]]:
    owners = {"测试": "", "前端": "", "后端": ""}
    details: List[Dict[str, Any]] = []
    for child in children:
        role = classify_child_role(child, type_map)
        owner = first_owner(child.get("owner"))
        details.append(
            {
                "id": str(child.get("id") or "").strip(),
                "name": str(child.get("name") or "").strip(),
                "role": role or "未识别",
                "owner": owner,
                "workitem_type_id": str(child.get("workitem_type_id") or "").strip(),
                "workitem_type_name": str(
                    (type_map.get(str(child.get("workitem_type_id") or "").strip()) or {}).get("name")
                    or ""
                ).strip(),
            }
        )
        if role in owners and owner and not owners[role]:
            owners[role] = owner
    return owners, details


def build_story_update(
    story: Dict[str, Any],
    owners: Dict[str, str],
    iteration_name: str = "",
) -> Dict[str, Any]:
    version = story_version(story)
    story_name = str(story.get("name") or "").strip()
    return {
        "link_story": {
            "enabled": True,
            "story_id": str(story.get("id") or "").strip(),
            "story_name": story_name,
        },
        "defaults": {
            "reporter": owners.get("测试") or "",
            "version": version,
            "iteration": iteration_name,
        },
        "module_owner_map": {
            "前端": owners.get("前端") or "",
            "后端": owners.get("后端") or "",
        },
    }


def sync_story_config(
    *,
    target_path: Path,
    workspace_id: str,
    story_ref: Dict[str, str],
) -> Dict[str, Any]:
    if not any(str(v).strip() for v in story_ref.values()):
        return {"status": "skipped"}

    client = load_tapd_client()
    if not workspace_id:
        existed = read_json(target_path) if target_path.is_file() else {}
        workspace_id = str(existed.get("workspace_id") or "").strip()
        url_ws, _ = parse_story_url(story_ref.get("story") or story_ref.get("story_url") or "")
        if url_ws:
            workspace_id = url_ws
    if not workspace_id:
        raise ValueError("缺少 workspace_id：请先在 project_config.json 填写，或传入带项目 ID 的需求链接")

    type_map = fetch_workitem_type_map(client, workspace_id)
    status, story, candidates, source = resolve_story_from_ref(
        client, workspace_id, story_ref, type_map
    )
    if status == "not_found":
        query = (
            story_ref.get("story_name")
            or story_ref.get("story")
            or story_ref.get("story_id")
            or story_ref.get("story_url")
            or ""
        )
        return {
            "status": "not_found",
            "source": source,
            "query": query,
            "message": f"未找到需求：{query}",
        }
    if status == "need_confirm":
        return {
            "status": "need_confirm",
            "source": source,
            "query": story_ref.get("story_name") or story_ref.get("story") or "",
            "candidates": candidates,
            "message": "需求名称匹配到多条，请改用 story_id 或更精确的标题",
        }

    assert story is not None
    children = fetch_child_stories(client, workspace_id, story)
    owners, child_details = map_child_owners(children, type_map)
    iteration = fetch_iteration(
        client, workspace_id, str(story.get("iteration_id") or "")
    )
    iteration_name = str(iteration.get("name") or "").strip()
    payload = build_story_update(story, owners, iteration_name)
    if not str((read_json(target_path) if target_path.is_file() else {}).get("workspace_id") or "").strip():
        payload["workspace_id"] = workspace_id

    return {
        "status": "matched",
        "source": source,
        "workspace_id": workspace_id,
        "story": story_brief(story, workspace_id, type_map, role="需求"),
        "children": child_details,
        "owners": {
            "defaults.reporter": owners.get("测试") or "",
            "module_owner_map.前端": owners.get("前端") or "",
            "module_owner_map.后端": owners.get("后端") or "",
        },
        "defaults": {
            "version": story_version(story),
            "iteration": iteration_name,
        },
        "payload": payload,
        "message": (
            "未找到子需求，reporter / 前端 / 后端按空写入"
            if not children
            else "已按子需求处理人回填 reporter / 前端 / 后端"
        ),
    }


def main() -> None:
    args = parse_args()
    template_path = Path(args.template).resolve()
    target_path = Path(args.target).resolve()

    update_payload, story_ref = load_update_payload(args)
    env_payload: Dict[str, Any] | None = None
    if args.env_payload_file:
        env_payload_path = Path(args.env_payload_file).resolve()
        if not env_payload_path.is_file():
            raise FileNotFoundError(f"env payload 文件不存在: {env_payload_path}")
        env_payload = read_json(env_payload_path)

    story_sync: Dict[str, Any] = {"status": "skipped"}
    if any(str(v).strip() for v in story_ref.values()):
        config_obj = read_json(target_path) if target_path.is_file() else {}
        workspace_id = str(
            update_payload.get("workspace_id") or config_obj.get("workspace_id") or ""
        ).strip()
        story_sync = sync_story_config(
            target_path=target_path,
            workspace_id=workspace_id,
            story_ref=story_ref,
        )
        if story_sync.get("status") == "matched":
            story_payload = story_sync.pop("payload", {}) or {}
            combined: Dict[str, Any] = {}
            combined, _ = apply_updates(combined, story_payload)
            if update_payload:
                combined, _ = apply_updates(combined, update_payload)
            update_payload = combined
        elif story_sync.get("status") in {"need_confirm", "not_found"}:
            result = {
                "status": story_sync["status"],
                "template_path": str(template_path),
                "target_path": str(target_path),
                "story_sync": story_sync,
            }
            print(json.dumps(result, ensure_ascii=False))
            raise SystemExit(2)

    project_status, project_added, project_updated = init_project_config(
        target_path,
        template_path,
        update_payload if update_payload else None,
    )
    env_status, env_added, env_updated = init_env_config(env_payload)

    result = {
        "status": project_status,
        "template_path": str(template_path),
        "target_path": str(target_path),
        "added_keys": project_added,
        "updated_keys": project_updated,
        "tapd_env_path": str(DEFAULT_ENV_TARGET),
        "tapd_env_status": env_status,
        "tapd_env_added_keys": env_added,
        "tapd_env_updated_keys": env_updated,
        "story_sync": story_sync,
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
