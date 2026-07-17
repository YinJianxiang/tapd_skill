"""读写项目配置白名单字段（飞书机器人）；按 open_id 写入用户 overlay。"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from submit_bridge import workspace_root

DEFAULT_EDITABLE_PATHS: List[str] = [
    "defaults.version",
    "defaults.iteration",
    "defaults.name",
    "defaults.reporter",
    "module_owner_map.前端",
    "module_owner_map.后端",
    "link_story.enabled",
    "link_story.story_id",
    "link_story.story_name",
    "link_story.match_scope",
]

PATH_LABELS: Dict[str, str] = {
    "defaults.version": "发现版本",
    "defaults.iteration": "迭代",
    "defaults.name": "标题前缀",
    "defaults.reporter": "提单人",
    "module_owner_map.前端": "前端负责人",
    "module_owner_map.后端": "后端负责人",
    "link_story.enabled": "自动关联需求",
    "link_story.story_id": "固定需求 ID",
    "link_story.story_name": "固定需求名称",
    "link_story.match_scope": "需求匹配范围",
}


def config_path() -> Path:
    return workspace_root() / ".tapd" / "project_config.json"


def backup_path() -> Path:
    return workspace_root() / ".tapd" / "project_config.json.bak"


def load_project_config() -> Dict[str, Any]:
    path = config_path()
    if not path.is_file():
        raise FileNotFoundError(f"缺少项目配置: {path}")
    with path.open("r", encoding="utf-8-sig") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("project_config.json 根节点必须是对象")
    return data


def get_by_path(data: Dict[str, Any], path: str) -> Any:
    cur: Any = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def set_by_path(data: Dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cur: Dict[str, Any] = data
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


def path_label(path: str) -> str:
    from user_config_store import LLM_PATH_LABELS, TAPD_PATH_LABELS

    return PATH_LABELS.get(path) or LLM_PATH_LABELS.get(path) or TAPD_PATH_LABELS.get(path, path)


def editable_paths(cfg_whitelist: Optional[List[str]] = None) -> List[str]:
    if cfg_whitelist:
        return [str(p).strip() for p in cfg_whitelist if str(p).strip()]
    return list(DEFAULT_EDITABLE_PATHS)


def snapshot_editable(
    data: Optional[Dict[str, Any]] = None,
    *,
    whitelist: Optional[List[str]] = None,
    open_id: Optional[str] = None,
) -> List[Tuple[str, str, Any]]:
    """返回 [(path, label, value), ...]。传入 open_id 时使用用户合并后的有效配置。"""
    if data is not None:
        cfg = data
    elif open_id:
        from user_config_store import resolve_project_config

        cfg = resolve_project_config(open_id)
    else:
        cfg = load_project_config()
    rows: List[Tuple[str, str, Any]] = []
    for path in editable_paths(whitelist):
        rows.append((path, path_label(path), get_by_path(cfg, path)))
    return rows


def _parse_bool(raw: str) -> bool:
    s = raw.strip().lower()
    if s in ("1", "true", "yes", "y", "on", "开启", "开", "启用"):
        return True
    if s in ("0", "false", "no", "n", "off", "关闭", "关", "禁用"):
        return False
    raise ValueError(f"无法解析布尔值: {raw!r}，请发送 开启/关闭 或 true/false")


def normalize_value(path: str, raw: Any) -> Any:
    if path == "link_story.enabled":
        if isinstance(raw, bool):
            return raw
        return _parse_bool(str(raw))
    if path == "link_story.match_scope":
        scope = str(raw).strip().lower()
        if scope not in ("iteration", "project"):
            raise ValueError("match_scope 仅允许 iteration 或 project")
        return scope
    if raw is None:
        return ""
    return str(raw).strip()


def save_project_config(data: Dict[str, Any]) -> None:
    """写入全局 project_config（仅管理员/初始化用途；飞书改配置走用户 overlay）。"""
    path = config_path()
    bak = backup_path()
    if path.is_file():
        shutil.copy2(path, bak)
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix="project_config_",
        suffix=".json",
        dir=str(path.parent),
    )
    tmp = Path(tmp_name)
    try:
        with open(fd, "w", encoding="utf-8") as f:
            f.write(text)
        tmp.replace(path)
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise


def update_editable(
    path: str,
    value: Any,
    *,
    whitelist: Optional[List[str]] = None,
    open_id: Optional[str] = None,
) -> Tuple[Any, Any]:
    """
    更新白名单字段。有 open_id 时写入用户 overlay；否则写全局文件。
    返回 (old_effective_value, new_value)。
    """
    allowed = set(editable_paths(whitelist))
    if path not in allowed:
        raise ValueError(f"不允许修改字段: {path}")
    new_val = normalize_value(path, value)

    if open_id:
        from user_config_store import resolve_project_config, update_project_field

        old = get_by_path(resolve_project_config(open_id), path)
        update_project_field(open_id, path, new_val)
        return old, new_val

    data = load_project_config()
    old = get_by_path(data, path)
    set_by_path(data, path, new_val)
    save_project_config(data)
    return old, new_val


def toggle_link_story_enabled(
    *,
    whitelist: Optional[List[str]] = None,
    open_id: Optional[str] = None,
) -> Tuple[bool, bool]:
    path = "link_story.enabled"
    if open_id:
        from user_config_store import resolve_project_config

        old = bool(get_by_path(resolve_project_config(open_id), path))
    else:
        old = bool(get_by_path(load_project_config(), path))
    return update_editable(path, not old, whitelist=whitelist, open_id=open_id)  # type: ignore[return-value]
