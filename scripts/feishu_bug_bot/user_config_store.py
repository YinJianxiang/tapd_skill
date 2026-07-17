"""按飞书 open_id 隔离的用户配置：LLM / TAPD 凭证 / skill 项目覆盖。"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from submit_bridge import workspace_root

SECRET_PATHS = frozenset(
    {
        "llm.api_key",
        "tapd_env.access_token",
    }
)

LLM_EDITABLE_PATHS: List[str] = [
    "llm.base_url",
    "llm.model",
    "llm.api_key",
]

TAPD_EDITABLE_PATHS: List[str] = [
    "tapd_env.access_token",
    "tapd_env.current_user_nick",
]

LLM_PATH_LABELS: Dict[str, str] = {
    "llm.base_url": "LLM Base URL",
    "llm.model": "LLM 模型",
    "llm.api_key": "LLM API Key",
}

TAPD_PATH_LABELS: Dict[str, str] = {
    "tapd_env.access_token": "TAPD Access Token",
    "tapd_env.current_user_nick": "当前用户昵称",
}

_SAFE_OPEN_ID = re.compile(r"[^A-Za-z0-9._-]+")


def users_dir() -> Path:
    d = workspace_root() / ".tapd" / "users"
    d.mkdir(parents=True, exist_ok=True)
    return d


def safe_open_id(open_id: str) -> str:
    s = (open_id or "").strip()
    if not s:
        raise ValueError("open_id 为空")
    cleaned = _SAFE_OPEN_ID.sub("_", s)
    if not cleaned:
        raise ValueError(f"无法安全化 open_id: {open_id!r}")
    return cleaned


def user_path(open_id: str) -> Path:
    return users_dir() / f"{safe_open_id(open_id)}.json"


def empty_user_config() -> Dict[str, Any]:
    return {
        "llm": {},
        "tapd_env": {},
        "project": {},
    }


def load_user_config(open_id: str) -> Dict[str, Any]:
    path = user_path(open_id)
    if not path.is_file():
        return empty_user_config()
    with path.open("r", encoding="utf-8-sig") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"用户配置根节点必须是对象: {path}")
    out = empty_user_config()
    for key in ("llm", "tapd_env", "project"):
        val = data.get(key)
        if isinstance(val, dict):
            out[key] = val
    return out


def save_user_config(open_id: str, data: Dict[str, Any]) -> None:
    path = user_path(open_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "llm": data.get("llm") if isinstance(data.get("llm"), dict) else {},
        "tapd_env": data.get("tapd_env") if isinstance(data.get("tapd_env"), dict) else {},
        "project": data.get("project") if isinstance(data.get("project"), dict) else {},
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix="user_cfg_",
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


def deep_merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    """递归合并：overlay 覆盖 base；两边均为 dict 时深入合并。"""
    out: Dict[str, Any] = dict(base)
    for key, val in (overlay or {}).items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], val)
        else:
            out[key] = val
    return out


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


def mask_secret(value: Any) -> str:
    s = str(value or "").strip()
    if not s:
        return "（未配置）"
    if len(s) <= 4:
        return "****"
    return f"****{s[-4:]}"


def is_secret_path(path: str) -> bool:
    return path in SECRET_PATHS


def display_value(path: str, value: Any) -> str:
    if is_secret_path(path):
        return mask_secret(value)
    if value is None or value == "":
        return "（空）"
    if isinstance(value, bool):
        return "开启" if value else "关闭"
    return str(value)


def _load_global_tapd_env() -> Dict[str, Any]:
    path = workspace_root() / ".tapd" / "tapd_env.json"
    if not path.is_file():
        return {}
    with path.open("r", encoding="utf-8-sig") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def resolve_llm(open_id: str, bot_cfg: Dict[str, Any]) -> Dict[str, Any]:
    base = dict(bot_cfg.get("llm") or {}) if isinstance(bot_cfg.get("llm"), dict) else {}
    user = load_user_config(open_id).get("llm") or {}
    if not isinstance(user, dict):
        user = {}
    return deep_merge(base, user)


def resolve_tapd_env(open_id: str) -> Dict[str, Any]:
    base = _load_global_tapd_env()
    user = load_user_config(open_id).get("tapd_env") or {}
    if not isinstance(user, dict):
        user = {}
    # 用户层空字符串视为未覆盖，保留全局
    cleaned: Dict[str, Any] = {}
    for k, v in user.items():
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        cleaned[k] = v
    return deep_merge(base, cleaned)


def resolve_project_config(open_id: str) -> Dict[str, Any]:
    from config_store import load_project_config

    base = load_project_config()
    user_project = load_user_config(open_id).get("project") or {}
    if not isinstance(user_project, dict):
        user_project = {}
    return deep_merge(base, user_project)


def update_project_field(open_id: str, path: str, value: Any) -> Tuple[Any, Any]:
    """更新用户 project overlay（skill 白名单路径，如 defaults.reporter）。"""
    data = load_user_config(open_id)
    project = data.get("project") if isinstance(data.get("project"), dict) else {}
    old = get_by_path(project, path)
    set_by_path(project, path, value)
    data["project"] = project
    save_user_config(open_id, data)
    return old, value


def update_llm_field(open_id: str, path: str, value: Any, *, bot_cfg: Dict[str, Any]) -> Tuple[Any, Any]:
    if path not in LLM_EDITABLE_PATHS:
        raise ValueError(f"不允许修改字段: {path}")
    sub = path[len("llm.") :]
    new_val = str(value).strip() if value is not None else ""
    if path == "llm.api_key" and not new_val:
        raise ValueError("API Key 不能为空")
    if path in ("llm.base_url", "llm.model") and not new_val:
        raise ValueError(f"{LLM_PATH_LABELS.get(path, path)} 不能为空")
    old = (resolve_llm(open_id, bot_cfg) or {}).get(sub)
    data = load_user_config(open_id)
    llm = data.get("llm") if isinstance(data.get("llm"), dict) else {}
    llm[sub] = new_val
    data["llm"] = llm
    save_user_config(open_id, data)
    return old, new_val


def update_tapd_field(open_id: str, path: str, value: Any) -> Tuple[Any, Any]:
    if path not in TAPD_EDITABLE_PATHS:
        raise ValueError(f"不允许修改字段: {path}")
    sub = path[len("tapd_env.") :]
    new_val = str(value).strip() if value is not None else ""
    if path == "tapd_env.access_token" and not new_val:
        raise ValueError("Access Token 不能为空")
    old = (resolve_tapd_env(open_id) or {}).get(sub)
    data = load_user_config(open_id)
    tapd = data.get("tapd_env") if isinstance(data.get("tapd_env"), dict) else {}
    tapd[sub] = new_val
    data["tapd_env"] = tapd
    save_user_config(open_id, data)
    return old, new_val


def snapshot_llm(open_id: str, bot_cfg: Dict[str, Any]) -> List[Tuple[str, str, Any]]:
    llm = resolve_llm(open_id, bot_cfg)
    rows: List[Tuple[str, str, Any]] = []
    for path in LLM_EDITABLE_PATHS:
        key = path[len("llm.") :]
        rows.append((path, LLM_PATH_LABELS.get(path, path), llm.get(key)))
    return rows


def snapshot_tapd(open_id: str) -> List[Tuple[str, str, Any]]:
    env = resolve_tapd_env(open_id)
    rows: List[Tuple[str, str, Any]] = []
    for path in TAPD_EDITABLE_PATHS:
        key = path[len("tapd_env.") :]
        rows.append((path, TAPD_PATH_LABELS.get(path, path), env.get(key)))
    return rows


def tapd_env_to_process_env(tapd_env: Dict[str, Any]) -> Dict[str, str]:
    """将有效 tapd_env 转为子进程 / 临时 os.environ 覆盖项。"""
    mapping = {
        "access_token": "TAPD_ACCESS_TOKEN",
        "api_user": "TAPD_API_USER",
        "api_password": "TAPD_API_PASSWORD",
        "api_base_url": "TAPD_API_BASE_URL",
        "base_url": "TAPD_BASE_URL",
        "current_user_nick": "CURRENT_USER_NICK",
    }
    out: Dict[str, str] = {}
    for src, dst in mapping.items():
        val = tapd_env.get(src)
        if val is None:
            continue
        s = str(val).strip()
        if s:
            out[dst] = s
    return out


def write_temp_project_config(open_id: str) -> Path:
    """写出合并后的临时 project_config，供 submit --config-file 使用。"""
    cfg = resolve_project_config(open_id)
    root = workspace_root() / ".tapd"
    root.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f"pc_{safe_open_id(open_id)}_",
        suffix=".json",
        dir=str(root),
    )
    path = Path(tmp_name)
    with open(fd, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return path
