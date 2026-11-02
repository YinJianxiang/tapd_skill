"""
在 TAPD 父需求下自动创建缺失的前/后/测试子需求，并用 parent_id 关联到父需求。
已存在对应角色的子需求则跳过，不重复创建。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_ROLES = ["前端", "后端", "测试"]
ROLE_ENGLISH = {"前端": "FE", "后端": "BE", "测试": "QA"}


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


def load_init_mod():
    return load_module(
        "init_tapd_config",
        find_skill_script("tapd-config-init", "scripts", "init_tapd_config.py"),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="自动创建 TAPD 子需求并关联到父需求")
    parser.add_argument("--payload-file", help="UTF-8 JSON，推荐传中文")
    parser.add_argument("--config-file", help="项目配置路径，默认 .tapd/project_config.json")
    parser.add_argument("--workspace-id", dest="workspace_id")
    parser.add_argument("--story", help="需求标题、ID 或链接")
    parser.add_argument("--story-id", dest="story_id")
    parser.add_argument("--story-name", dest="story_name")
    parser.add_argument("--story-url", dest="story_url")
    parser.add_argument(
        "--roles",
        help="要创建的角色，逗号分隔，默认 前端,后端,测试",
    )
    parser.add_argument("--frontend-owner", dest="frontend_owner", help="前端子需求处理人")
    parser.add_argument("--backend-owner", dest="backend_owner", help="后端子需求处理人")
    parser.add_argument("--qa-owner", dest="qa_owner", help="测试子需求处理人")
    parser.add_argument("--creator", help="创建人昵称，默认 tapd_env.current_user_nick")
    parser.add_argument("--dry-run", action="store_true", help="只预览，不创建")
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


def parse_roles(raw: Any) -> List[str]:
    if raw is None or raw == "":
        return list(DEFAULT_ROLES)
    if isinstance(raw, list):
        items = [str(x).strip() for x in raw]
    else:
        items = [p.strip() for p in str(raw).replace("，", ",").split(",")]
    alias = {"fe": "前端", "be": "后端", "qa": "测试", "测试人员": "测试"}
    roles: List[str] = []
    for item in items:
        if not item:
            continue
        role = alias.get(item.lower(), item)
        if role not in ROLE_ENGLISH:
            raise ValueError(f"不支持的子需求角色: {item}（仅 前端/后端/测试）")
        if role not in roles:
            roles.append(role)
    return roles or list(DEFAULT_ROLES)


def owner_from_payload(payload: Dict[str, Any], role: str) -> str:
    owners = payload.get("owners")
    if isinstance(owners, dict):
        val = str(owners.get(role) or "").strip()
        if val:
            return val
    keys = {
        "前端": ("frontend_owner", "前端"),
        "后端": ("backend_owner", "后端"),
        "测试": ("qa_owner", "reporter", "测试"),
    }
    for key in keys[role]:
        val = str(payload.get(key) or "").strip()
        if val:
            return val
    return ""


def owner_from_config(config: Dict[str, Any], role: str) -> str:
    if role == "测试":
        defaults = config.get("defaults") if isinstance(config.get("defaults"), dict) else {}
        return str(defaults.get("reporter") or "").strip()
    mapping = config.get("module_owner_map") if isinstance(config.get("module_owner_map"), dict) else {}
    return str(mapping.get(role) or "").strip()


def resolve_owner(role: str, payload: Dict[str, Any], config: Dict[str, Any]) -> str:
    return owner_from_payload(payload, role) or owner_from_config(config, role)


def pick_role_type(
    type_map: Dict[str, Dict[str, Any]], role: str
) -> Tuple[str, str]:
    want_en = ROLE_ENGLISH[role]
    scored: List[Tuple[int, str, str]] = []
    for tid, info in type_map.items():
        name = str(info.get("name") or "").strip()
        english = str(info.get("english_name") or "").strip().upper()
        score = 0
        if english == want_en:
            score += 10
        if role in name:
            score += 5
        if "子" in name:
            score += 2
        if score:
            scored.append((score, tid, name))
    scored.sort(key=lambda x: x[0], reverse=True)
    if not scored:
        raise ValueError(f"项目未配置「{role}」需求类别（workitem_type）")
    return scored[0][1], scored[0][2]


def load_payload(args: argparse.Namespace) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    if args.payload_file:
        path = Path(args.payload_file).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"payload 文件不存在: {path}")
        payload = read_json(path)
    for key, val in [
        ("workspace_id", args.workspace_id),
        ("story", args.story),
        ("story_id", args.story_id),
        ("story_name", args.story_name),
        ("story_url", args.story_url),
        ("roles", args.roles),
        ("frontend_owner", args.frontend_owner),
        ("backend_owner", args.backend_owner),
        ("qa_owner", args.qa_owner),
        ("creator", args.creator),
    ]:
        if val is not None and str(val).strip():
            payload[key] = val
    payload["dry_run"] = bool(payload.get("dry_run")) or bool(args.dry_run)
    return payload


def story_url(workspace_id: str, story_id: str) -> str:
    base = os.environ.get("TAPD_BASE_URL", "https://www.tapd.cn").rstrip("/")
    return f"{base}/{workspace_id}/prong/stories/view/{story_id}"


def existing_by_role(
    children: List[Dict[str, Any]], type_map: Dict[str, Dict[str, Any]], init_mod: Any
) -> Dict[str, Dict[str, Any]]:
    found: Dict[str, Dict[str, Any]] = {}
    for child in children:
        role = init_mod.classify_child_role(child, type_map)
        if role and role not in found:
            found[role] = child
    return found


def create_child_story(
    client: Any,
    *,
    workspace_id: str,
    parent: Dict[str, Any],
    role: str,
    type_id: str,
    owner: str,
    creator: str,
) -> Dict[str, Any]:
    parent_id = str(parent.get("id") or "").strip()
    body: Dict[str, Any] = {
        "workspace_id": int(workspace_id),
        "name": str(parent.get("name") or "").strip(),
        "parent_id": parent_id,
        "workitem_type_id": type_id,
    }
    iteration_id = str(parent.get("iteration_id") or "").strip()
    if iteration_id and iteration_id not in ("0", "null"):
        body["iteration_id"] = iteration_id
    if owner:
        body["owner"] = owner
    if creator:
        body["creator"] = creator
    resp = client.request("POST", "stories", data=body)
    if resp.get("status") != 1:
        raise RuntimeError(f"创建{role}子需求失败: {json.dumps(resp, ensure_ascii=False)}")
    story = {}
    data = resp.get("data")
    if isinstance(data, dict):
        story = data.get("Story") if isinstance(data.get("Story"), dict) else data
    if not isinstance(story, dict) or not str(story.get("id") or "").strip():
        raise RuntimeError(f"创建{role}子需求成功但未返回 id: {json.dumps(resp, ensure_ascii=False)}")
    return story


def main() -> None:
    args = parse_args()
    init_mod = load_init_mod()
    config = load_config(args)
    payload = load_payload(args)
    client = init_mod.load_tapd_client()

    workspace_id = str(
        payload.get("workspace_id") or config.get("workspace_id") or ""
    ).strip()
    story_ref = {
        key: str(payload.get(key) or "").strip()
        for key in ("story", "story_id", "story_name", "story_url")
        if str(payload.get(key) or "").strip()
    }
    if not story_ref:
        raise ValueError("请提供 story / story_id / story_name / story_url")
    if not workspace_id:
        url_ws, _ = init_mod.parse_story_url(story_ref.get("story") or story_ref.get("story_url") or "")
        workspace_id = url_ws
    if not workspace_id:
        raise ValueError("缺少 workspace_id：请配置 project_config.json 或传入需求链接")

    roles = parse_roles(payload.get("roles"))
    type_map = init_mod.fetch_workitem_type_map(client, workspace_id)
    status, story, candidates, source = init_mod.resolve_story_from_ref(
        client, workspace_id, story_ref, type_map
    )
    if status == "not_found":
        print(
            json.dumps(
                {
                    "status": "not_found",
                    "source": source,
                    "message": "未找到父需求",
                    "query": story_ref,
                },
                ensure_ascii=False,
            )
        )
        raise SystemExit(2)
    if status == "need_confirm":
        print(
            json.dumps(
                {
                    "status": "need_confirm",
                    "source": source,
                    "message": "需求名称匹配到多条，请改用 story_id",
                    "candidates": candidates,
                },
                ensure_ascii=False,
            )
        )
        raise SystemExit(2)

    assert story is not None
    parent = init_mod.resolve_root_story(client, workspace_id, story, type_map)
    children = init_mod.fetch_child_stories(client, workspace_id, parent)
    existing = existing_by_role(children, type_map, init_mod)
    creator = str(payload.get("creator") or os.environ.get("CURRENT_USER_NICK") or "").strip()
    dry_run = bool(payload.get("dry_run"))

    results: List[Dict[str, Any]] = []
    created = 0
    skipped = 0
    failed = 0
    for role in roles:
        type_id, type_name = pick_role_type(type_map, role)
        owner = resolve_owner(role, payload, config)
        current = existing.get(role)
        if current:
            sid = str(current.get("id") or "").strip()
            results.append(
                {
                    "role": role,
                    "action": "skipped_exists",
                    "workitem_type_id": type_id,
                    "workitem_type_name": type_name,
                    "owner": init_mod.first_owner(current.get("owner")),
                    "story_id": sid,
                    "story_url": story_url(workspace_id, sid),
                }
            )
            skipped += 1
            continue
        item = {
            "role": role,
            "action": "would_create" if dry_run else "created",
            "workitem_type_id": type_id,
            "workitem_type_name": type_name,
            "owner": owner,
            "name": str(parent.get("name") or "").strip(),
            "parent_id": str(parent.get("id") or "").strip(),
        }
        if dry_run:
            results.append(item)
            created += 1
            continue
        try:
            child = create_child_story(
                client,
                workspace_id=workspace_id,
                parent=parent,
                role=role,
                type_id=type_id,
                owner=owner,
                creator=creator,
            )
            sid = str(child.get("id") or "").strip()
            item["story_id"] = sid
            item["story_url"] = story_url(workspace_id, sid)
            results.append(item)
            created += 1
        except Exception as exc:
            item["action"] = "failed"
            item["error"] = str(exc)
            results.append(item)
            failed += 1

    parent_id = str(parent.get("id") or "").strip()
    out = {
        "status": "dry_run" if dry_run else ("error" if failed else "ok"),
        "source": source,
        "workspace_id": workspace_id,
        "parent": init_mod.story_brief(parent, workspace_id, type_map, role="需求"),
        "parent_url": story_url(workspace_id, parent_id),
        "dry_run": dry_run,
        "summary": {
            "created_or_planned": created,
            "skipped_exists": skipped,
            "failed": failed,
        },
        "children": results,
        "message": (
            "预览：将按 parent_id 创建缺失子需求"
            if dry_run
            else "已用 parent_id 把子需求挂到父需求下；已存在的角色已跳过"
        ),
    }
    print(json.dumps(out, ensure_ascii=False))
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
