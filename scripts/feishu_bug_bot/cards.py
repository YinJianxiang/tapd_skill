"""飞书交互卡片 JSON（经典 schema）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def _btn(text: str, action: str, *, primary: bool = False, path: str = "") -> Dict[str, Any]:
    value: Dict[str, Any] = {"action": action}
    if path:
        value["path"] = path
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": text},
        "type": "primary" if primary else "default",
        "value": value,
    }


def _md(content: str) -> Dict[str, Any]:
    return {"tag": "div", "text": {"tag": "lark_md", "content": content}}


def _header(title: str) -> Dict[str, Any]:
    return {
        "title": {"tag": "plain_text", "content": title},
        "template": "blue",
    }


def _card(title: str, elements: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "config": {"wide_screen_mode": True},
        "header": _header(title),
        "elements": elements,
    }


def help_card() -> Dict[str, Any]:
    body = (
        "**私聊（Agent 理解）**\n"
        "直接发自然语言，由 Agent 提取参数并调用能力；参数不足会文字追问。\n"
        "- `查我近三天提的 bug` / `查近三天登录相关 bug`\n"
        "- `查最近的 resolved bug` / `查当前迭代需求`\n"
        "- `关闭 bug 123456` / 描述缺陷提 Bug\n"
        "- `查看我的配置` / `把迭代改成 1.2.0`\n\n"
        "**菜单**（保留原卡片流程）\n"
        "- 提 Bug / 查 Bug / 查需求 / 关 Bug / 查看配置 / 修改配置\n\n"
        "**注意**\n"
        "- 关单、提单、改配置会先出确认卡，确认后才写入\n"
        "- 确认/取消后原卡按钮会置灰失效\n"
        "- 取消：回复「取消」"
    )
    return _card(
        "TAPD 提 Bug 助手",
        [
            _md(body),
            {
                "tag": "action",
                "actions": [
                    _btn("提 Bug", "submit_bug", primary=True),
                    _btn("查 Bug", "query_bug"),
                    _btn("查需求", "query_story"),
                ],
            },
            {
                "tag": "action",
                "actions": [
                    _btn("关 Bug", "close_bug"),
                    _btn("查看配置", "config_view"),
                    _btn("修改配置", "config_edit"),
                ],
            },
        ],
    )


def _draft_body_md(draft: Dict[str, Any], *, image_count: int = 0) -> str:
    module = draft.get("module") or "待确认"
    label = draft.get("label") or "无"
    desc = str(draft.get("description") or "（待补）")
    if len(desc) > 800:
        desc = desc[:800] + "…"
    body = (
        f"**标题**：{draft.get('title') or '（待补）'}\n"
        f"**模块**：{module}\n"
        f"**优先级**：{draft.get('priority') or '—'}\n"
        f"**严重程度**：{draft.get('severity') or '—'}\n"
        f"**标签**：{label}\n"
        f"**处理人**：{draft.get('current_owner') or '（按模块自动映射）'}\n"
        f"**附件图片**：{image_count} 张\n\n"
        f"**描述**\n{desc}"
    )
    hint = str(draft.get("need_clarification") or draft.get("reply_hint") or "").strip()
    if hint:
        body += f"\n\n说明：{hint}"
    if not draft.get("module") or draft.get("module_confidence") == "low":
        body += "\n\n模块尚未确认，请先选择「前端」或「后端」。"
    return body


def draft_card(
    draft: Dict[str, Any],
    *,
    image_count: int = 0,
    operation_id: str = "",
) -> Dict[str, Any]:
    """缺陷草稿确认卡（Schema 2.0）。"""
    body = _draft_body_md(draft, image_count=image_count)
    elements: List[Dict[str, Any]] = [_form_md(body)]
    need_module = not draft.get("module") or draft.get("module_confidence") == "low"
    if need_module:
        elements.append(
            _v2_callback_btn("btn_module_frontend", "前端", "module_frontend")
        )
        elements.append(
            _v2_callback_btn("btn_module_backend", "后端", "module_backend")
        )
    else:
        elements.append(
            _v2_callback_btn(
                "btn_confirm_submit",
                "确认提交",
                "confirm_submit",
                primary=True,
                operation_id=operation_id,
            )
        )
    elements.append(
        _v2_callback_btn(
            "btn_cancel_draft",
            "取消草稿",
            "cancel_draft",
            operation_id=operation_id,
        )
    )
    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": "缺陷草稿"},
            "template": "blue",
        },
        "body": {"elements": elements},
    }


def _format_value(value: Any) -> str:
    if value is None or value == "":
        return "（空）"
    if isinstance(value, bool):
        return "开启" if value else "关闭"
    return str(value)


def config_view_card(
    rows: List[Tuple[str, str, Any]],
    *,
    title: str = "我的项目配置",
) -> Dict[str, Any]:
    lines = [f"**{label}**（`{path}`）：{_format_value(val)}" for path, label, val in rows]
    body = (
        "以下为**你的**有效配置（个人覆盖 ∪ 全局默认）。\n\n"
        + ("\n".join(lines) if lines else "（无可用字段）")
    )
    # schema 2.0：禁止 tag=action，改用 behaviors 回传按钮
    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": "blue",
        },
        "body": {
            "elements": [
                _form_md(body),
                _v2_callback_btn("btn_config_edit", "修改配置", "config_edit", primary=True),
                _v2_callback_btn("btn_config_refresh", "刷新", "config_view"),
            ]
        },
    }


def _select_static(
    name: str,
    options: List[Tuple[str, str]],
    *,
    required: bool = False,
    initial: str = "",
) -> Dict[str, Any]:
    obj: Dict[str, Any] = {
        "tag": "select_static",
        "name": name,
        "required": required,
        "placeholder": {"tag": "plain_text", "content": "请选择"},
        "options": [
            {"text": {"tag": "plain_text", "content": label}, "value": value}
            for label, value in options
        ],
    }
    if initial:
        obj["initial_option"] = initial
    return obj


def _input_field(
    name: str,
    placeholder: str,
    *,
    required: bool = False,
    multiline: bool = False,
    password: bool = False,
    default_value: str = "",
) -> Dict[str, Any]:
    obj: Dict[str, Any] = {
        "tag": "input",
        "name": name,
        "required": required,
        "placeholder": {"tag": "plain_text", "content": placeholder},
        "width": "fill",
    }
    if password:
        obj["input_type"] = "password"
    elif multiline:
        obj["input_type"] = "multiline_text"
        obj["rows"] = 4
    if default_value and not password:
        obj["default_value"] = default_value
    return obj


def _form_md(content: str) -> Dict[str, Any]:
    return {"tag": "markdown", "content": content}


def _form_submit_btn(name: str, text: str, action: str) -> Dict[str, Any]:
    return {
        "tag": "button",
        "name": name,
        "text": {"tag": "plain_text", "content": text},
        "type": "primary",
        "form_action_type": "submit",
        "behaviors": [{"type": "callback", "value": {"action": action}}],
    }


def _v2_callback_btn(
    name: str,
    text: str,
    action: str,
    *,
    primary: bool = False,
    operation_id: str = "",
    disabled: bool = False,
) -> Dict[str, Any]:
    """schema 2.0 可用的回传按钮（不可再用 tag=action 容器）。"""
    value: Dict[str, Any] = {"action": action}
    if operation_id:
        value["operation_id"] = operation_id
    btn: Dict[str, Any] = {
        "tag": "button",
        "name": name,
        "text": {"tag": "plain_text", "content": text},
        "type": "primary" if primary else "default",
        "behaviors": [{"type": "callback", "value": value}],
    }
    if disabled:
        btn["disabled"] = True
    return btn


def disabled_confirm_card(
    *,
    title: str,
    summary_md: str,
    status_label: str,
    template: str = "grey",
) -> Dict[str, Any]:
    """确认后原地替换：按钮全部 disabled。"""
    body = f"{summary_md}\n\n**状态：{status_label}**"
    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": template,
        },
        "body": {
            "elements": [
                _form_md(body),
                _v2_callback_btn(
                    "btn_done_disabled", status_label, "noop", disabled=True
                ),
            ]
        },
    }


# 飞书表单 name 建议用字母数字下划线；path 可能含中文/点号，需映射
_CONFIG_FORM_NAME_BY_PATH: Dict[str, str] = {
    "defaults.version": "cfg_defaults_version",
    "defaults.iteration": "cfg_defaults_iteration",
    "defaults.name": "cfg_defaults_name",
    "defaults.reporter": "cfg_defaults_reporter",
    "module_owner_map.前端": "cfg_module_owner_frontend",
    "module_owner_map.后端": "cfg_module_owner_backend",
    "link_story.enabled": "cfg_link_story_enabled",
    "link_story.story_id": "cfg_link_story_story_id",
    "link_story.story_name": "cfg_link_story_story_name",
    "link_story.match_scope": "cfg_link_story_match_scope",
}
_CONFIG_FORM_PATH_BY_NAME: Dict[str, str] = {v: k for k, v in _CONFIG_FORM_NAME_BY_PATH.items()}


def config_form_field_name(path: str) -> str:
    return _CONFIG_FORM_NAME_BY_PATH.get(path) or (
        "cfg_" + "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in path)
    )


def resolve_config_form_path(field_name: str) -> str:
    """把表单回传的 name 还原为 project_config path。"""
    if field_name in _CONFIG_FORM_PATH_BY_NAME:
        return _CONFIG_FORM_PATH_BY_NAME[field_name]
    return field_name


def config_form_card(rows: List[Tuple[str, str, Any]]) -> Dict[str, Any]:
    """项目配置编辑表单。提交才写入；取消回到查看卡。"""
    elements: List[Dict[str, Any]] = [
        _form_md("修改后点「提交保存」才会生效；点「取消」不保存。"),
    ]
    for path, label, val in rows:
        fname = config_form_field_name(path)
        elements.append(_form_md(f"**{label}**"))
        if path == "link_story.enabled":
            initial = "true" if bool(val) else "false"
            elements.append(
                _select_static(
                    fname,
                    [("开启", "true"), ("关闭", "false")],
                    initial=initial,
                )
            )
        elif path == "link_story.match_scope":
            cur = str(val or "iteration").strip().lower()
            if cur not in ("iteration", "project"):
                cur = "iteration"
            elements.append(
                _select_static(
                    fname,
                    [("当前迭代 iteration", "iteration"), ("全项目 project", "project")],
                    initial=cur,
                )
            )
        else:
            elements.append(
                _input_field(
                    fname,
                    f"当前：{_format_value(val)}",
                    default_value="" if val is None else str(val),
                )
            )
    elements.append(
        _form_submit_btn("btn_submit_project_config", "提交保存", "config_form_submit")
    )
    # 取消放在 form 外：form 内非 submit/reset 按钮在部分客户端会校验失败
    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": "修改项目配置"},
            "template": "blue",
        },
        "body": {
            "elements": [
                {
                    "tag": "form",
                    "name": "project_config_form",
                    "elements": elements,
                },
                _v2_callback_btn("btn_cancel_project_config", "取消", "config_view"),
            ]
        },
    }


def account_form_card(
    llm_rows: List[Tuple[str, str, Any]],
    tapd_rows: List[Tuple[str, str, Any]],
) -> Dict[str, Any]:
    """账号配置编辑表单。密钥 password，留空表示不改。"""
    elements: List[Dict[str, Any]] = [
        _form_md(
            "修改后点「提交保存」才会生效；点「取消」不保存。\n"
            "**API Key / Access Token** 为密码框，**留空表示不修改**。"
        ),
        _form_md("**LLM**"),
    ]
    for path, label, val in llm_rows:
        elements.append(_form_md(f"**{label}**"))
        if path == "llm.api_key":
            elements.append(
                _input_field(
                    path,
                    "留空表示不修改（已配置则保持原值）",
                    password=True,
                )
            )
        else:
            elements.append(
                _input_field(
                    path,
                    f"当前：{_format_value(val)}",
                    default_value="" if val is None else str(val),
                )
            )
    elements.append(_form_md("**TAPD**"))
    for path, label, val in tapd_rows:
        elements.append(_form_md(f"**{label}**"))
        if path == "tapd_env.access_token":
            elements.append(
                _input_field(
                    path,
                    "留空表示不修改（已配置则保持原值）",
                    password=True,
                )
            )
        else:
            elements.append(
                _input_field(
                    path,
                    f"当前：{_format_value(val)}",
                    default_value="" if val is None else str(val),
                )
            )
    elements.append(
        _form_submit_btn("btn_submit_account_config", "提交保存", "account_form_submit")
    )
    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": "修改账号配置"},
            "template": "blue",
        },
        "body": {
            "elements": [
                {
                    "tag": "form",
                    "name": "account_config_form",
                    "elements": elements,
                },
                _v2_callback_btn("btn_cancel_account_config", "取消", "account_view"),
            ]
        },
    }


def account_view_card(
    llm_rows: List[Tuple[str, str, str]],
    tapd_rows: List[Tuple[str, str, str]],
) -> Dict[str, Any]:
    """合并 LLM + TAPD 查看卡。rows 已掩码。"""
    lines = ["**LLM**"]
    lines.extend(f"- **{label}**：{disp}" for _p, label, disp in llm_rows)
    lines.append("")
    lines.append("**TAPD**")
    lines.extend(f"- **{label}**：{disp}" for _p, label, disp in tapd_rows)
    body = (
        "你的账号有效配置（个人覆盖 ∪ 全局兜底）。"
        "**API Key / Access Token 仅显示掩码。**\n\n" + "\n".join(lines)
    )
    return _card(
        "我的账号配置",
        [
            _md(body),
            {
                "tag": "action",
                "actions": [
                    _btn("修改配置", "account_edit", primary=True),
                    _btn("刷新", "account_view"),
                ],
            },
        ],
    )


def submit_bug_form_card() -> Dict[str, Any]:
    """飞书 JSON 2.0 表单：填完提交走 TAPD 生产提单。"""
    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": "提 Bug"},
            "template": "blue",
        },
        "body": {
            "elements": [
                {
                    "tag": "markdown",
                    "content": "填写下方表单后点「提交到 TAPD」。版本/迭代等沿用项目配置（可用菜单「修改配置」调整）。",
                },
                {
                    "tag": "form",
                    "name": "submit_bug_form",
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": "**标题***",
                        },
                        _input_field("title", "缺陷标题（不要带【】前缀）", required=True),
                        {
                            "tag": "markdown",
                            "content": "**模块***",
                        },
                        _select_static(
                            "module",
                            [("前端", "前端"), ("后端", "后端")],
                            required=True,
                        ),
                        {
                            "tag": "markdown",
                            "content": "**描述***（复现步骤 / 实际结果 / 期望结果）",
                        },
                        _input_field(
                            "description",
                            "请描述问题",
                            required=True,
                            multiline=True,
                        ),
                        {
                            "tag": "markdown",
                            "content": "**优先级**",
                        },
                        _select_static(
                            "priority",
                            [
                                ("紧急 urgent", "urgent"),
                                ("高 high", "high"),
                                ("中 medium", "medium"),
                                ("低 low", "low"),
                            ],
                            initial="medium",
                        ),
                        {
                            "tag": "markdown",
                            "content": "**严重程度**",
                        },
                        _select_static(
                            "severity",
                            [
                                ("致命 fatal", "fatal"),
                                ("严重 serious", "serious"),
                                ("一般 normal", "normal"),
                                ("提示 prompt", "prompt"),
                            ],
                            initial="normal",
                        ),
                        {
                            "tag": "markdown",
                            "content": "**处理人**（可选，空则按模块映射）",
                        },
                        _input_field("current_owner", "负责人昵称"),
                        {
                            "tag": "markdown",
                            "content": "**标签**（可选）",
                        },
                        _input_field("label", "如：简单问题"),
                        {
                            "tag": "button",
                            "name": "btn_submit_bug",
                            "text": {"tag": "plain_text", "content": "提交到 TAPD"},
                            "type": "primary",
                            "form_action_type": "submit",
                            "behaviors": [
                                {
                                    "type": "callback",
                                    "value": {"action": "submit_bug_form"},
                                }
                            ],
                        },
                    ],
                },
            ]
        },
    }


def _multi_select_static(
    name: str,
    options: List[Tuple[str, str]],
    *,
    required: bool = False,
    placeholder: str = "请选择",
) -> Dict[str, Any]:
    return {
        "tag": "multi_select_static",
        "name": name,
        "required": required,
        "placeholder": {"tag": "plain_text", "content": placeholder},
        "options": [
            {"text": {"tag": "plain_text", "content": label}, "value": value}
            for label, value in options
        ],
    }


def _truncate_option_label(text: str, *, max_len: int = 80) -> str:
    text = (text or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def close_bug_select_card(
    rows: List[Dict[str, str]],
    *,
    scope_title: str,
) -> Dict[str, Any]:
    """resolved Bug 多选关闭卡（Schema 2.0）。"""
    options: List[Tuple[str, str]] = []
    for row in rows:
        bug_id = str(row.get("id") or "").strip()
        if not bug_id:
            continue
        title = str(row.get("title") or "（无标题）").strip()
        label = _truncate_option_label(f"[{bug_id}] {title}")
        options.append((label, bug_id))

    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": "关闭 Bug"},
            "template": "blue",
        },
        "body": {
            "elements": [
                _form_md(
                    f"以下为你最近 resolved（已解决未关闭）Bug（{scope_title}，最多 10 条）。"
                    "勾选后点「关闭选中」；关闭时处理人会设为创建人（reporter）。"
                ),
                {
                    "tag": "form",
                    "name": "close_bug_form",
                    "elements": [
                        _multi_select_static(
                            "bug_ids",
                            options,
                            required=True,
                            placeholder="选择要关闭的缺陷",
                        ),
                        _form_submit_btn(
                            "btn_close_bugs",
                            "关闭选中",
                            "close_bug_form",
                        ),
                    ],
                },
                _v2_callback_btn("btn_close_bug_cancel", "取消", "close_bug_cancel"),
            ]
        },
    }


def close_bug_preview_card(
    rows: List[Dict[str, str]],
    *,
    operation_id: str = "",
) -> Dict[str, Any]:
    """关闭预览确认卡（Schema 2.0）。"""
    lines: List[str] = [
        "将把以下缺陷状态更新为 **closed**，处理人设为创建人（reporter）。确认后才会写入 TAPD。",
        "",
    ]
    for idx, row in enumerate(rows, 1):
        bid = str(row.get("id") or "").strip()
        title = str(row.get("title") or "（无标题）").strip()
        status = str(row.get("status") or "resolved").strip()
        url = str(row.get("url") or "").strip()
        reporter = str(row.get("reporter") or "").strip()
        owner = str(row.get("current_owner") or "").strip()
        owner_on_close = reporter or owner or "（未知）"
        line = (
            f"{idx}. [{bid}] {title}\n"
            f"   当前：{status} → closed\n"
            f"   处理人：{owner or '（空）'} → {owner_on_close}"
        )
        if url:
            line += f"\n   {url}"
        lines.append(line)
    summary = "\n".join(lines)
    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": "确认关闭 Bug"},
            "template": "orange",
        },
        "body": {
            "elements": [
                _form_md(summary),
                _v2_callback_btn(
                    "btn_confirm_close",
                    "确认关闭",
                    "confirm_close",
                    primary=True,
                    operation_id=operation_id,
                ),
                _v2_callback_btn(
                    "btn_close_bug_cancel",
                    "取消",
                    "close_bug_cancel",
                    operation_id=operation_id,
                ),
            ]
        },
    }


def config_change_preview_card(
    changes: List[Tuple[str, str, Any, Any]],
    *,
    operation_id: str = "",
) -> Dict[str, Any]:
    """
    配置变更预览。
    changes: [(path, label, old_value, new_value), ...]
    """
    lines = ["将保存以下配置变更（写入你的个人覆盖，确认后生效）：", ""]
    for path, label, old_v, new_v in changes:
        lines.append(
            f"- **{label}**（`{path}`）\n"
            f"  {_format_value(old_v)} → **{_format_value(new_v)}**"
        )
    summary = "\n".join(lines)
    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": "确认保存配置"},
            "template": "orange",
        },
        "body": {
            "elements": [
                _form_md(summary),
                _v2_callback_btn(
                    "btn_confirm_config",
                    "确认保存",
                    "confirm_config",
                    primary=True,
                    operation_id=operation_id,
                ),
                _v2_callback_btn(
                    "btn_cancel_config",
                    "取消",
                    "config_view",
                    operation_id=operation_id,
                ),
            ]
        },
    }


def query_bug_choice_card() -> Dict[str, Any]:
    return _card(
        "查 Bug",
        [
            _md("选择查询方式："),
            {
                "tag": "action",
                "actions": [
                    _btn("按关键词 / ID", "query_bug_keyword", primary=True),
                    _btn("最近解决", "query_bug_recent"),
                ],
            },
        ],
    )


def query_story_choice_card() -> Dict[str, Any]:
    return _card(
        "查需求",
        [
            _md("选择查询方式："),
            {
                "tag": "action",
                "actions": [
                    _btn("按关键词 / ID", "query_story_keyword", primary=True),
                    _btn("当前迭代最近", "query_story_recent"),
                ],
            },
        ],
    )


def form_field_str(form_value: Dict[str, Any], key: str) -> str:
    v = form_value.get(key)
    if v is None:
        return ""
    if isinstance(v, dict):
        return str(v.get("value") or v.get("text") or "").strip()
    return str(v).strip()


def bug_ids_from_form_value(form_value: Dict[str, Any]) -> List[str]:
    """解析飞书 multi_select 回传的 bug id 列表（去重保序）。"""
    raw = form_value.get("bug_ids")
    if raw is None:
        return []

    items: List[str] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                val = str(item.get("value") or item.get("text") or "").strip()
            else:
                val = str(item).strip()
            if val:
                items.append(val)
    elif isinstance(raw, dict):
        val = str(raw.get("value") or raw.get("text") or "").strip()
        if val:
            items.append(val)
    else:
        text = str(raw).strip()
        if text:
            items.extend(part.strip() for part in text.split(",") if part.strip())

    seen: set[str] = set()
    out: List[str] = []
    for bug_id in items:
        if bug_id not in seen:
            seen.add(bug_id)
            out.append(bug_id)
    return out


def draft_from_form_value(form_value: Dict[str, Any]) -> Dict[str, Any]:
    """把飞书 form_value 转成 submit_bridge draft。"""
    def _s(key: str) -> str:
        return form_field_str(form_value, key)

    draft: Dict[str, Any] = {
        "title": _s("title"),
        "module": _s("module"),
        "description": _s("description"),
        "priority": _s("priority") or "medium",
        "severity": _s("severity") or "normal",
        "module_confidence": "high",
    }
    owner = _s("current_owner")
    if owner:
        draft["current_owner"] = owner
    label = _s("label")
    if label:
        draft["label"] = label
    return draft

