"""查询 TAPD 缺陷 / 需求，供飞书机器人展示。"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Dict, Iterator, List, Optional, Tuple

from submit_bridge import workspace_root

RECENT_BUG_STATUS = "resolved"  # TAPD：已解决未关闭
QUERY_LIMIT = 10
QUERY_PAGE_SIZE = 50
QUERY_MAX_PAGES = 10
# 关键词过短时标题命中多为偶然匹配，视为低置信度并忽略
MIN_KEYWORD_LEN = 2

# region agent log
_AGENT_LOG_PATH = workspace_root() / "debug-00956b.log"


def _agent_debug_log(
    hypothesis_id: str,
    location: str,
    message: str,
    data: Dict[str, Any],
    *,
    run_id: str = "pre-fix",
) -> None:
    try:
        payload = {
            "sessionId": "00956b",
            "runId": run_id,
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with open(_AGENT_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass


# endregion

_TAPD_ENV_LOCK = threading.Lock()
_TAPD_ENV_KEYS = (
    "TAPD_ACCESS_TOKEN",
    "TAPD_API_USER",
    "TAPD_API_PASSWORD",
    "TAPD_API_BASE_URL",
    "TAPD_BASE_URL",
    "CURRENT_USER_NICK",
)


def _ensure_tapd_client() -> Any:
    root = workspace_root()
    client_dir = root / ".cursor" / "skills" / "tapd-plus" / "scripts"
    if not client_dir.is_dir():
        raise FileNotFoundError(f"未找到 tapd-plus scripts: {client_dir}")
    path = str(client_dir)
    if path not in sys.path:
        sys.path.insert(0, path)
    import tapd_client_stdlib as tapd  # type: ignore

    tapd.load_local_tapd_env()
    return tapd


@contextmanager
def _user_tapd_environ(open_id: Optional[str]) -> Iterator[None]:
    """在锁内临时覆盖 os.environ，避免多用户查询串 token。"""
    if not open_id:
        yield
        return
    from user_config_store import resolve_tapd_env, tapd_env_to_process_env

    overrides = tapd_env_to_process_env(resolve_tapd_env(open_id))
    with _TAPD_ENV_LOCK:
        saved: Dict[str, Optional[str]] = {k: os.environ.get(k) for k in _TAPD_ENV_KEYS}
        try:
            for k, v in overrides.items():
                os.environ[k] = v
            yield
        finally:
            for k, old in saved.items():
                if old is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = old


def _base_url(open_id: Optional[str] = None) -> str:
    if open_id:
        from user_config_store import resolve_tapd_env

        env = resolve_tapd_env(open_id)
        base = str(env.get("base_url") or "").strip()
        if base:
            return base.rstrip("/")
    return (os.environ.get("TAPD_BASE_URL") or "https://www.tapd.cn").rstrip("/")


def _workspace_and_iteration(open_id: Optional[str] = None) -> Tuple[int, str]:
    if open_id:
        from user_config_store import resolve_project_config

        cfg = resolve_project_config(open_id)
    else:
        from config_store import load_project_config

        cfg = load_project_config()
    wid = int(str(cfg.get("workspace_id") or "").strip())
    iteration = str((cfg.get("defaults") or {}).get("iteration") or "").strip()
    return wid, iteration


def _unwrap_list(resp: Dict[str, Any], entity_key: str) -> List[Dict[str, Any]]:
    """TAPD 列表常见结构：data 为 [{Bug: {...}}] 或 [{Story: {...}}]。"""
    data = resp.get("data")
    if data is None:
        return []
    if isinstance(data, dict):
        inner = data.get(entity_key) or data.get(entity_key.capitalize()) or data
        return [inner] if isinstance(inner, dict) else []
    if not isinstance(data, list):
        return []
    rows: List[Dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        if entity_key in item and isinstance(item[entity_key], dict):
            rows.append(item[entity_key])
        elif entity_key.capitalize() in item and isinstance(
            item[entity_key.capitalize()], dict
        ):
            rows.append(item[entity_key.capitalize()])
        else:
            rows.append(item)
    return rows


def _bug_url(base: str, workspace_id: int, bug_id: str) -> str:
    return f"{base}/{workspace_id}/bugtrace/bugs/view/{bug_id}"


def _story_url(base: str, workspace_id: int, story_id: str) -> str:
    return f"{base}/{workspace_id}/prong/stories/view/{story_id}"


def resolve_iteration_id(
    tapd: Any, workspace_id: int, iteration_name: str
) -> Optional[str]:
    if not iteration_name:
        return None
    resp = tapd.get_iterations(workspace_id, {"limit": 50, "name": iteration_name})
    rows = _unwrap_list(resp, "Iteration")
    if not rows:
        rows = _unwrap_list(resp, "iteration")
    name_norm = iteration_name.strip()
    for row in rows:
        if str(row.get("name") or "").strip() == name_norm:
            return str(row.get("id") or "").strip() or None
    for row in rows:
        if name_norm in str(row.get("name") or ""):
            return str(row.get("id") or "").strip() or None
    return None


def _resolve_reporter(open_id: Optional[str]) -> str:
    """当前用户昵称：project defaults.reporter 优先，其次 tapd_env.current_user_nick。"""
    if open_id:
        from user_config_store import resolve_project_config, resolve_tapd_env

        cfg = resolve_project_config(open_id)
        reporter = str((cfg.get("defaults") or {}).get("reporter") or "").strip()
        if reporter:
            return reporter
        return str(resolve_tapd_env(open_id).get("current_user_nick") or "").strip()
    from config_store import load_project_config

    cfg = load_project_config()
    reporter = str((cfg.get("defaults") or {}).get("reporter") or "").strip()
    if reporter:
        return reporter
    return str(os.environ.get("CURRENT_USER_NICK") or "").strip()


def recent_bug_list_title(open_id: Optional[str]) -> str:
    reporter = _resolve_reporter(open_id)
    parts: List[str] = []
    if reporter:
        parts.append(f"创建人「{reporter}」")
    parts.append("最近解决（resolved）")
    return " · ".join(parts) + f" Bug（最多 {QUERY_LIMIT} 条）："


def normalize_date_str(s: str) -> Optional[datetime]:
    text = (s or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def get_bug_created(bug: Dict[str, Any]) -> Optional[datetime]:
    for key in ("created", "created_at", "create_time"):
        dt = normalize_date_str(str(bug.get(key, "")))
        if dt:
            return dt
    return None


def resolve_created_bounds(
    *,
    days: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Tuple[Optional[datetime], Optional[datetime]]:
    start = normalize_date_str(str(start_date or ""))
    end = normalize_date_str(str(end_date or ""))
    if days is not None:
        try:
            n = max(1, int(days))
        except (TypeError, ValueError):
            n = 0
        if n:
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            start = today - timedelta(days=n - 1)
            end = today
    if end:
        end = end.replace(hour=23, minute=59, second=59)
    return start, end


def in_created_range(
    bug: Dict[str, Any],
    start: Optional[datetime],
    end: Optional[datetime],
) -> bool:
    if not start and not end:
        return True
    dt = get_bug_created(bug)
    if dt is None:
        return False
    if start and dt < start:
        return False
    if end and dt > end:
        return False
    return True


def bug_query_title(
    *,
    open_id: Optional[str] = None,
    keyword: Optional[str] = None,
    bug_id: Optional[str] = None,
    mine: bool = False,
    days: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    status: Optional[str] = None,
    recent_open: bool = False,
) -> str:
    if bug_id:
        return f"Bug ID={bug_id}："
    if recent_open and not any([days, start_date, end_date, keyword]):
        return recent_bug_list_title(open_id)

    parts: List[str] = []
    if mine or recent_open:
        reporter = _resolve_reporter(open_id)
        parts.append("我提交的" + (f"（{reporter}）" if reporter else ""))
    if days:
        parts.append(f"近 {int(days)} 天")
    elif start_date or end_date:
        if start_date and end_date:
            parts.append(f"{start_date}～{end_date}")
        elif start_date:
            parts.append(f"{start_date} 起")
        else:
            parts.append(f"至 {end_date}")
    st = str(status or "").strip()
    if st:
        parts.append(f"状态={st}")
    if keyword:
        parts.append(f"标题含「{keyword}」")
    if not parts:
        parts.append("Bug 查询")
    return " · ".join(parts) + f"（最多 {QUERY_LIMIT} 条）："


def _keyword_match_confidence(keyword: str, title: str) -> str:
    """标题关键词匹配置信度：须完整子串命中且关键词足够长。"""
    kw = (keyword or "").strip().casefold()
    title_cf = (title or "").strip().casefold()
    if not kw or not title_cf:
        return "low"
    if len(kw) < MIN_KEYWORD_LEN:
        return "low"
    if kw not in title_cf:
        return "low"
    return "high"


def _fetch_bugs_pages(
    tapd: Any,
    workspace_id: int,
    base_opts: Dict[str, Any],
    *,
    max_pages: int = QUERY_MAX_PAGES,
) -> List[Dict[str, Any]]:
    all_rows: List[Dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        opts = dict(base_opts)
        opts["page"] = page
        opts["limit"] = int(opts.get("limit") or QUERY_PAGE_SIZE)
        resp = tapd.get_bugs(workspace_id, opts)
        if resp.get("status") not in (1, "1", None) and resp.get("info") not in (
            None,
            "success",
        ):
            if resp.get("error") or (
                isinstance(resp.get("data"), dict) and resp["data"].get("error")
            ):
                raise RuntimeError(json.dumps(resp, ensure_ascii=False)[:500])
        rows = _unwrap_list(resp, "Bug")
        if not rows:
            break
        all_rows.extend(rows)
        if len(rows) < int(opts["limit"]):
            break
    return all_rows


def query_bugs(
    *,
    keyword: Optional[str] = None,
    bug_id: Optional[str] = None,
    recent_open: bool = False,
    mine: bool = False,
    days: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    status: Optional[str] = None,
    open_id: Optional[str] = None,
) -> Tuple[List[Dict[str, str]], str]:
    """
    返回 (rows, error_or_empty)。
    row: id, title, status, url, current_owner, reporter, created
    """
    with _user_tapd_environ(open_id):
        tapd = _ensure_tapd_client()
        workspace_id, _iteration_name = _workspace_and_iteration(open_id)
        base = _base_url(open_id)
        start_bound, end_bound = resolve_created_bounds(
            days=days, start_date=start_date, end_date=end_date
        )
        need_time_filter = bool(start_bound or end_bound)
        status_filter = str(status or "").strip().lower()
        if recent_open and not status_filter and not need_time_filter and not keyword:
            status_filter = RECENT_BUG_STATUS

        use_mine = bool(mine or recent_open)
        reporter = _resolve_reporter(open_id) if use_mine else ""

        opts: Dict[str, Any] = {
            "fields": "id,title,status,priority,severity,current_owner,created,reporter,iteration_id",
            "order": "created desc",
        }

        # 是否需要分页本地过滤
        complex_filter = bool(
            need_time_filter
            or (use_mine and keyword)
            or (status_filter and keyword and use_mine)
            or (use_mine and need_time_filter)
            or (need_time_filter and (keyword or status_filter or use_mine))
        )

        if bug_id:
            opts["id"] = str(bug_id).strip()
            opts["limit"] = QUERY_LIMIT
            complex_filter = False
        elif complex_filter or need_time_filter:
            opts["limit"] = QUERY_PAGE_SIZE
            if reporter:
                opts["reporter"] = reporter
            # 状态可交给 API 预筛（时间仍本地过滤）
            if status_filter:
                opts["status"] = status_filter
        elif recent_open:
            opts["status"] = RECENT_BUG_STATUS
            opts["limit"] = QUERY_LIMIT
            if reporter:
                opts["reporter"] = reporter
        elif keyword:
            opts["title"] = keyword
            opts["limit"] = QUERY_LIMIT
            if status_filter:
                opts["status"] = status_filter
            if reporter:
                opts["reporter"] = reporter
        else:
            opts["limit"] = QUERY_LIMIT
            if status_filter:
                opts["status"] = status_filter
            if reporter:
                opts["reporter"] = reporter

        # region agent log
        _agent_debug_log(
            "H1,H2,H3",
            "query_bridge.py:query_bugs:before_get_bugs",
            "Resolved query mode and filters",
            {
                "complexFilter": complex_filter or need_time_filter,
                "filterKeys": sorted(opts.keys()),
                "reporterConfigured": bool(opts.get("reporter")),
                "mine": use_mine,
                "days": days,
                "status": status_filter,
                "keywordLength": len(str(keyword or "").strip()),
            },
            run_id="post-fix",
        )
        # endregion

        try:
            if bug_id:
                resp = tapd.get_bugs(workspace_id, {**opts, "page": 1})
                if resp.get("status") not in (1, "1", None) and resp.get("info") not in (
                    None,
                    "success",
                ):
                    if resp.get("error") or (
                        isinstance(resp.get("data"), dict) and resp["data"].get("error")
                    ):
                        return [], json.dumps(resp, ensure_ascii=False)[:500]
                rows_raw = _unwrap_list(resp, "Bug")
            elif complex_filter or need_time_filter:
                rows_raw = _fetch_bugs_pages(tapd, workspace_id, opts)
            else:
                resp = tapd.get_bugs(workspace_id, {**opts, "page": 1})
                if resp.get("status") not in (1, "1", None) and resp.get("info") not in (
                    None,
                    "success",
                ):
                    if resp.get("error") or (
                        isinstance(resp.get("data"), dict) and resp["data"].get("error")
                    ):
                        return [], json.dumps(resp, ensure_ascii=False)[:500]
                rows_raw = _unwrap_list(resp, "Bug")
        except RuntimeError as e:
            return [], str(e)

        # region agent log
        _agent_debug_log(
            "H1,H2,H3",
            "query_bridge.py:query_bugs:after_get_bugs",
            "Received TAPD bug candidates",
            {
                "candidateCount": len(rows_raw),
                "statuses": sorted(
                    {
                        str(row.get("status") or "").strip().lower()
                        for row in rows_raw
                        if isinstance(row, dict)
                    }
                ),
                "createdPresentCount": sum(
                    bool(row.get("created")) for row in rows_raw if isinstance(row, dict)
                ),
            },
            run_id="post-fix",
        )
        # endregion

        if bug_id:
            requested_id = str(bug_id).strip()
            rows_raw = [
                r for r in rows_raw if str(r.get("id") or "").strip() == requested_id
            ]
        else:
            filtered: List[Dict[str, Any]] = []
            for r in rows_raw:
                if status_filter and str(r.get("status") or "").strip().lower() != status_filter:
                    continue
                if use_mine and reporter and reporter not in str(r.get("reporter") or ""):
                    continue
                if need_time_filter and not in_created_range(r, start_bound, end_bound):
                    continue
                if (
                    keyword
                    and _keyword_match_confidence(
                        str(keyword), str(r.get("title") or "")
                    )
                    != "high"
                ):
                    continue
                filtered.append(r)
            rows_raw = filtered

        rows_raw.sort(key=lambda r: get_bug_created(r) or datetime.min, reverse=True)
        rows_raw = rows_raw[:QUERY_LIMIT]

        out: List[Dict[str, str]] = []
        for r in rows_raw:
            bid = str(r.get("id") or "").strip()
            if not bid:
                continue
            out.append(
                {
                    "id": bid,
                    "title": str(r.get("title") or "").strip() or "（无标题）",
                    "status": str(r.get("status") or "").strip(),
                    "url": _bug_url(base, workspace_id, bid),
                    "current_owner": str(r.get("current_owner") or "").strip(),
                    "reporter": str(r.get("reporter") or "").strip(),
                    "created": str(r.get("created") or "").strip(),
                }
            )
        return out, ""


def query_stories(
    *,
    keyword: Optional[str] = None,
    story_id: Optional[str] = None,
    recent_in_iteration: bool = False,
    open_id: Optional[str] = None,
) -> Tuple[List[Dict[str, str]], str]:
    """
    返回 (rows, error_or_empty)。
    row: id, name, status, url
    """
    with _user_tapd_environ(open_id):
        tapd = _ensure_tapd_client()
        workspace_id, iteration_name = _workspace_and_iteration(open_id)
        base = _base_url(open_id)
        opts: Dict[str, Any] = {
            "entity_type": "stories",
            "limit": QUERY_LIMIT,
            "page": 1,
            "fields": "id,name,status,owner,iteration_id,created",
        }
        if story_id:
            opts["id"] = story_id
        elif keyword:
            opts["name"] = keyword
        elif recent_in_iteration and iteration_name:
            iid = resolve_iteration_id(tapd, workspace_id, iteration_name)
            if iid:
                opts["iteration_id"] = iid

        resp = tapd.get_stories(workspace_id, opts)
        rows_raw = _unwrap_list(resp, "Story")[:QUERY_LIMIT]

        out: List[Dict[str, str]] = []
        for r in rows_raw:
            sid = str(r.get("id") or "").strip()
            if not sid:
                continue
            out.append(
                {
                    "id": sid,
                    "name": str(r.get("name") or "").strip() or "（无标题）",
                    "status": str(r.get("status") or "").strip(),
                    "url": _story_url(base, workspace_id, sid),
                }
            )
        return out, ""


def format_bug_list(rows: List[Dict[str, str]], *, title: str) -> str:
    if not rows:
        return f"{title}\n（无结果）"
    lines = [title, ""]
    for i, r in enumerate(rows, 1):
        lines.append(f"{i}. [{r['id']}] {r['title']}")
        meta: List[str] = []
        if r.get("status"):
            meta.append(f"状态：{r['status']}")
        if r.get("created"):
            meta.append(f"创建：{r['created']}")
        if meta:
            lines.append("   " + " · ".join(meta))
        lines.append(f"   {r['url']}")
    return "\n".join(lines)


def format_story_list(rows: List[Dict[str, str]], *, title: str) -> str:
    if not rows:
        return f"{title}\n（无结果）"
    lines = [title, ""]
    for i, r in enumerate(rows, 1):
        lines.append(f"{i}. [{r['id']}] {r['name']}")
        if r.get("status"):
            lines.append(f"   状态：{r['status']}")
        lines.append(f"   {r['url']}")
    return "\n".join(lines)


def looks_like_id(text: str) -> bool:
    s = text.strip()
    return bool(s) and s.isdigit()
