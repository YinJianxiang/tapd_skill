"""
初始化并升级 TAPD 本地配置：
- 不存在则创建
- 存在则递归补齐缺失字段（不覆盖已有值）
- 可选 --payload-file / --set 显式更新指定字段（会覆盖）
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple


def workspace_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / ".cursor").is_dir():
            return parent
    raise RuntimeError("无法定位项目根目录（未找到 .cursor 目录）")


DEFAULT_TEMPLATE = workspace_root() / ".tapd" / "project_config.template.json"
DEFAULT_TARGET = workspace_root() / ".tapd" / "project_config.json"
DEFAULT_ENV_TEMPLATE = workspace_root() / ".tapd" / "tapd_env.template.json"
DEFAULT_ENV_TARGET = workspace_root() / ".tapd" / "tapd_env.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="初始化/升级 TAPD 项目配置")
    parser.add_argument("--template", default=str(DEFAULT_TEMPLATE), help="模板 JSON 路径")
    parser.add_argument("--target", default=str(DEFAULT_TARGET), help="目标配置 JSON 路径")
    parser.add_argument(
        "--payload-file",
        help="UTF-8 JSON 文件，递归合并并覆盖 project_config.json 中对应字段",
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


def load_update_payload(args: argparse.Namespace) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    if args.payload_file:
        payload_path = Path(args.payload_file).resolve()
        if not payload_path.is_file():
            raise FileNotFoundError(f"payload 文件不存在: {payload_path}")
        file_payload = read_json(payload_path)
        payload, _ = apply_updates(payload, file_payload)
    if args.set_items:
        set_payload = build_updates_from_set_items(args.set_items)
        payload, _ = apply_updates(payload, set_payload)
    return payload


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


def main() -> None:
    args = parse_args()
    template_path = Path(args.template).resolve()
    target_path = Path(args.target).resolve()

    update_payload = load_update_payload(args)
    env_payload: Dict[str, Any] | None = None
    if args.env_payload_file:
        env_payload_path = Path(args.env_payload_file).resolve()
        if not env_payload_path.is_file():
            raise FileNotFoundError(f"env payload 文件不存在: {env_payload_path}")
        env_payload = read_json(env_payload_path)

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
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
