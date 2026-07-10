"""
初始化并升级 TAPD 本地配置：
- 不存在则创建
- 存在则递归补齐缺失字段
- 保留已有值，不做覆盖
"""

from __future__ import annotations

import argparse
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
            target[key] = tpl_val
            added.append(key_path)
            continue

        cur_val = target[key]
        if isinstance(cur_val, dict) and isinstance(tpl_val, dict):
            _, nested_added = merge_missing_keys(cur_val, tpl_val, key_path)
            added.extend(nested_added)
    return target, added


def write_json(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def main() -> None:
    args = parse_args()
    template_path = Path(args.template).resolve()
    target_path = Path(args.target).resolve()

    if not template_path.is_file():
        raise FileNotFoundError(f"模板文件不存在: {template_path}")

    template_obj = read_json(template_path)

    if not target_path.exists():
        write_json(target_path, template_obj)
        project_status = "created"
        project_added = sorted(template_obj.keys())
    else:
        target_obj = read_json(target_path)
        merged_obj, added = merge_missing_keys(target_obj, template_obj)
        if added:
            write_json(target_path, merged_obj)
            project_status = "merged"
        else:
            project_status = "skipped"
        project_added = added

    env_status = "skipped"
    env_added: List[str] = []
    if DEFAULT_ENV_TEMPLATE.is_file():
        if not DEFAULT_ENV_TARGET.exists():
            env_obj = read_json(DEFAULT_ENV_TEMPLATE)
            write_json(DEFAULT_ENV_TARGET, env_obj)
            env_status = "created"
            env_added = sorted(env_obj.keys())
        else:
            env_obj = read_json(DEFAULT_ENV_TARGET)
            env_tpl = read_json(DEFAULT_ENV_TEMPLATE)
            merged_env, env_added = merge_missing_keys(env_obj, env_tpl)
            if env_added:
                write_json(DEFAULT_ENV_TARGET, merged_env)
                env_status = "merged"
            else:
                env_status = "skipped"

    result = {
        "status": project_status,
        "template_path": str(template_path),
        "target_path": str(target_path),
        "added_keys": project_added,
        "tapd_env_path": str(DEFAULT_ENV_TARGET),
        "tapd_env_status": env_status,
        "tapd_env_added_keys": env_added,
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()

