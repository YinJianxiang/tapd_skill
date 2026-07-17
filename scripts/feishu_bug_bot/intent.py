"""自然语言意图识别：私聊 agent 优先，规则负责槽位校验与时间解析。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from config_store import DEFAULT_EDITABLE_PATHS, PATH_LABELS, path_label

ALLOWED_INTENTS = (
    "query_bug",
    "query_story",
    "close_bug",
    "config_view",
    "config_edit",
    "submit_bug",
    "help",
    "clarify",
)

# 配置字段别名 → path
_CONFIG_ALIASES: List[Tuple[re.Pattern[str], str]] = [
    (re.compile(r"发现版本|版本号|^版本$"), "defaults.version"),
    (re.compile(r"迭代|sprint"), "defaults.iteration"),
    (re.compile(r"标题前缀|前缀|^名称$"), "defaults.name"),
    (re.compile(r"提单人|报告人|reporter"), "defaults.reporter"),
    (re.compile(r"前端负责人|前端处理人"), "module_owner_map.前端"),
    (re.compile(r"后端负责人|后端处理人"), "module_owner_map.后端"),
    (re.compile(r"自动关联需求|关联需求开关"), "link_story.enabled"),
    (re.compile(r"固定需求\s*id|需求\s*id"), "link_story.story_id"),
    (re.compile(r"固定需求名称|需求名称"), "link_story.story_name"),
    (re.compile(r"需求匹配范围|匹配范围"), "link_story.match_scope"),
]

_TIME_NOISE_RE = re.compile(
    r"("
    r"近\s*[0-9一二两三四五六七八九十]+\s*天|最近\s*[0-9一二两三四五六七八九十]+\s*天|这\s*[0-9一二两三四五六七八九十]+\s*天|"
    r"今天|今日|本周|这周|近一周|最近一周|近一个星期|"
    r"近一个月|最近一个月|本月"
    r")",
    re.I,
)
_MINE_RE = re.compile(r"(我\s*提的|我\s*创建的|我\s*提交的|我\s*报的|我的)")
_STATUS_MAP = {
    "resolved": "resolved",
    "已解决": "resolved",
    "未关闭": "resolved",
    "closed": "closed",
    "已关闭": "closed",
    "new": "new",
    "新建": "new",
    "in_progress": "in_progress",
    "接受/处理": "in_progress",
    "处理中": "in_progress",
}


@dataclass
class IntentResult:
    kind: str = "clarify"
    confidence: str = "low"  # high | low
    slots: Dict[str, Any] = field(default_factory=dict)
    clarification: str = ""

    @property
    def is_command(self) -> bool:
        return self.kind not in ("submit_bug", "clarify") and self.confidence == "high"


def resolve_config_path_hint(text: str) -> Optional[str]:
    raw = (text or "").strip()
    if not raw:
        return None
    if raw in DEFAULT_EDITABLE_PATHS:
        return raw
    for path, label in PATH_LABELS.items():
        if raw == label or label in raw:
            return path
    for pat, path in _CONFIG_ALIASES:
        if pat.search(raw):
            return path
    return None


def _extract_id(text: str) -> str:
    m = re.search(r"(?:bug|缺陷|需求|story)?\s*(?:id)?\s*[#:]?\s*(\d{6,})", text, re.I)
    if m:
        return m.group(1)
    m = re.search(r"\b(\d{10,})\b", text)
    return m.group(1) if m else ""


_CN_NUM = {
    "一": 1,
    "两": 2,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


def _parse_day_count(token: str) -> Optional[int]:
    t = (token or "").strip()
    if not t:
        return None
    if t.isdigit():
        return max(1, int(t))
    if t in _CN_NUM:
        return _CN_NUM[t]
    if t.startswith("十") and len(t) == 2 and t[1] in _CN_NUM:
        return 10 + _CN_NUM[t[1]]
    if t.endswith("十") and len(t) == 2 and t[0] in _CN_NUM:
        return _CN_NUM[t[0]] * 10
    return None


def parse_time_range(text: str) -> Dict[str, Any]:
    """
    解析相对时间 → {days} 或 {start_date, end_date}（YYYY-MM-DD）。
    无命中返回空 dict。
    """
    raw = text or ""
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    m = re.search(r"(?:近|最近|这)\s*([0-9一二两三四五六七八九十]+)\s*天", raw)
    if m:
        n = _parse_day_count(m.group(1))
        if n:
            return {"days": n}

    if re.search(r"今天|今日", raw):
        return {"days": 1}

    if re.search(r"本周|这周|近一周|最近一周|近一个星期", raw):
        if re.search(r"本周|这周", raw):
            start = today - timedelta(days=today.weekday())
            return {
                "start_date": start.strftime("%Y-%m-%d"),
                "end_date": today.strftime("%Y-%m-%d"),
            }
        return {"days": 7}

    if re.search(r"近一个月|最近一个月", raw):
        return {"days": 30}

    if re.search(r"本月", raw):
        start = today.replace(day=1)
        return {
            "start_date": start.strftime("%Y-%m-%d"),
            "end_date": today.strftime("%Y-%m-%d"),
        }

    return {}


def extract_status_hint(text: str) -> str:
    raw = text or ""
    for key, status in _STATUS_MAP.items():
        if re.search(re.escape(key), raw, re.I):
            return status
    return ""


def _strip_query_noise(text: str) -> str:
    t = text.strip()
    t = re.sub(r"@_user_\d+", " ", t)
    t = re.sub(
        r"^(请|帮我|帮忙|麻烦)?(查一下|查询一下|查下|查询|查|搜一下|搜索)?",
        "",
        t,
    )
    t = _TIME_NOISE_RE.sub(" ", t)
    t = re.sub(r"\s+", " ", t)
    t = _MINE_RE.sub(" ", t)
    t = re.sub(r"(最近|最新|当前迭代)?\s*(resolved|已解决|未关闭|已关闭)?", "", t, flags=re.I)
    t = re.sub(r"(相关)?\s*(的)?\s*(bug|缺陷|需求|story)s?\s*", "", t, flags=re.I)
    t = re.sub(r"^(一下|下|提的|提交的|创建的|我提|提)\s*", "", t)
    t = re.sub(r"\s+", " ", t).strip(" ：:，,。的 ")
    return t


def enrich_slots_from_text(text: str, slots: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """用规则补正/覆盖时间、mine、status、id；并清洗 keyword。"""
    out: Dict[str, Any] = dict(slots or {})
    raw = (text or "").strip()

    time_slots = parse_time_range(raw)
    for k, v in time_slots.items():
        out[k] = v

    if _MINE_RE.search(raw) or re.search(r"我.{0,6}提", raw):
        out["mine"] = True

    status = extract_status_hint(raw)
    if status:
        out["status"] = status

    # LLM 可能把 days 写成字符串
    if "days" in out and out["days"] is not None and out["days"] != "":
        try:
            out["days"] = max(1, int(out["days"]))
        except (TypeError, ValueError):
            out.pop("days", None)

    for key in ("start_date", "end_date"):
        if key in out and out[key] is not None:
            out[key] = str(out[key]).strip()
            if not out[key]:
                out.pop(key, None)

    if out.get("recent") and not any(out.get(k) for k in ("days", "start_date", "end_date")):
        # 兼容旧 recent：无时间窗时表示「最近 resolved」
        if not out.get("status"):
            out["status"] = "resolved"
        out["mine"] = True if out.get("mine") is None else out["mine"]

    bid = _extract_id(raw)
    if bid and not out.get("bug_id") and not out.get("story_id"):
        if re.search(r"需求|story", raw, re.I):
            out["story_id"] = bid
        elif re.search(r"bug|缺陷", raw, re.I) or re.search(r"\d{6,}", raw):
            out["bug_id"] = bid

    # keyword：去掉时间/我的 等噪声；纯噪声则删除
    kw = str(out.get("keyword") or "").strip()
    if not kw:
        kw = _strip_query_noise(raw)
    else:
        kw = _TIME_NOISE_RE.sub(" ", kw)
        kw = _MINE_RE.sub(" ", kw)
        kw = re.sub(r"(resolved|已解决|未关闭|已关闭)", "", kw, flags=re.I)
        kw = re.sub(r"\s+", " ", kw).strip(" ：:，,。的 ")

    noise_only = re.fullmatch(
        r"(最近|最新|当前迭代|我提的|我的|现在|当前|提的|提交的|我提|我创建|我\s*提交|提交)*",
        kw or "",
        re.I,
    )
    if kw and not noise_only and len(kw) >= 1:
        # 避免把时间残留当标题
        if not re.fullmatch(r"\d+", kw):
            out["keyword"] = kw
        else:
            out.pop("keyword", None)
    else:
        out.pop("keyword", None)

    # 已有时间窗或 mine 时，残留的「我/提交」类词不得再当标题关键词
    if any(out.get(k) for k in ("days", "start_date", "end_date")) or out.get("mine"):
        kw2 = str(out.get("keyword") or "").strip()
        if not kw2 or re.fullmatch(
            r"(我|提交|提的|创建的|我的|相关|的|\s)+", kw2
        ):
            out.pop("keyword", None)

    # 有明确时间窗时，不再把「最近」当 resolved 列表捷径，除非用户写了 resolved
    if any(out.get(k) for k in ("days", "start_date", "end_date")):
        out.pop("recent", None)
        if out.get("status") == "resolved" and not re.search(
            r"resolved|已解决|未关闭", raw, re.I
        ):
            # 「查近三天」不等于只要 resolved
            out.pop("status", None)

    return out


def classify_by_rules(text: str) -> Optional[IntentResult]:
    """规则命中则返回；否则 None。"""
    raw = (text or "").strip()
    raw = re.sub(r"@_user_\d+", " ", raw).strip()
    if not raw:
        return None

    if re.fullmatch(r"(帮助|help|菜单|功能)", raw, re.I):
        return IntentResult(kind="help", confidence="high")

    if re.search(r"(查看|看看|显示).{0,6}(我的)?(项目)?配置", raw) or re.fullmatch(
        r"(我的)?(项目)?配置", raw
    ):
        return IntentResult(kind="config_view", confidence="high")

    m = re.search(
        r"(?:把|将)?\s*(?P<field>.+?)\s*(?:改成|改为|改到|设为|设置为|设置成|更新为)\s*(?P<value>.+)$",
        raw,
    )
    if m:
        field = m.group("field").strip()
        value = m.group("value").strip().strip("「」\"'")
        path = resolve_config_path_hint(field)
        if path and value:
            return IntentResult(
                kind="config_edit",
                confidence="high",
                slots={"path": path, "value": value, "label": path_label(path)},
            )
        if value and not path:
            return IntentResult(
                kind="clarify",
                confidence="high",
                clarification=(
                    f"未识别配置项「{field}」。可改：版本、迭代、提单人、前后端负责人、关联需求等。"
                ),
            )

    if re.search(r"(修改|编辑|更新).{0,6}(项目)?配置", raw) or re.fullmatch(
        r"修改配置", raw
    ):
        return IntentResult(
            kind="config_edit",
            confidence="high",
            slots={},
            clarification="请说明要改的配置，例如：把迭代改成 1.2.0",
        )

    if re.search(r"(?<![未不])(关闭|关掉|关单).{0,20}(bug|缺陷)?", raw, re.I) or re.match(
        r"^(请|帮我|帮忙)?\s*(关闭|关掉|关单)\b", raw, re.I
    ):
        slots: Dict[str, Any] = enrich_slots_from_text(raw, {})
        bug_id = _extract_id(raw)
        if bug_id:
            slots["bug_id"] = bug_id
            slots.pop("keyword", None)
        else:
            kw = raw
            kw = re.sub(r"(请|帮我|帮忙|麻烦)", "", kw)
            kw = re.sub(r"(关闭|关掉|关单)", "", kw)
            kw = re.sub(r"(这个|那个|一下|下)", "", kw)
            kw = _TIME_NOISE_RE.sub(" ", kw)
            kw = _MINE_RE.sub(" ", kw)
            kw = re.sub(r"(相关)?\s*(的)?\s*(bug|缺陷|单)s?", "", kw, flags=re.I)
            kw = kw.strip(" ：:，,。的")
            if kw and kw not in ("最近", "最新", "resolved", "已解决"):
                slots["keyword"] = kw
            if re.search(r"最近|resolved|已解决", raw, re.I) and not slots.get("days"):
                slots["recent"] = True
        return IntentResult(kind="close_bug", confidence="high", slots=slots)

    if re.search(r"(查|查询|搜).{0,10}(需求|story)", raw, re.I):
        slots = enrich_slots_from_text(raw, {})
        if re.search(r"最近|当前迭代|最新", raw) and not any(
            slots.get(k) for k in ("days", "start_date", "keyword")
        ):
            slots["recent"] = True
        sid = _extract_id(raw)
        if sid:
            slots["story_id"] = sid
            slots.pop("keyword", None)
        return IntentResult(kind="query_story", confidence="high", slots=slots)

    if re.search(r"(查|查询|搜).{0,16}(bug|缺陷)", raw, re.I) or re.search(
        r"(最近|最新).{0,6}(resolved|已解决|未关闭).{0,6}(bug|缺陷)",
        raw,
        re.I,
    ) or re.search(
        r"(我提的|我的).{0,10}(未关闭|resolved|已解决).{0,8}(bug|缺陷)",
        raw,
        re.I,
    ):
        slots = enrich_slots_from_text(raw, {})
        bid = _extract_id(raw)
        if bid and not re.search(r"需求|story", raw, re.I):
            slots["bug_id"] = bid
            slots.pop("keyword", None)
            slots.pop("recent", None)
        elif (
            not any(slots.get(k) for k in ("days", "start_date", "end_date", "keyword", "bug_id"))
            and re.search(r"最近|最新|resolved|已解决|未关闭", raw, re.I)
        ):
            slots["recent"] = True
            slots["mine"] = True
            slots["status"] = slots.get("status") or "resolved"
        return IntentResult(kind="query_bug", confidence="high", slots=slots)

    return None


def _sanitize_llm_result(raw_text: str, llm_res: Dict[str, Any]) -> IntentResult:
    kind = str(llm_res.get("intent") or "clarify").strip()
    conf = str(llm_res.get("confidence") or "low").strip().lower()
    if kind not in ALLOWED_INTENTS:
        return IntentResult(
            kind="clarify",
            confidence="high",
            clarification="未能识别该操作。可查询 Bug/需求、关闭 Bug、查看/修改配置，或描述缺陷提 Bug。",
        )
    if conf not in ("high", "low"):
        conf = "low"
    slots = llm_res.get("slots") if isinstance(llm_res.get("slots"), dict) else {}
    slots = enrich_slots_from_text(raw_text, dict(slots))

    if kind == "config_edit":
        path = str(slots.get("path") or "").strip()
        if path and path not in DEFAULT_EDITABLE_PATHS:
            hint = resolve_config_path_hint(path) or resolve_config_path_hint(
                str(slots.get("field") or "")
            )
            if hint:
                slots["path"] = hint
            else:
                return IntentResult(
                    kind="clarify",
                    confidence="high",
                    clarification="配置项不在可修改白名单内，请说明如：迭代、版本、提单人。",
                )
        if path and path in DEFAULT_EDITABLE_PATHS:
            slots["label"] = path_label(path)

    if kind == "clarify":
        return IntentResult(
            kind="clarify",
            confidence="high",
            clarification=str(llm_res.get("clarification") or "请补充更明确的操作说明。"),
            slots=slots,
        )

    # 低置信非 submit → 追问，不误执行
    if kind != "submit_bug" and conf != "high":
        return IntentResult(
            kind="clarify",
            confidence="high",
            clarification=str(llm_res.get("clarification") or "请补充更明确的操作说明。"),
            slots=slots,
        )

    return IntentResult(kind=kind, confidence=conf if kind == "submit_bug" else "high", slots=slots)


def classify_intent(
    text: str,
    *,
    llm_cfg: Optional[Dict[str, Any]] = None,
    use_llm: bool = True,
    agent_first: bool = True,
) -> IntentResult:
    """
    分类用户意图。
    - agent_first=True（私聊默认）：LLM 优先，规则补正槽位；无 LLM 时回退规则，再不命中则 clarify
    - agent_first=False：旧行为，规则优先
    """
    raw = (text or "").strip()
    ruled = classify_by_rules(raw)

    # 配置改写等高精度规则在 agent 模式下仍可直接采用
    if agent_first and ruled is not None and ruled.kind in (
        "help",
        "config_view",
        "config_edit",
        "clarify",
    ):
        if ruled.kind != "clarify" or ruled.clarification:
            ruled.slots = enrich_slots_from_text(raw, ruled.slots)
            return ruled

    has_llm = bool(
        use_llm and llm_cfg and str(llm_cfg.get("api_key") or "").strip()
    )

    if agent_first and has_llm:
        try:
            from llm_parse import classify_user_intent

            llm_res = classify_user_intent(llm_cfg=llm_cfg, user_text=raw)
            result = _sanitize_llm_result(raw, llm_res)
            # 规则若明确命中同族命令，用规则槽位补强（尤其时间）
            if ruled is not None and ruled.kind == result.kind:
                merged = dict(result.slots)
                for k, v in (ruled.slots or {}).items():
                    if v in (None, "", False) and k in merged:
                        continue
                    if k not in merged or merged.get(k) in (None, "", False):
                        merged[k] = v
                result.slots = enrich_slots_from_text(raw, merged)
            return result
        except Exception:
            if ruled is not None:
                ruled.slots = enrich_slots_from_text(raw, ruled.slots)
                return ruled
            return IntentResult(
                kind="clarify",
                confidence="high",
                clarification="Agent 暂时不可用，请稍后重试，或改用更明确的指令（如「查最近的 resolved bug」）。",
            )

    if ruled is not None:
        ruled.slots = enrich_slots_from_text(raw, ruled.slots)
        return ruled

    if not agent_first and has_llm:
        try:
            from llm_parse import classify_user_intent

            llm_res = classify_user_intent(llm_cfg=llm_cfg, user_text=raw)
            return _sanitize_llm_result(raw, llm_res)
        except Exception:
            pass

    if agent_first:
        if not has_llm:
            return IntentResult(
                kind="clarify",
                confidence="high",
                clarification=(
                    "尚未配置 LLM，无法理解自然语言指令。"
                    "请先在「账号配置」填写 API Key，或发送更明确的短指令。"
                ),
            )
        return IntentResult(
            kind="clarify",
            confidence="high",
            clarification="未能理解该指令。可试：查我近三天提的 bug / 关闭 bug 123 / 把迭代改成 1.2.0，或直接描述缺陷。",
        )

    return IntentResult(kind="submit_bug", confidence="low")
