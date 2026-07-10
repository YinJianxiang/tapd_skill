"""
TAPD 缺陷提交通用脚本：
- 上传图片并内嵌到 description「实际结果」（upload_image）
- 创建 bug
- 可选上传非图片附件
- 自动关联需求
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


DEFAULT_TAPD_BASE_URL = "https://www.tapd.cn"


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
DEFAULT_STATE_FILE = workspace_root() / ".tapd" / "submit_state.json"

EMBED_IMAGE_SUFFIXES = {".png", ".gif", ".jpg", ".jpeg", ".bmp"}

# TAPD 优先级 value -> 中文标签（部分实例创建时需同时传 priority_label）
PRIORITY_LABEL_MAP = {
    "urgent": "紧急",
    "high": "高",
    "medium": "中",
    "low": "低",
    "insignificant": "无关紧要",
}


def load_tapd_client():
    script_path = find_tapd_client_script()
    if not script_path.is_file():
        raise FileNotFoundError(f"未找到 tapd 客户端脚本: {script_path}")

    spec = importlib.util.spec_from_file_location("tapd_client_stdlib", script_path)
    if not spec or not spec.loader:
        raise RuntimeError("加载 tapd_client_stdlib 失败")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def find_link_bug_story_script() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "tapd-link-bug-story" / "scripts" / "link_bug_story.py"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("未找到 link_bug_story.py")


def load_link_bug_story_module():
    script_path = find_link_bug_story_script()
    spec = importlib.util.spec_from_file_location("link_bug_story", script_path)
    if not spec or not spec.loader:
        raise RuntimeError("加载 link_bug_story 失败")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="创建 TAPD 缺陷并可选上传附件")
    parser.add_argument("--payload-file", help="UTF-8 JSON 文件路径，推荐")
    parser.add_argument("--config-file", help="项目本地配置文件路径（可选）")
    parser.add_argument("--workspace-id", dest="workspace_id")
    parser.add_argument("--title")
    parser.add_argument("--description")
    parser.add_argument("--priority")
    parser.add_argument("--severity")
    parser.add_argument("--current-owner", dest="current_owner")
    parser.add_argument("--reporter")
    parser.add_argument("--module", help="缺陷所属模块，用于自动匹配负责人")
    parser.add_argument(
        "--name",
        help="标题前缀名称，提交时格式化为【name】xxx；可覆盖 defaults.name",
    )
    parser.add_argument("--version", help="所属版本，可覆盖配置默认值")
    parser.add_argument(
        "--label",
        help="缺陷标签，多个用英文竖线 | 分隔（与 TAPD label 字段一致）",
    )
    parser.add_argument("--story-id", dest="story_id", help="关联需求 ID（可选，跳过自动匹配）")
    parser.add_argument("--story-name", dest="story_name", help="关联需求标题（可选）")
    parser.add_argument(
        "--force-link-story",
        action="store_true",
        help="低置信度也强制关联需求",
    )
    parser.add_argument(
        "--no-link-story",
        action="store_true",
        help="创建 bug 后不搜索/关联需求",
    )
    parser.add_argument("--file", dest="file_path", help="截图/附件路径（图片默认内嵌到「实际结果」）")
    parser.add_argument(
        "--files",
        dest="files",
        help="多个文件路径，逗号分隔（图片默认内嵌；附件需 --attach）",
    )
    parser.add_argument(
        "--attach",
        action="store_true",
        help="将 --file/--files 中的文件上传到缺陷「附件」区（默认不上传）",
    )
    parser.add_argument(
        "--no-embed-image",
        action="store_true",
        help="图片不内嵌到描述（需配合 --attach 才会进入附件区）",
    )
    parser.add_argument(
        "--also-attach-image",
        action="store_true",
        help="图片内嵌到描述的同时，也上传到附件区（无需 --attach）",
    )
    parser.add_argument(
        "--resume-only",
        action="store_true",
        help="仅从本地状态恢复并继续附件上传，不创建新 bug",
    )
    parser.add_argument("--dry-run", action="store_true", help="仅校验并输出最终 payload，不实际提交")
    parser.add_argument(
        "--show-raw-response",
        action="store_true",
        help="输出 TAPD 原始响应（调试用）",
    )
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


def normalize_description(summary: str, description: str) -> str:
    if not summary:
        return description
    return f"问题概述：{summary}\n\n{description}"

def normalize_label_value(value: Any) -> Optional[str]:
    """将 label / labels 规范为 TAPD API 要求的竖线分隔字符串。"""
    if value is None:
        return None
    if isinstance(value, list):
        parts = [str(item).strip() for item in value if str(item).strip()]
        return "|".join(parts) if parts else None
    text = str(value).strip()
    if not text:
        return None
    if "|" in text:
        parts = [part.strip() for part in text.split("|") if part.strip()]
        return "|".join(parts) if parts else None
    for sep in (",", "，", ";", "；"):
        if sep in text:
            parts = [part.strip() for part in text.split(sep) if part.strip()]
            return "|".join(parts) if parts else None
    return text


def format_description_for_tapd(description: str) -> str:
    """
    TAPD 页面在部分场景下对纯 \\n 展示不稳定，统一转换为 <br/> 提高换行可读性。
    已含 <img 标签的行原样保留，避免破坏内嵌图片 HTML。
    """
    normalized = description.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in normalized.split("\n")]
    parts: List[str] = []
    for line in lines:
        if "<img" in line.lower():
            parts.append(line)
        else:
            parts.append(line)
    return "<br/>".join(parts)


def is_embeddable_image(path: str) -> bool:
    return Path(path).suffix.lower() in EMBED_IMAGE_SUFFIXES


def split_path_list(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        items = [str(x).strip() for x in raw]
    else:
        text = str(raw).strip()
        if not text:
            return []
        items = re.split(r"[,，;；\s]+", text)
    return [p for p in items if p]


def attachment_upload_enabled(
    args: argparse.Namespace, payload: Dict[str, Any], config: Dict[str, Any]
) -> bool:
    if args.attach:
        return True
    if payload.get("attach") or payload.get("upload_attachment"):
        return True
    att_cfg = config.get("attachments", {})
    if isinstance(att_cfg, dict) and att_cfg.get("enabled"):
        return True
    return False


def collect_input_files(
    args: argparse.Namespace, payload: Dict[str, Any], config: Dict[str, Any]
) -> Tuple[List[str], List[str]]:
    """返回 (内嵌图片路径, 附件路径)。"""
    paths: List[str] = []
    if args.file_path:
        paths.append(str(args.file_path).strip())
    if args.files:
        paths.extend(split_path_list(args.files))
    for key in ("files", "file_paths", "images"):
        paths.extend(split_path_list(payload.get(key)))

    seen = set()
    unique: List[str] = []
    for p in paths:
        resolved = str(Path(p).resolve()) if p else ""
        if resolved and resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)

    embed_enabled = not args.no_embed_image
    embed_cfg = payload.get("_embed_images_cfg")
    if not isinstance(embed_cfg, dict):
        embed_cfg = config.get("embed_images", {})
    if isinstance(embed_cfg, dict) and embed_cfg.get("enabled") is False:
        embed_enabled = False

    attach_all = attachment_upload_enabled(args, payload, config)
    also_attach_images = bool(args.also_attach_image)
    if isinstance(embed_cfg, dict) and embed_cfg.get("also_attach"):
        also_attach_images = True

    image_paths: List[str] = []
    attachment_paths: List[str] = []

    for p in unique:
        if not Path(p).is_file():
            raise FileNotFoundError(f"文件不存在: {p}")
        is_img = is_embeddable_image(p)
        if embed_enabled and is_img:
            image_paths.append(p)
        if attach_all:
            attachment_paths.append(p)
        elif also_attach_images and is_img:
            attachment_paths.append(p)
    return image_paths, attachment_paths


def inject_images_into_actual_result(description: str, image_html_blocks: List[str]) -> str:
    """将 upload_image 返回的 html_code 插入「实际结果」段落后。"""
    if not image_html_blocks:
        return description
    block = "<br/>".join(image_html_blocks)

    for marker in ("实际结果：", "实际结果:"):
        idx = description.find(marker)
        if idx < 0:
            continue
        line_end = description.find("<br/>", idx)
        if line_end < 0:
            return description + "<br/>" + block
        insert_at = line_end + len("<br/>")
        return description[:insert_at] + block + "<br/>" + description[insert_at:]

    return description + "<br/><br/>实际结果截图：<br/>" + block


def embed_images_in_description(
    client: Any,
    payload: Dict[str, Any],
    image_paths: List[str],
    *,
    dry_run: bool = False,
) -> Dict[str, Any]:
    if not image_paths:
        return {"action": "skipped", "embedded": []}

    html_blocks: List[str] = []
    embedded: List[Dict[str, Any]] = []
    workspace_id = int(payload["workspace_id"])

    for path in image_paths:
        if dry_run:
            html_blocks.append(f'<!-- dry-run image: {Path(path).name} -->')
            embedded.append({"file": path, "filename": Path(path).name, "dry_run": True})
            continue
        resp = client.upload_image(workspace_id, path)
        data = resp.get("data", {}) or {}
        html_code = str(data.get("html_code", "")).strip()
        if not html_code:
            src = str(data.get("image_src", "")).strip()
            html_code = f'<img src="{src}"/>' if src else ""
        if not html_code:
            raise RuntimeError(f"upload_image 未返回 html_code: {path}")
        html_blocks.append(html_code)
        embedded.append(
            {
                "file": path,
                "filename": Path(path).name,
                "image_src": data.get("image_src"),
                "html_code": html_code,
            }
        )

    if dry_run:
        payload["description"] = inject_images_into_actual_result(
            payload["description"],
            [f'<img alt="{Path(p).name}" />' for p in image_paths],
        )
        return {"action": "would_embed", "embedded": embedded, "count": len(image_paths)}

    payload["description"] = inject_images_into_actual_result(payload["description"], html_blocks)
    return {"action": "embedded", "embedded": embedded, "count": len(embedded)}


def pick_value(payload: Dict[str, Any], config: Dict[str, Any], key: str) -> Optional[Any]:
    if str(payload.get(key, "")).strip():
        return payload.get(key)
    defaults = config.get("defaults", {})
    if isinstance(defaults, dict) and str(defaults.get(key, "")).strip():
        return defaults.get(key)
    if str(config.get(key, "")).strip():
        return config.get(key)
    return None


def format_bug_title(title: str, name: str) -> str:
    """
    将 title 规范为【name】xxx。
    - name 为空时：title 须已含【】前缀，否则报错
    - name 非空时：取 title 中】后的正文（或整段 title），拼成【name】正文
    """
    body = str(title or "").strip()
    name_norm = str(name or "").strip()
    if body.startswith("【") and "】" in body:
        body = body[body.index("】") + 1 :].strip()
    if not name_norm:
        full = str(title or "").strip()
        if full.startswith("【") and "】" in full:
            return full
        raise ValueError(
            "title 须为【name】xxx 格式：请配置 defaults.name 或在 payload 中提供 name"
        )
    if not body:
        raise ValueError("title 正文不能为空")
    prefix = f"【{name_norm}】"
    if str(title or "").strip().startswith(prefix):
        return str(title).strip()
    return prefix + body


def resolve_iteration_id(
    client: Any,
    workspace_id: str,
    iteration_id: Optional[str],
    iteration_name: Optional[str],
) -> Optional[str]:
    if str(iteration_id or "").strip():
        return str(iteration_id).strip()
    if not str(iteration_name or "").strip():
        return None
    resp = client.request(
        "GET",
        "iterations",
        params={
            "workspace_id": workspace_id,
            "name": iteration_name,
            "limit": 200,
            "page": 1,
        },
    )
    rows = resp.get("data", [])
    if not isinstance(rows, list):
        return None
    exact = None
    fuzzy = None
    name_norm = iteration_name.strip()
    for row in rows:
        it = row.get("Iteration", row) if isinstance(row, dict) else {}
        if not isinstance(it, dict):
            continue
        iid = str(it.get("id", "")).strip()
        iname = str(it.get("name", "")).strip()
        if not iid:
            continue
        if iname == name_norm:
            exact = iid
            break
        if name_norm and name_norm in iname and fuzzy is None:
            fuzzy = iid
    return exact or fuzzy


def resolve_iteration_in_payload(
    payload: Dict[str, Any], config: Dict[str, Any], client: Any
) -> Dict[str, Any]:
    """将配置/payload 中的迭代名称解析为 TAPD API 所需的 iteration_id。"""
    if str(payload.get("iteration_id", "")).strip():
        payload.pop("iteration", None)
        return payload

    iteration_name = str(payload.get("iteration", "")).strip()
    if not iteration_name:
        picked = pick_value(payload, config, "iteration")
        if picked is not None:
            iteration_name = str(picked).strip()
            payload["iteration"] = iteration_name

    if not iteration_name:
        payload.pop("iteration", None)
        return payload

    workspace_id = str(payload.get("workspace_id", "")).strip()
    iid = resolve_iteration_id(client, workspace_id, None, iteration_name)
    if not iid:
        raise ValueError(f"未找到迭代：{iteration_name}")

    payload["iteration_id"] = iid
    payload.pop("iteration", None)
    return payload


def finalize_tapd_bug_payload(payload: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    """
    将内部字段映射为 TAPD bugs API 字段，并按项目约定自动补齐侧栏信息。

    页面字段 -> API 字段：
    - 发现版本 -> version_report（非 version）
    - 测试人员 -> te（默认 = reporter）
    - 开发人员 -> de（默认 = current_owner）
    - 软件平台 -> platform（默认 = module）
    - 迭代 -> iteration_id（由 iteration 名称在提交前解析）
    """
    # 发现版本：配置里的 version 对应 TAPD 的 version_report
    if not str(payload.get("version_report", "")).strip():
        version = pick_value(payload, config, "version")
        if version is not None:
            payload["version_report"] = version
    payload.pop("version", None)

    # 测试人员 = 创建人
    if not str(payload.get("te", "")).strip() and str(payload.get("reporter", "")).strip():
        payload["te"] = payload["reporter"]

    # 开发人员 = 处理人
    if not str(payload.get("de", "")).strip() and str(payload.get("current_owner", "")).strip():
        payload["de"] = payload["current_owner"]

    # 软件平台 = 模块
    if not str(payload.get("platform", "")).strip():
        module = str(payload.get("module", "")).strip()
        if module:
            payload["platform"] = module

    # 优先级标签（与 priority 值配套）
    priority = str(payload.get("priority", "")).strip()
    if priority and not str(payload.get("priority_label", "")).strip():
        payload["priority_label"] = PRIORITY_LABEL_MAP.get(priority, priority)

    # 缺陷标签（TAPD label 字段，多标签以 | 分隔；不存在时 TAPD 会自动创建）
    raw_label = payload.get("label")
    if not str(raw_label or "").strip():
        raw_label = payload.get("labels")
    label = normalize_label_value(raw_label)
    if label:
        payload["label"] = label
    else:
        payload.pop("label", None)
    payload.pop("labels", None)

    return payload


def load_payload(args: argparse.Namespace, config: Dict[str, Any]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    if args.payload_file:
        payload_path = Path(args.payload_file).resolve()
        with payload_path.open("r", encoding="utf-8-sig") as f:
            from_file = json.load(f)
        if not isinstance(from_file, dict):
            raise ValueError("payload 文件必须是 JSON 对象")
        payload.update(from_file)

    cli_values = {
        "workspace_id": args.workspace_id,
        "title": args.title,
        "description": args.description,
        "priority": args.priority,
        "severity": args.severity,
        "current_owner": args.current_owner,
        "reporter": args.reporter,
        "module": args.module,
        "name": args.name,
        "version": args.version,
        "label": args.label,
        "story_id": args.story_id,
        "story_name": args.story_name,
    }
    for k, v in cli_values.items():
        if v is not None:
            payload[k] = v

    if args.force_link_story:
        payload["force_link_story"] = True
    if args.no_link_story:
        payload["skip_link_story"] = True
    if args.attach:
        payload["attach"] = True

    # 配置兜底：全局默认字段
    # module 不走 defaults 兜底，须由用户显式指定或 AI 高置信判定后写入 payload
    for field in ["workspace_id", "reporter", "version", "iteration", "name"]:
        value = pick_value(payload, config, field)
        if value is not None:
            payload[field] = value

    if not str(payload.get("label", "")).strip() and not payload.get("labels"):
        defaults = config.get("defaults", {})
        if isinstance(defaults, dict):
            for key in ("label", "labels"):
                if key in defaults and defaults[key] not in (None, ""):
                    payload[key] = defaults[key]
                    break

    # 模块负责人自动匹配
    if not str(payload.get("current_owner", "")).strip():
        module = str(payload.get("module", "")).strip()
        if module:
            module_owner_map = config.get("module_owner_map", {})
            if isinstance(module_owner_map, dict) and str(module_owner_map.get(module, "")).strip():
                payload["current_owner"] = module_owner_map[module]
            elif config:
                raise ValueError(f"模块 '{module}' 未在 module_owner_map 中配置负责人")

    # 模块扩展字段
    module = str(payload.get("module", "")).strip()
    if module:
        module_field_map = config.get("module_field_map", {})
        if isinstance(module_field_map, dict):
            module_extra = module_field_map.get(module, {})
            if isinstance(module_extra, dict):
                for k, v in module_extra.items():
                    if k not in payload or payload[k] in (None, ""):
                        payload[k] = v

    summary = str(payload.pop("summary", "")).strip()
    description = str(payload.get("description", "")).strip()
    if summary and description:
        payload["description"] = normalize_description(summary, description)
    elif description:
        payload["description"] = description

    if str(payload.get("description", "")).strip():
        payload["description"] = format_description_for_tapd(str(payload["description"]))

    title_name = str(payload.get("name", "")).strip()
    payload["title"] = format_bug_title(str(payload.get("title", "")), title_name)
    payload.pop("name", None)

    payload = finalize_tapd_bug_payload(payload, config)

    required = [
        "workspace_id",
        "title",
        "description",
        "priority",
        "severity",
        "current_owner",
        "reporter",
    ]
    missing = [k for k in required if not str(payload.get(k, "")).strip()]
    if missing:
        raise ValueError(f"缺少必填字段: {', '.join(missing)}")

    payload["workspace_id"] = str(payload["workspace_id"])

    embed_cfg = config.get("embed_images", {})
    if isinstance(embed_cfg, dict):
        payload["_embed_images_cfg"] = embed_cfg

    return payload


def extract_bug_id(response: Dict[str, Any]) -> str:
    bug = response.get("data", {}).get("Bug", {})
    bug_id = bug.get("id")
    if not bug_id:
        raise RuntimeError(f"创建缺陷返回异常: {json.dumps(response, ensure_ascii=False)}")
    return str(bug_id)


def build_bug_url(workspace_id: str, bug_id: str) -> str:
    base_url = os.environ.get("TAPD_BASE_URL", DEFAULT_TAPD_BASE_URL).rstrip("/")
    return f"{base_url}/{workspace_id}/bugtrace/bugs/view/{bug_id}"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def calc_idempotency_key(payload: Dict[str, Any]) -> str:
    raw = "|".join(
        [
            str(payload.get("workspace_id", "")).strip(),
            str(payload.get("title", "")).strip(),
            str(payload.get("description", "")).strip(),
            str(payload.get("reporter", "")).strip(),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def calc_file_fingerprint(file_path: Optional[str]) -> Optional[str]:
    if not file_path:
        return None
    p = Path(file_path)
    if not p.is_file():
        return None
    stat = p.stat()
    return f"{p.name}:{stat.st_size}:{stat.st_mtime_ns}"


def load_submit_state(state_path: Path) -> Dict[str, Any]:
    if not state_path.is_file():
        return {"records": {}}
    with state_path.open("r", encoding="utf-8-sig") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return {"records": {}}
    records = data.get("records")
    if not isinstance(records, dict):
        data["records"] = {}
    return data


def save_submit_state(state_path: Path, state: Dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def upsert_state_record(
    state: Dict[str, Any],
    idempotency_key: str,
    payload: Dict[str, Any],
    bug_id: str,
    attachment_uploaded: bool,
    attachment_file_fingerprint: Optional[str],
    attachment_id: Optional[str] = None,
    attachment_filename: Optional[str] = None,
) -> Dict[str, Any]:
    records = state.setdefault("records", {})
    if not isinstance(records, dict):
        records = {}
        state["records"] = records
    record = {
        "idempotency_key": idempotency_key,
        "workspace_id": str(payload.get("workspace_id", "")),
        "title": str(payload.get("title", "")),
        "reporter": str(payload.get("reporter", "")),
        "bug_id": str(bug_id),
        "attachment_uploaded": bool(attachment_uploaded),
        "attachment_file_fingerprint": attachment_file_fingerprint,
        "attachment_id": attachment_id,
        "attachment_filename": attachment_filename,
        "updated_at": utc_now_iso(),
    }
    records[idempotency_key] = record
    return record


def attempt_story_link(
    client: Any,
    config: Dict[str, Any],
    payload: Dict[str, Any],
    bug_id: str,
    *,
    dry_run: bool = False,
) -> Optional[Dict[str, Any]]:
    """创建 bug 后搜索并尝试关联需求（默认开启，可通过 no-link-story / config 关闭）。"""
    try:
        link_mod = load_link_bug_story_module()
    except FileNotFoundError:
        return {"action": "skipped", "message": "未找到 link_bug_story 模块"}

    if not link_mod.story_link_enabled(config, payload):
        return {"action": "skipped", "message": "已关闭需求关联"}

    link_payload = link_mod.build_link_payload_from_config(config, payload)
    iteration_id = str(payload.get("iteration_id", "")).strip() or None
    if dry_run and bug_id == "dry-run":
        bug = {
            "id": "dry-run",
            "title": str(payload.get("title", "")).strip(),
            "description": str(payload.get("description", "")).strip(),
        }
    else:
        bug = {
            "id": bug_id,
            "title": str(payload.get("title", "")).strip(),
            "description": str(payload.get("description", "")).strip(),
        }
    return link_mod.link_bug_to_story(
        client,
        str(payload["workspace_id"]),
        bug,
        link_payload,
        iteration_id,
        dry_run=dry_run,
    )


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = parse_args()
    config = load_config(args)
    payload = load_payload(args, config)
    client = load_tapd_client()
    payload = resolve_iteration_in_payload(payload, config, client)

    image_paths, attachment_paths = collect_input_files(args, payload, config)
    embed_result = embed_images_in_description(
        client, payload, image_paths, dry_run=bool(args.dry_run)
    )
    payload.pop("_embed_images_cfg", None)

    state_path = DEFAULT_STATE_FILE
    state = load_submit_state(state_path)
    idempotency_key = calc_idempotency_key(payload)
    attachment_fingerprint = (
        "|".join(fp for fp in (calc_file_fingerprint(p) for p in attachment_paths) if fp)
        or None
    )

    if args.dry_run:
        defaults = config.get("defaults", {})
        iteration_name = (
            defaults.get("iteration")
            if isinstance(defaults, dict) and str(defaults.get("iteration", "")).strip()
            else None
        )
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "payload": payload,
                    "iteration_name": iteration_name,
                    "config_file": str((Path(args.config_file).resolve() if args.config_file else DEFAULT_CONFIG_FILE)),
                    "image_paths": image_paths,
                    "attachment_paths": attachment_paths,
                    "attachment_upload": attachment_upload_enabled(args, payload, config),
                    "embed_images": embed_result,
                    "state_file": str(state_path),
                    "idempotency_key": idempotency_key,
                    "story_link": attempt_story_link(
                        client,
                        config,
                        payload,
                        bug_id="dry-run",
                        dry_run=True,
                    ),
                },
                ensure_ascii=False,
            )
        )
        return

    record = state.get("records", {}).get(idempotency_key, {})
    if not isinstance(record, dict):
        record = {}
    bug_id = str(record.get("bug_id", "")).strip()
    create_resp: Optional[Dict[str, Any]] = None
    recovered = False
    idempotent_skip = False
    if bug_id:
        recovered = True
    if args.resume_only and not bug_id:
        raise RuntimeError("未找到可恢复状态：请先执行一次创建，或去掉 --resume-only")
    if not bug_id:
        create_resp = client.request("POST", "bugs", data=payload)
        bug_id = extract_bug_id(create_resp)
        upsert_state_record(
            state=state,
            idempotency_key=idempotency_key,
            payload=payload,
            bug_id=bug_id,
            attachment_uploaded=False,
            attachment_file_fingerprint=attachment_fingerprint,
        )
        save_submit_state(state_path, state)

    attachment_resp: Optional[Dict[str, Any]] = None
    attachment_info: Dict[str, Any] = {}
    uploaded_attachments: List[Dict[str, Any]] = []

    if attachment_paths:
        uploaded = bool(record.get("attachment_uploaded"))
        old_fingerprint = record.get("attachment_file_fingerprint")
        if uploaded and old_fingerprint and old_fingerprint == attachment_fingerprint:
            idempotent_skip = True
            attachment_info = {
                "attachment_id": record.get("attachment_id"),
                "attachment_filename": record.get("attachment_filename"),
            }
        else:
            try:
                for path in attachment_paths:
                    attachment_resp = client.upload_attachment(
                        workspace_id=int(payload["workspace_id"]),
                        entry_id=bug_id,
                        entry_type="bug",
                        file_path=path,
                    )
                    attachment = attachment_resp.get("data", {}).get("Attachment", {})
                    uploaded_attachments.append(
                        {
                            "attachment_id": attachment.get("id"),
                            "attachment_filename": attachment.get("filename"),
                            "file": path,
                        }
                    )
                if uploaded_attachments:
                    last = uploaded_attachments[-1]
                    attachment_info = {
                        "attachment_id": last.get("attachment_id"),
                        "attachment_filename": last.get("attachment_filename"),
                        "attachments": uploaded_attachments,
                    }
            except Exception as exc:
                upsert_state_record(
                    state=state,
                    idempotency_key=idempotency_key,
                    payload=payload,
                    bug_id=bug_id,
                    attachment_uploaded=False,
                    attachment_file_fingerprint=attachment_fingerprint,
                    attachment_id=record.get("attachment_id"),
                    attachment_filename=record.get("attachment_filename"),
                )
                save_submit_state(state_path, state)
                error_result = {
                    "error": "attachment_upload_failed",
                    "message": str(exc),
                    "bug_id": bug_id,
                    "bug_url": build_bug_url(payload["workspace_id"], bug_id),
                    "embed_images": embed_result,
                    "next_action": "retry_attachment_only",
                    "idempotency_key": idempotency_key,
                }
                if args.show_raw_response:
                    error_result["raw_response"] = {"create_bug": create_resp, "upload_attachment": None}
                print(json.dumps(error_result, ensure_ascii=False), file=sys.stderr)
                raise SystemExit(2)

            upsert_state_record(
                state=state,
                idempotency_key=idempotency_key,
                payload=payload,
                bug_id=bug_id,
                attachment_uploaded=True,
                attachment_file_fingerprint=attachment_fingerprint,
                attachment_id=attachment_info.get("attachment_id"),
                attachment_filename=attachment_info.get("attachment_filename"),
            )
            save_submit_state(state_path, state)
    else:
        upsert_state_record(
            state=state,
            idempotency_key=idempotency_key,
            payload=payload,
            bug_id=bug_id,
            attachment_uploaded=bool(record.get("attachment_uploaded", False)),
            attachment_file_fingerprint=record.get("attachment_file_fingerprint"),
            attachment_id=record.get("attachment_id"),
            attachment_filename=record.get("attachment_filename"),
        )
        save_submit_state(state_path, state)

    story_link = attempt_story_link(client, config, payload, bug_id, dry_run=False)

    result: Dict[str, Any] = {
        "bug_id": bug_id,
        "bug_url": build_bug_url(payload["workspace_id"], bug_id),
        "recovered": recovered,
        "idempotent_skip": idempotent_skip,
        "idempotency_key": idempotency_key,
        "embed_images": embed_result,
        "story_link": story_link,
        **attachment_info,
    }

    if args.show_raw_response:
        result["raw_response"] = {
            "create_bug": create_resp,
            "upload_attachment": attachment_resp,
        }

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
