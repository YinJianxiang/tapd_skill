"""OpenAI 兼容 Chat Completions：把自然语言解析为 TAPD bug payload。"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional


SYSTEM_PROMPT = """你是 TAPD 缺陷助手。根据用户私聊内容，输出严格 JSON（不要 markdown 代码块），字段：
{
  "title": "缺陷标题正文（不要带【】前缀）",
  "summary": "一句话概述",
  "description": "必须包含：复现步骤、实际结果、期望结果、影响范围（中文）",
  "module": "前端 或 后端；不确定则空字符串",
  "module_confidence": "high 或 low",
  "priority": "urgent|high|medium|low",
  "severity": "fatal|serious|normal|prompt",
  "label": "可选；仅高置信简单展示类问题时写 简单问题，否则空字符串",
  "current_owner": "可选；用户明确指定负责人时填写，否则空字符串",
  "need_clarification": "若缺关键信息需要追问，写一句中文问题，否则空字符串",
  "reply_hint": "给用户的一句话说明（中文）"
}

规则：
- 全部使用中文业务表达
- module 仅在高置信时填写；低置信留空并把 module_confidence 设为 low
- 不要编造用户未提供的具体账号、request_id；有则写入 description
- 若用户在修改已有草稿，在原草稿基础上合并更新
"""


INTENT_SYSTEM_PROMPT = """你是飞书 TAPD 机器人的受约束 Agent。根据用户一句话，选择唯一能力并抽取参数，输出严格 JSON（不要 markdown）：
{
  "intent": "query_bug|query_story|close_bug|config_view|config_edit|submit_bug|help|clarify",
  "confidence": "high|low",
  "slots": {
    "bug_id": "",
    "story_id": "",
    "keyword": "",
    "mine": false,
    "days": null,
    "start_date": "",
    "end_date": "",
    "status": "",
    "recent": false,
    "path": "",
    "value": "",
    "field": "",
    "missing": []
  },
  "clarification": "若 intent=clarify 或缺参时给用户的一句追问，否则空字符串"
}

能力说明：
- query_bug / query_story：只读查询，直接执行
- close_bug / config_edit / submit_bug：写操作，系统会再确认；你只负责理解参数
- config_view / help / clarify：信息类

规则：
1. 用户在描述缺陷现象、复现步骤、报错 → submit_bug（即使含「帮我看看」）
2. 「查询我近三天提的 bug」→ query_bug，slots={mine:true, days:3}，keyword 必须为空（时间不是标题关键词）
3. 「查近三天登录相关 bug」→ query_bug，slots={days:3, keyword:"登录"}
4. 「查最近的 resolved bug / 我提的未关闭 bug」→ query_bug，slots={recent:true, mine:true, status:"resolved"}
5. days 用整数；也可用 start_date/end_date（YYYY-MM-DD）。支持：今天、近N天、本周、近一周、近一个月、本月
6. mine=true 表示当前用户创建/提交的；我提的/我创建的 → mine=true
7. status 仅在用户明确提到时填写：resolved|closed|new|in_progress 等
8. config_edit.path 只能是：defaults.version, defaults.iteration, defaults.name, defaults.reporter,
   module_owner_map.前端, module_owner_map.后端, link_story.enabled, link_story.story_id,
   link_story.story_name, link_story.match_scope；不确定则 clarify
9. 禁止发明其它 intent、函数名或配置 path
10. 参数不足 → intent=clarify 或在 slots.missing 列出缺项，并写 clarification
11. 模糊指令（如仅「查一下」）→ clarify；不要把普通缺陷描述判成 close_bug / config_edit
"""


def chat_completions(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: List[Dict[str, str]],
    temperature: float = 0.2,
) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM HTTP {e.code}: {err}") from e

    try:
        return str(data["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"LLM 响应格式异常: {data}") from e


def _extract_json(text: str) -> Dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise ValueError(f"无法从 LLM 输出解析 JSON: {text[:500]}")
    obj = json.loads(m.group(0))
    if not isinstance(obj, dict):
        raise ValueError("LLM JSON 根节点必须是对象")
    return obj


def parse_bug_from_nl(
    *,
    llm_cfg: Dict[str, Any],
    user_text: str,
    prior_draft: Optional[Dict[str, Any]] = None,
    history: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    messages: List[Dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    if prior_draft:
        messages.append(
            {
                "role": "system",
                "content": "当前草稿 JSON：\n" + json.dumps(prior_draft, ensure_ascii=False),
            }
        )
    if history:
        # 仅附最近几轮，避免上下文过长
        for item in history[-6:]:
            messages.append({"role": item["role"], "content": item["content"]})
    messages.append({"role": "user", "content": user_text})

    raw = chat_completions(
        base_url=str(llm_cfg.get("base_url") or ""),
        api_key=str(llm_cfg.get("api_key") or ""),
        model=str(llm_cfg.get("model") or ""),
        messages=messages,
    )
    parsed = _extract_json(raw)

    # 规范化
    module = str(parsed.get("module") or "").strip()
    if module not in ("", "前端", "后端"):
        module = ""
        parsed["module_confidence"] = "low"
    parsed["module"] = module
    conf = str(parsed.get("module_confidence") or "").strip().lower()
    if conf not in ("high", "low"):
        conf = "high" if module else "low"
    parsed["module_confidence"] = conf
    for key in ("title", "summary", "description", "priority", "severity", "label", "current_owner"):
        if key in parsed and parsed[key] is not None:
            parsed[key] = str(parsed[key]).strip()
    return parsed


def classify_user_intent(*, llm_cfg: Dict[str, Any], user_text: str) -> Dict[str, Any]:
    """LLM 意图分类；返回原始结构化结果，由 intent.classify_intent 再校验。"""
    messages: List[Dict[str, str]] = [
        {"role": "system", "content": INTENT_SYSTEM_PROMPT},
        {"role": "user", "content": user_text},
    ]
    raw = chat_completions(
        base_url=str(llm_cfg.get("base_url") or ""),
        api_key=str(llm_cfg.get("api_key") or ""),
        model=str(llm_cfg.get("model") or ""),
        messages=messages,
        temperature=0.0,
    )
    return _extract_json(raw)


def format_draft_reply(draft: Dict[str, Any], *, image_count: int = 0) -> str:
    module = draft.get("module") or "待确认（请回复「前端」或「后端」）"
    label = draft.get("label") or "无"
    lines = [
        "草稿内容：",
        f"- 标题：{draft.get('title') or '（待补）'}",
        f"- 模块 / 软件平台：{module}",
        f"- 优先级：{draft.get('priority') or '（待补）'}",
        f"- 严重程度：{draft.get('severity') or '（待补）'}",
        f"- 标签：{label}",
        f"- 处理人：{draft.get('current_owner') or '（按模块自动映射）'}",
        f"- 附件图片：{image_count} 张",
        "- 描述：",
        str(draft.get("description") or "（待补）"),
        "",
    ]
    if draft.get("module_confidence") == "low" or not draft.get("module"):
        lines.append("模块尚未确认，请回复「前端」或「后端」后再提交。")
    else:
        lines.append("确认无误请回复「确认提交」；取消请回复「取消」；也可继续补充/修改。")
    hint = str(draft.get("need_clarification") or draft.get("reply_hint") or "").strip()
    if hint:
        lines.append(f"说明：{hint}")
    return "\n".join(lines)
