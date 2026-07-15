"""
TAPD 缺陷与需求关联脚本：
- 按 bug id / 链接 / 标题定位缺陷
- 显式指定 story_id，或按标题/描述自动匹配需求
- 调用 POST relations 建立关联（幂等：已关联则跳过）
"""

from __future__ import annotations

import argparse
import difflib
import importlib.util
import json
import os
import re
import sys
from html import unescape
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_TAPD_BASE_URL = "https://www.tapd.cn"

HIGH_SCORE = 0.55
HIGH_GAP = 0.20
MEDIUM_SCORE = 0.35


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
    parser = argparse.ArgumentParser(description="关联 TAPD 缺陷与需求")
    parser.add_argument("--payload-file", help="UTF-8 JSON 文件路径，推荐")
    parser.add_argument("--config-file", help="项目本地配置文件路径（可选）")
    parser.add_argument("--workspace-id", dest="workspace_id")
    parser.add_argument("--bug-id", dest="bug_id", help="缺陷 ID（长 id 或短 id）")
    parser.add_argument("--bug-url", dest="bug_url", help="缺陷链接")
    parser.add_argument("--title", help="按缺陷标题匹配（须唯一命中）")
    parser.add_argument("--story-id", dest="story_id", help="需求 ID（显式指定时跳过自动匹配）")
    parser.add_argument("--story-url", dest="story_url", help="需求链接")
    parser.add_argument("--story-name", dest="story_name", help="按需求标题精确/模糊匹配")
    parser.add_argument("--query", help="自动匹配用的查询文本（默认从缺陷标题+描述提取）")
    parser.add_argument(
        "--iteration",
        help="限定匹配的需求所属迭代名称（默认取 defaults.iteration）",
    )
    parser.add_argument(
        "--match-only",
        action="store_true",
        help="仅匹配候选需求，不创建关联",
    )
    parser.add_argument("--force", action="store_true", help="低置信度也强制关联（须已指定 story_id 或唯一候选）")
    parser.add_argument("--dry-run", action="store_true", help="预览，不实际调用 relations")
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
    link_defaults = config.get("link_story", {})
    if isinstance(link_defaults, dict) and str(link_defaults.get(key, "")).strip():
        return link_defaults.get(key)
    if str(config.get(key, "")).strip():
        return config.get(key)
    return None


def parse_id_from_url(url: str, kind: str) -> str:
    text = (url or "").strip()
    if not text:
        return ""
    if kind == "bug":
        for pat in (r"/bugs/view/(\d+)", r"bug_id=(\d+)"):
            m = re.search(pat, text)
            if m:
                return m.group(1)
    if kind == "story":
        for pat in (r"/stories/view/(\d+)", r"story_id=(\d+)"):
            m = re.search(pat, text)
            if m:
                return m.group(1)
    return ""


def normalize_long_id(client: Any, workspace_id: str, raw_id: str) -> str:
    raw = str(raw_id or "").strip()
    if not raw:
        return ""
    try:
        return client.to_long_id(raw, int(workspace_id))
    except ValueError:
        return raw


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


def fetch_bug(client: Any, workspace_id: str, bug_id: str) -> Dict[str, Any]:
    long_id = normalize_long_id(client, workspace_id, bug_id)
    resp = client.request(
        "GET",
        "bugs",
        params={"workspace_id": workspace_id, "id": long_id, "limit": 1},
    )
    bug = extract_entity(resp, "Bug")
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
            "fields": "id,title,description",
        },
    )
    candidates: List[Dict[str, Any]] = []
    for row in resp.get("data", []) or []:
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
    listing = "; ".join(f'{b.get("id")}={b.get("title")}' for b in candidates[:8])
    raise ValueError(f"标题「{title_norm}」匹配到多个缺陷，请改用 bug_id。候选：{listing}")


def fetch_story(client: Any, workspace_id: str, story_id: str) -> Dict[str, Any]:
    long_id = normalize_long_id(client, workspace_id, story_id)
    resp = client.request(
        "GET",
        "stories",
        params={"workspace_id": workspace_id, "id": long_id, "limit": 1},
    )
    story = extract_entity(resp, "Story")
    if not story:
        raise ValueError(f"未找到需求：{long_id}")
    return story


def resolve_iteration_id(
    client: Any, workspace_id: str, iteration_name: Optional[str]
) -> Optional[str]:
    if not str(iteration_name or "").strip():
        return None
    name_norm = iteration_name.strip()
    resp = client.request(
        "GET",
        "iterations",
        params={"workspace_id": workspace_id, "name": name_norm, "limit": 50, "page": 1},
    )
    for row in resp.get("data", []) or []:
        it = row.get("Iteration", row) if isinstance(row, dict) else {}
        if not isinstance(it, dict):
            continue
        iid = str(it.get("id", "")).strip()
        iname = str(it.get("name", "")).strip()
        if iid and iname == name_norm:
            return iid
    for row in resp.get("data", []) or []:
        it = row.get("Iteration", row) if isinstance(row, dict) else {}
        if not isinstance(it, dict):
            continue
        iid = str(it.get("id", "")).strip()
        iname = str(it.get("name", "")).strip()
        if iid and name_norm in iname:
            return iid
    raise ValueError(f"未找到迭代：{name_norm}")


def fetch_all_stories_paged(
    client: Any,
    workspace_id: str,
    iteration_id: Optional[str] = None,
    max_pages: int = 20,
    page_limit: int = 200,
) -> List[Dict[str, Any]]:
    stories: List[Dict[str, Any]] = []
    page = 1
    while page <= max_pages:
        params: Dict[str, Any] = {
            "workspace_id": workspace_id,
            "limit": page_limit,
            "page": page,
            "fields": "id,name,status,owner,iteration_id",
        }
        if iteration_id:
            params["iteration_id"] = iteration_id
        resp = client.request("GET", "stories", params=params)
        rows = resp.get("data", []) or []
        if not rows:
            break
        for row in rows:
            story = row.get("Story", row) if isinstance(row, dict) else {}
            if isinstance(story, dict) and str(story.get("id", "")).strip():
                stories.append(story)
        if len(rows) < page_limit:
            break
        page += 1
    return stories


def fetch_stories(
    client: Any,
    workspace_id: str,
    iteration_id: Optional[str] = None,
    page_limit: int = 200,
) -> List[Dict[str, Any]]:
    return fetch_all_stories_paged(
        client, workspace_id, iteration_id=iteration_id, page_limit=page_limit, max_pages=1
    )


def strip_html(text: str) -> str:
    s = unescape(str(text or ""))
    s = re.sub(r"<br\s*/?>", " ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def normalize_text(text: str) -> str:
    s = strip_html(text)
    s = re.sub(r"【[^】]*】", " ", s)
    s = re.sub(r"[^\w\u4e00-\u9fff]+", " ", s, flags=re.UNICODE)
    return re.sub(r"\s+", " ", s).strip().lower()


def tokenize(text: str) -> List[str]:
    norm = normalize_text(text)
    tokens = re.findall(r"[\u4e00-\u9fff]{2,}|\w{2,}", norm)
    return tokens


def score_pair(query: str, story_name: str) -> float:
    q_norm = normalize_text(query)
    n_norm = normalize_text(story_name)
    if not q_norm or not n_norm:
        return 0.0
    ratio = difflib.SequenceMatcher(None, q_norm, n_norm).ratio()
    q_tokens = set(tokenize(query))
    n_tokens = set(tokenize(story_name))
    jaccard = 0.0
    if q_tokens and n_tokens:
        jaccard = len(q_tokens & n_tokens) / len(q_tokens | n_tokens)
    substring_boost = 0.0
    if n_norm in q_norm or q_norm in n_norm:
        substring_boost = 0.15
    return min(1.0, 0.45 * ratio + 0.45 * jaccard + substring_boost)


def classify_confidence(score: float, second_score: float) -> str:
    gap = score - second_score
    if score >= HIGH_SCORE and gap >= HIGH_GAP:
        return "high"
    if score >= MEDIUM_SCORE:
        return "medium"
    return "low"


def build_story_url(workspace_id: str, story_id: str) -> str:
    base = os.environ.get("TAPD_BASE_URL", DEFAULT_TAPD_BASE_URL).rstrip("/")
    return f"{base}/{workspace_id}/prong/stories/view/{story_id}"


def build_bug_url(workspace_id: str, bug_id: str) -> str:
    base = os.environ.get("TAPD_BASE_URL", DEFAULT_TAPD_BASE_URL).rstrip("/")
    return f"{base}/{workspace_id}/bugtrace/bugs/view/{bug_id}"


def extract_description_keywords(description: str, limit: int = 160) -> str:
    text = strip_html(description)
    for label in (
        "问题概述",
        "问题描述",
        "复现步骤",
        "实际结果",
        "期望结果",
        "影响范围",
    ):
        text = text.replace(label, " ")
    text = re.sub(r"\d+[\)）]", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:limit]


def build_query_variants(bug: Dict[str, Any], extra_query: str = "") -> List[str]:
    title = str(bug.get("title", "")).strip()
    body = re.sub(r"^【[^】]*】", "", title).strip()
    desc = extract_description_keywords(str(bug.get("description", "")))
    extra = str(extra_query or "").strip()
    variants: List[str] = []
    for item in (body, title, desc, f"{body} {desc}".strip(), extra):
        norm = normalize_text(item)
        if norm and norm not in {normalize_text(v) for v in variants}:
            variants.append(item)
    return variants


def build_query_from_bug(bug: Dict[str, Any], extra_query: str = "") -> str:
    variants = build_query_variants(bug, extra_query)
    return variants[0] if variants else ""


def score_story(query_variants: List[str], story_name: str, bug_title: str = "") -> float:
    if not query_variants:
        return 0.0
    base = max(score_pair(q, story_name) for q in query_variants)
    name_norm = normalize_text(story_name)
    raw_title = strip_html(bug_title)
    bracket = re.search(r"【([^】]+)】", raw_title)
    if bracket and normalize_text(bracket.group(1)) == name_norm:
        return min(1.0, max(base, 0.85))
    title_norm = normalize_text(bug_title)
    if name_norm and title_norm and name_norm in title_norm:
        return min(1.0, max(base, 0.72))
    return base


def rank_stories(
    query_variants: List[str],
    stories: List[Dict[str, Any]],
    workspace_id: str,
    bug_title: str = "",
    top_n: int = 5,
) -> List[Dict[str, Any]]:
    scored: List[Tuple[float, Dict[str, Any]]] = []
    for story in stories:
        name = str(story.get("name", "")).strip()
        if not name:
            continue
        scored.append((score_story(query_variants, name, bug_title), story))
    scored.sort(key=lambda x: x[0], reverse=True)
    results: List[Dict[str, Any]] = []
    for idx, (score, story) in enumerate(scored[:top_n]):
        second = scored[idx + 1][0] if idx + 1 < len(scored) else 0.0
        conf = classify_confidence(score, second if idx == 0 else 0.0)
        sid = str(story.get("id", "")).strip()
        results.append(
            {
                "id": sid,
                "name": str(story.get("name", "")).strip(),
                "status": str(story.get("status", "")).strip(),
                "owner": str(story.get("owner", "")).strip(),
                "score": round(score, 4),
                "confidence": conf if idx == 0 else classify_confidence(score, 0.0),
                "url": build_story_url(workspace_id, sid),
            }
        )
    return results


def get_related_story_ids(client: Any, workspace_id: str, bug_id: str) -> List[str]:
    resp = client.request(
        "GET",
        "bugs/get_related_stories",
        params={"workspace_id": workspace_id, "bug_id": bug_id},
    )
    ids: List[str] = []
    for row in resp.get("data", []) or []:
        if isinstance(row, dict):
            sid = str(row.get("story_id", "")).strip()
            if sid:
                ids.append(sid)
    return ids


def create_relation(
    client: Any,
    workspace_id: str,
    story_id: str,
    bug_id: str,
) -> Dict[str, Any]:
    payload = {
        "workspace_id": int(workspace_id),
        "source_type": "story",
        "target_type": "bug",
        "source_id": int(story_id),
        "target_id": int(bug_id),
    }
    return client.request("POST", "relations", data=payload)


def load_payload(args: argparse.Namespace, config: Dict[str, Any]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    if args.payload_file:
        with Path(args.payload_file).resolve().open("r", encoding="utf-8-sig") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("payload 文件必须是 JSON 对象")
        payload.update(data)

    for key, val in [
        ("workspace_id", args.workspace_id),
        ("bug_id", args.bug_id),
        ("bug_url", args.bug_url),
        ("title", args.title),
        ("story_id", args.story_id),
        ("story_url", args.story_url),
        ("story_name", args.story_name),
        ("query", args.query),
        ("iteration", args.iteration),
    ]:
        if val is not None:
            payload[key] = val

    payload["match_only"] = bool(payload.get("match_only")) or args.match_only
    payload["force"] = bool(payload.get("force")) or args.force

    ws = pick_value(payload, config, "workspace_id")
    if ws is not None:
        payload["workspace_id"] = str(ws)
    if not str(payload.get("workspace_id", "")).strip():
        raise ValueError("缺少 workspace_id：请配置 project_config.json 或在 payload 中提供")

    if not str(payload.get("iteration", "")).strip():
        picked = pick_value(payload, config, "iteration")
        if picked is not None:
            payload["iteration"] = str(picked).strip()

    link_cfg = config.get("link_story", {})
    if isinstance(link_cfg, dict):
        for key in ("match_scope", "fallback_project"):
            if key not in payload and link_cfg.get(key) is not None:
                payload[key] = link_cfg[key]
        # story_id / story_name / story_url：payload/CLI 优先，否则读 link_story 配置
        for key in ("story_id", "story_name", "story_url"):
            if str(payload.get(key, "")).strip():
                continue
            cfg_val = str(link_cfg.get(key, "")).strip()
            if cfg_val:
                payload[key] = link_cfg[key]

    return payload


def resolve_bug(client: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
    workspace_id = payload["workspace_id"]
    if str(payload.get("bug_id", "")).strip():
        return fetch_bug(client, workspace_id, payload["bug_id"])
    if str(payload.get("bug_url", "")).strip():
        bug_id = parse_id_from_url(payload["bug_url"], "bug")
        if not bug_id:
            raise ValueError(f"无法从链接解析 bug_id：{payload['bug_url']}")
        return fetch_bug(client, workspace_id, bug_id)
    if str(payload.get("title", "")).strip():
        return search_bug_by_title(client, workspace_id, payload["title"])
    raise ValueError("缺少缺陷定位信息：请提供 bug_id、bug_url 或 title")


def resolve_story(
    client: Any,
    payload: Dict[str, Any],
    bug: Dict[str, Any],
    iteration_id: Optional[str],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    workspace_id = payload["workspace_id"]
    meta: Dict[str, Any] = {"source": "explicit"}

    if str(payload.get("story_id", "")).strip():
        story = fetch_story(client, workspace_id, payload["story_id"])
        meta["source"] = "explicit_id"
        return story, meta
    if str(payload.get("story_url", "")).strip():
        sid = parse_id_from_url(payload["story_url"], "story")
        if not sid:
            raise ValueError(f"无法从链接解析 story_id：{payload['story_url']}")
        story = fetch_story(client, workspace_id, sid)
        meta["source"] = "explicit_url"
        return story, meta
    if str(payload.get("story_name", "")).strip():
        name = payload["story_name"].strip()
        stories = fetch_stories(client, workspace_id, iteration_id)
        exact = [s for s in stories if str(s.get("name", "")).strip() == name]
        if len(exact) == 1:
            meta["source"] = "story_name_exact"
            return exact[0], meta
        fuzzy = [s for s in stories if name in str(s.get("name", ""))]
        if len(fuzzy) == 1:
            meta["source"] = "story_name_fuzzy"
            return fuzzy[0], meta
        if not fuzzy:
            raise ValueError(f"未找到需求：{name}")
        listing = "; ".join(f'{s.get("id")}={s.get("name")}' for s in fuzzy[:8])
        raise ValueError(f"需求名称「{name}」匹配到多个，请改用 story_id。候选：{listing}")

    query_variants = (
        [str(payload.get("query", "")).strip()]
        if str(payload.get("query", "")).strip()
        else build_query_variants(bug)
    )
    query = query_variants[0] if query_variants else ""

    scope = str(payload.get("match_scope") or "iteration").strip()
    fallback_project = bool(payload.get("fallback_project", True))

    iteration_stories = (
        fetch_all_stories_paged(client, workspace_id, iteration_id=iteration_id)
        if iteration_id
        else []
    )
    project_stories = fetch_all_stories_paged(client, workspace_id, iteration_id=None)

    stories: List[Dict[str, Any]]
    search_scope = "iteration"
    if scope == "project":
        stories = project_stories
        search_scope = "project"
    elif iteration_stories:
        stories = iteration_stories
    else:
        stories = project_stories
        search_scope = "project"

    if not stories:
        scope_label = f"迭代 {payload.get('iteration')}" if iteration_id else "项目"
        raise ValueError(f"{scope_label} 下未找到可匹配的需求")

    bug_title = str(bug.get("title", "")).strip()

    candidates = rank_stories(query_variants, stories, workspace_id, bug_title, top_n=5)
    best_score = candidates[0]["score"] if candidates else 0.0
    if (
        scope != "project"
        and fallback_project
        and project_stories
        and best_score < MEDIUM_SCORE
    ):
        project_candidates = rank_stories(
            query_variants, project_stories, workspace_id, bug_title, top_n=5
        )
        if project_candidates and project_candidates[0]["score"] > best_score:
            stories = project_stories
            candidates = project_candidates
            search_scope = "project_fallback"

    if not candidates:
        raise ValueError("未能从需求列表中计算出候选")
    best = candidates[0]
    second_score = candidates[1]["score"] if len(candidates) > 1 else 0.0
    confidence = classify_confidence(best["score"], second_score)
    meta.update(
        {
            "source": "name_match",
            "query": query,
            "search_scope": search_scope,
            "stories_total": len(stories),
            "confidence": confidence,
            "candidates": candidates,
            "best_match": best,
        }
    )
    story = fetch_story(client, workspace_id, best["id"])
    return story, meta


def link_bug_to_story(
    client: Any,
    workspace_id: str,
    bug: Dict[str, Any],
    link_payload: Dict[str, Any],
    iteration_id: Optional[str] = None,
    *,
    dry_run: bool = False,
    match_only: bool = False,
) -> Dict[str, Any]:
    """
    搜索并（可选）关联需求。供 link_bug_story.py 与 submit_bug_with_attachment.py 共用。
    link_payload 可含 story_id / story_name / query / force / match_scope / fallback_project。
    """
    bug_id = normalize_long_id(client, workspace_id, str(bug.get("id", "")))
    preview = dry_run and (not str(bug.get("id", "")).strip() or str(bug.get("id", "")).strip() == "dry-run")

    story, match_meta = resolve_story(client, link_payload, bug, iteration_id)
    if link_payload.get("story_link_from_config"):
        match_meta["link_from_config"] = True
        src = str(match_meta.get("source", "")).strip()
        if src and not src.startswith("config_"):
            match_meta["source"] = f"config_{src}"
    story_id = normalize_long_id(client, workspace_id, str(story.get("id", "")))
    match_meta.setdefault("story_url", build_story_url(workspace_id, story_id))

    if preview:
        existing: List[str] = []
        already_linked = False
    else:
        existing = get_related_story_ids(client, workspace_id, bug_id)
        already_linked = story_id in existing

    confidence = match_meta.get("confidence", "explicit")
    can_auto_link = (
        match_meta.get("source") != "name_match"
        or confidence == "high"
        or link_payload.get("force")
    )

    result: Dict[str, Any] = {
        "bug_id": bug_id,
        "story_id": story_id,
        "story_name": str(story.get("name", "")).strip(),
        "story_url": build_story_url(workspace_id, story_id),
        "already_linked": already_linked,
        "existing_story_ids": existing,
        "match": match_meta,
        "confidence": confidence,
        "can_auto_link": can_auto_link,
    }

    if dry_run or match_only:
        if match_only:
            result["action"] = "match_only"
        elif not can_auto_link and match_meta.get("source") == "name_match":
            result["action"] = "need_confirm"
            result["message"] = "匹配置信度不足，请确认候选需求后带 story_id 或 force 再关联"
        elif already_linked:
            result["action"] = "skip_already_linked"
        else:
            result["action"] = "would_link"
        return result

    if already_linked:
        result["action"] = "skip_already_linked"
        result["linked"] = False
        return result

    if not can_auto_link:
        result["action"] = "need_confirm"
        result["linked"] = False
        result["message"] = "匹配置信度不足，请确认候选需求后带 story_id 或 force 再关联"
        return result

    relation_resp = create_relation(client, workspace_id, story_id, bug_id)
    result["linked"] = True
    result["action"] = "linked"
    relation = relation_resp.get("data", {}).get("Relation", {})
    if relation:
        result["relation_id"] = relation.get("id")
    return result


def _pick_story_link_field(
    submit_payload: Dict[str, Any],
    link_cfg: Dict[str, Any],
    key: str,
) -> Tuple[Optional[Any], Optional[str]]:
    """返回值：(字段值, 来源 submit|config|None)。"""
    submit_val = str(submit_payload.get(key, "")).strip()
    if submit_val:
        return submit_payload[key], "submit"
    cfg_val = str(link_cfg.get(key, "")).strip()
    if cfg_val:
        return link_cfg[key], "config"
    return None, None


def build_link_payload_from_config(
    config: Dict[str, Any],
    submit_payload: Dict[str, Any],
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """从提 bug payload + project_config 组装关联参数。

    需求关联优先级：submit payload/CLI > link_story 配置 > 自动标题匹配。
    link_story.story_id / story_name / story_url 为空时走自动匹配。
    """
    link_cfg = config.get("link_story", {})
    if not isinstance(link_cfg, dict):
        link_cfg = {}

    out: Dict[str, Any] = {
        "workspace_id": submit_payload.get("workspace_id"),
        "match_scope": link_cfg.get("match_scope", "iteration"),
        "fallback_project": link_cfg.get("fallback_project", True),
    }

    story_from_config = False
    for key in ("story_id", "story_name", "story_url"):
        val, source = _pick_story_link_field(submit_payload, link_cfg, key)
        if val is not None:
            out[key] = val
            if source == "config":
                story_from_config = True

    for key in ("query", "iteration"):
        if str(submit_payload.get(key, "")).strip():
            out[key] = submit_payload[key]

    if story_from_config:
        out["story_link_from_config"] = True

    if link_cfg.get("force"):
        out["force"] = True
    if submit_payload.get("force_link_story"):
        out["force"] = True

    if extra:
        out.update(extra)
    return out


def story_link_enabled(config: Dict[str, Any], submit_payload: Dict[str, Any]) -> bool:
    if submit_payload.get("skip_link_story"):
        return False
    link_cfg = config.get("link_story", {})
    if isinstance(link_cfg, dict) and link_cfg.get("enabled") is False:
        return False
    return True


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = parse_args()
    config = load_config(args)
    payload = load_payload(args, config)
    client = load_tapd_client()

    bug = resolve_bug(client, payload)
    bug_id = normalize_long_id(client, payload["workspace_id"], str(bug.get("id", "")))
    workspace_id = payload["workspace_id"]

    iteration_id = None
    if str(payload.get("iteration", "")).strip():
        iteration_id = resolve_iteration_id(client, workspace_id, payload["iteration"])

    result = link_bug_to_story(
        client,
        workspace_id,
        bug,
        payload,
        iteration_id,
        dry_run=bool(args.dry_run),
        match_only=bool(payload.get("match_only")),
    )
    result.update(
        {
            "workspace_id": workspace_id,
            "bug_title": str(bug.get("title", "")).strip(),
            "bug_url": build_bug_url(workspace_id, result["bug_id"]),
        }
    )

    if args.dry_run:
        result["dry_run"] = True
    if payload.get("match_only"):
        result["match_only"] = True

    if result.get("action") == "need_confirm" and not args.dry_run and not payload.get("match_only"):
        print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(3)

    if args.show_raw_response and result.get("action") == "linked":
        result["raw_response"] = result.get("raw_response")

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
