"""
飞书私聊提 Bug 本地回调服务。

启动：
  python scripts/feishu_bug_bot/server.py

配置：
  .tapd/feishu_bot.json（从 feishu_bot.template.json 复制）
"""

from __future__ import annotations

import json
import re
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

# region agent log
_AGENT_LOG_PATH = _THIS_DIR.parent.parent / "debug-00956b.log"
_DEBUG_83_LOG = _THIS_DIR.parent.parent / "debug-83fe6d.log"


def _agent_debug_log(
    hypothesis_id: str,
    location: str,
    message: str,
    data: Dict[str, Any],
    *,
    run_id: str = "group-pre",
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


def _dbg83(hypothesis_id: str, location: str, message: str, data: Dict[str, Any], *, run_id: str = "pre-fix") -> None:
    try:
        from submit_bridge import workspace_root as _wr

        payload = {
            "sessionId": "83fe6d",
            "runId": run_id,
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with (_wr() / "debug-83fe6d.log").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass


# endregion

from cards import (  # noqa: E402
    account_form_card,
    account_view_card,
    bug_ids_from_form_value,
    close_bug_preview_card,
    close_bug_select_card,
    config_change_preview_card,
    config_form_card,
    config_view_card,
    disabled_confirm_card,
    draft_card,
    draft_from_form_value,
    form_field_str,
    help_card,
    query_bug_choice_card,
    query_story_choice_card,
    resolve_config_form_path,
    submit_bug_form_card,
)
from close_bridge import close_bugs, format_close_result  # noqa: E402
from config_store import (  # noqa: E402
    DEFAULT_EDITABLE_PATHS,
    get_by_path,
    path_label,
    snapshot_editable,
    update_editable,
)
from feishu_api import (  # noqa: E402
    FeishuClient,
    aes_decrypt_feishu,
    parse_message_content,
)
from intent import IntentResult, classify_intent  # noqa: E402
from llm_parse import format_draft_reply, parse_bug_from_nl  # noqa: E402
from query_bridge import (  # noqa: E402
    bug_query_title,
    format_bug_list,
    format_story_list,
    looks_like_id,
    query_bugs,
    query_stories,
    recent_bug_list_title,
)
from session import SessionStore  # noqa: E402
from submit_bridge import submit_bug, workspace_root  # noqa: E402
from user_config_store import (  # noqa: E402
    LLM_EDITABLE_PATHS,
    TAPD_EDITABLE_PATHS,
    display_value,
    is_secret_path,
    resolve_llm,
    resolve_project_config,
    snapshot_llm,
    snapshot_tapd,
    update_llm_field,
    update_tapd_field,
)


CONFIRM_RE = re.compile(r"^(确认提交|确认|submit)$", re.I)
CONFIRM_CLOSE_RE = re.compile(r"^(确认关闭|关闭确认)$", re.I)
CONFIRM_CONFIG_RE = re.compile(r"^(确认保存|确认配置|确认保存配置)$", re.I)
CANCEL_RE = re.compile(r"^(取消|cancel|清空)$", re.I)
MODULE_RE = re.compile(r"^(前端|后端)$")

DEFAULT_MENU_KEYS = {
    "help": "help",
    "submit_bug": "submit_bug",
    "query_bug": "query_bug",
    "query_story": "query_story",
    "close_bug": "close_bug",
    "config_view": "config_view",
    "config_edit": "config_edit",
    "account_config": "account_view",
    "cancel_draft": "cancel_draft",
}


def load_bot_config() -> Dict[str, Any]:
    root = workspace_root()
    path = root / ".tapd" / "feishu_bot.json"
    if not path.is_file():
        raise FileNotFoundError(
            f"缺少配置文件: {path}\n请复制 .tapd/feishu_bot.template.json 为 feishu_bot.json 并填写密钥"
        )
    with path.open("r", encoding="utf-8-sig") as f:
        cfg = json.load(f)
    if not str(cfg.get("app_id") or "").strip():
        raise ValueError("feishu_bot.json 缺少 app_id")
    if not str(cfg.get("app_secret") or "").strip():
        raise ValueError("feishu_bot.json 缺少 app_secret")
    llm = cfg.get("llm") or {}
    if not isinstance(llm, dict):
        llm = {}
        cfg["llm"] = llm
    # 全局 LLM 为兜底，可空；用户可在飞书卡片中配置自己的 key
    if not str(llm.get("base_url") or "").strip():
        llm["base_url"] = "https://api.deepseek.com/v1"
    if not str(llm.get("model") or "").strip():
        llm["model"] = "deepseek-chat"
    keys = cfg.get("menu_event_keys") or {}
    if not isinstance(keys, dict):
        keys = {}
    for k, v in DEFAULT_MENU_KEYS.items():
        if k not in keys:
            keys[k] = v
    cfg["menu_event_keys"] = keys
    if not cfg.get("config_editable_paths"):
        cfg["config_editable_paths"] = list(DEFAULT_EDITABLE_PATHS)
    return cfg


class BotApp:
    def __init__(self, cfg: Dict[str, Any]) -> None:
        self.cfg = cfg
        self.feishu = FeishuClient(str(cfg["app_id"]), str(cfg["app_secret"]))
        self.sessions = SessionStore()
        self.inbox_dir = workspace_root() / ".tapd" / "feishu_inbox"
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        self._handled_message_ids: Set[str] = set()
        self._handled_event_ids: Set[str] = set()
        self._lock = threading.Lock()

    @property
    def whitelist(self) -> List[str]:
        return [str(p) for p in (self.cfg.get("config_editable_paths") or DEFAULT_EDITABLE_PATHS)]

    def _run_bg(self, fn: Any, *args: Any, **kwargs: Any) -> None:
        """卡片回调须尽快返回 toast；飞书 API / 慢操作放到后台。"""

        def _safe() -> None:
            try:
                fn(*args, **kwargs)
            except Exception:
                traceback.print_exc()

        threading.Thread(target=_safe, daemon=True).start()

    def reply_text(self, open_id: str, text: str) -> Dict[str, Any]:
        """私聊回 open_id；群聊会话优先回 chat_id，失败则回退私聊。"""
        chat_id = str(self.sessions.get(open_id).reply_chat_id or "").strip()
        if chat_id:
            try:
                result = self.feishu.send_text(
                    chat_id, text, receive_id_type="chat_id"
                )
                # region agent log
                _agent_debug_log(
                    "H4",
                    "server.py:reply_text:group_ok",
                    "Group reply succeeded",
                    {"hasChatId": True, "textLength": len(text or "")},
                    run_id="group-post",
                )
                # endregion
                return result
            except Exception as e:
                # region agent log
                _agent_debug_log(
                    "H4",
                    "server.py:reply_text:group_fail_fallback",
                    "Group reply failed; fallback to open_id",
                    {
                        "hasChatId": True,
                        "errorType": type(e).__name__,
                        "errorCodeHint": "invalid_receive_id"
                        if "invalid receive_id" in str(e)
                        else "other",
                    },
                    run_id="group-post",
                )
                # endregion
        return self.feishu.send_text(open_id, text)

    def reply_interactive(self, open_id: str, card: Dict[str, Any]) -> Dict[str, Any]:
        chat_id = str(self.sessions.get(open_id).reply_chat_id or "").strip()
        if chat_id:
            try:
                return self.feishu.send_interactive(
                    chat_id, card, receive_id_type="chat_id"
                )
            except Exception as e:
                # region agent log
                _agent_debug_log(
                    "H4",
                    "server.py:reply_interactive:group_fail_fallback",
                    "Group card reply failed; fallback to open_id",
                    {"hasChatId": True, "errorType": type(e).__name__},
                    run_id="group-post",
                )
                # endregion
        return self.feishu.send_interactive(open_id, card)

    def menu_key_map(self) -> Dict[str, str]:
        raw = self.cfg.get("menu_event_keys") or {}
        return {str(v): str(k) for k, v in raw.items() if v}

    def verify_token(self, token: Optional[str]) -> bool:
        expected = str(self.cfg.get("verification_token") or "").strip()
        if not expected:
            return True
        return (token or "") == expected

    def unwrap_event(self, body: Dict[str, Any]) -> Dict[str, Any]:
        if "encrypt" in body:
            key = str(self.cfg.get("encrypt_key") or "").strip()
            if not key:
                raise ValueError("收到加密事件，请在 feishu_bot.json 填写 encrypt_key")
            plain = aes_decrypt_feishu(body["encrypt"], key)
            return json.loads(plain.decode("utf-8"))
        return body

    def _dedupe_event(self, event_id: str) -> bool:
        if not event_id:
            return False
        with self._lock:
            if event_id in self._handled_event_ids:
                return True
            self._handled_event_ids.add(event_id)
            if len(self._handled_event_ids) > 5000:
                self._handled_event_ids.clear()
        return False

    def handle_event_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if payload.get("type") == "url_verification" or (
            "challenge" in payload and payload.get("type") in (None, "url_verification")
        ):
            if not self.verify_token(payload.get("token")):
                return {"error": "invalid token"}
            return {"challenge": payload.get("challenge")}

        header = payload.get("header") or {}
        event_type = header.get("event_type") or payload.get("type")
        token = header.get("token") or payload.get("token")
        if not self.verify_token(token):
            return {"error": "invalid token"}

        event_id = str(header.get("event_id") or "")
        if self._dedupe_event(event_id):
            return {"code": 0}

        if event_type == "im.message.receive_v1":
            event = payload.get("event") or {}
            message = event.get("message") or {}
            # region agent log
            _agent_debug_log(
                "H1,H2",
                "server.py:handle_event_payload:message_receive",
                "Received im.message.receive_v1",
                {
                    "chatType": str(message.get("chat_type") or ""),
                    "hasChatId": bool(message.get("chat_id")),
                    "msgType": str(message.get("message_type") or ""),
                    "mentionCount": len(message.get("mentions") or []),
                    "contentLength": len(str(message.get("content") or "")),
                },
            )
            # endregion
            threading.Thread(target=self._safe_handle_message, args=(event,), daemon=True).start()
            return {"code": 0}

        if event_type == "application.bot.menu_v6":
            event = payload.get("event") or {}
            threading.Thread(target=self._safe_handle_menu, args=(event,), daemon=True).start()
            return {"code": 0}

        if event_type in ("card.action.trigger", "card.action.trigger_v1"):
            return self.handle_card_payload(payload)

        if "challenge" in payload:
            return {"challenge": payload["challenge"]}

        return {"code": 0}

    def handle_card_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        has_action = bool(payload.get("action") or (payload.get("event") or {}).get("action"))
        if payload.get("type") == "url_verification" or (
            "challenge" in payload and not has_action
        ):
            if not self.verify_token(payload.get("token")):
                return {"error": "invalid token"}
            return {"challenge": payload.get("challenge")}

        header = payload.get("header") or {}
        token = header.get("token") or payload.get("token")
        if token is not None and not self.verify_token(token):
            return {"toast": {"type": "error", "content": "token 校验失败"}}

        event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
        action = payload.get("action") or event.get("action") or {}
        value = action.get("value") or {}
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                value = {"action": value}

        form_value = action.get("form_value") or {}
        if not isinstance(form_value, dict):
            form_value = {}

        open_id = (
            payload.get("open_id")
            or (payload.get("operator") or {}).get("open_id")
            or ""
        )
        if not open_id:
            op = event.get("operator") or payload.get("operator") or {}
            oid = op.get("operator_id") or {}
            open_id = (
                op.get("open_id")
                or oid.get("open_id")
                or str(payload.get("user_id") or "")
            )

        action_name = str(value.get("action") or "").strip()
        path = str(value.get("path") or "").strip()
        operation_id = str(value.get("operation_id") or "").strip()
        if not action_name and form_value:
            btn_name = str(action.get("name") or "")
            if btn_name == "btn_submit_bug" or "title" in form_value:
                action_name = "submit_bug_form"
            elif btn_name == "btn_submit_project_config":
                action_name = "config_form_submit"
            elif btn_name == "btn_submit_account_config":
                action_name = "account_form_submit"
            elif btn_name == "btn_close_bugs" or "bug_ids" in form_value:
                action_name = "close_bug_form"

        # #region agent log
        try:
            import time as _time
            from submit_bridge import workspace_root as _wr

            with (_wr() / "debug-83fe6d.log").open("a", encoding="utf-8") as _lf:
                _lf.write(
                    json.dumps(
                        {
                            "sessionId": "83fe6d",
                            "runId": "post-fix",
                            "hypothesisId": "H_callback",
                            "location": "server.py:handle_card_payload",
                            "message": "card callback received",
                            "data": {
                                "action_name": action_name,
                                "has_open_id": bool(open_id),
                                "form_keys": sorted(form_value.keys())[:20],
                                "btn_name": str(action.get("name") or ""),
                                "has_operation_id": bool(operation_id),
                            },
                            "timestamp": int(_time.time() * 1000),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        except Exception:
            pass
        # #endregion

        if not open_id or not action_name:
            return {"toast": {"type": "error", "content": "无效的卡片操作"}}
        if action_name == "noop":
            return {"toast": {"type": "info", "content": "该操作已结束"}}

        try:
            result = self.dispatch_action(
                open_id,
                action_name,
                path=path,
                form_value=form_value,
                operation_id=operation_id,
            )
            if not isinstance(result, dict):
                return {"toast": {"type": "info", "content": str(result)}}
            # 兼容旧返回：纯 toast 字段
            if "toast" in result or "card" in result:
                out: Dict[str, Any] = {}
                if result.get("toast"):
                    out["toast"] = result["toast"]
                if result.get("card"):
                    out["card"] = result["card"]
                return out or {"toast": {"type": "info", "content": "已处理"}}
            # dispatch 直接返回 toast 结构 {type, content}
            if "type" in result and "content" in result:
                return {"toast": result}
            return {"toast": {"type": "info", "content": "已处理"}}
        except Exception as e:
            traceback.print_exc()
            return {"toast": {"type": "error", "content": f"处理失败：{e}"}}

    def _card_response(
        self,
        toast: Dict[str, Any],
        *,
        card: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        out: Dict[str, Any] = {"toast": toast}
        if card:
            out["card"] = {"type": "raw", "data": card}
        return out

    def _disabled_card_for_op(
        self,
        *,
        kind: str,
        summary: str,
        status_label: str,
        template: str = "grey",
    ) -> Dict[str, Any]:
        title_map = {
            "close_bug": "确认关闭 Bug",
            "config_edit": "确认保存配置",
            "submit_bug": "缺陷草稿",
        }
        return disabled_confirm_card(
            title=title_map.get(kind, "操作结果"),
            summary_md=summary or "（无摘要）",
            status_label=status_label,
            template=template,
        )

    def dispatch_action(
        self,
        open_id: str,
        action_name: str,
        *,
        path: str = "",
        form_value: Optional[Dict[str, Any]] = None,
        operation_id: str = "",
    ) -> Dict[str, Any]:
        """
        卡片回调路径：本地状态可同步改，飞书发消息一律后台执行，
        以便在飞书约 3s 超时前尽快返回 toast（确认类可附带原地置灰卡）。
        """
        form_value = form_value or {}

        if action_name == "help":
            self._run_bg(self.reply_interactive, open_id, help_card())
            return {"type": "info", "content": "已发送帮助"}

        if action_name == "submit_bug":
            self._run_bg(self.reply_interactive, open_id, submit_bug_form_card())
            return {"type": "info", "content": "请填写提 Bug 表单"}

        if action_name == "query_bug":
            self._run_bg(self.reply_interactive, open_id, query_bug_choice_card())
            return {"type": "info", "content": "请选择查 Bug 方式"}

        if action_name == "query_story":
            self._run_bg(self.reply_interactive, open_id, query_story_choice_card())
            return {"type": "info", "content": "请选择查需求方式"}

        if action_name == "query_bug_keyword":
            session = self.sessions.get(open_id)
            session.status = "awaiting_query_bug"
            self._run_bg(
                self.reply_text,
                open_id,
                "请发送 Bug 关键词或 ID（纯数字按 ID 查）。回复「取消」退出。",
            )
            return {"type": "info", "content": "等待关键词"}

        if action_name == "query_bug_recent":
            self._run_bg(self._run_query_bugs_recent, open_id)
            return {"type": "info", "content": "正在查询最近解决 Bug…"}

        if action_name == "query_story_keyword":
            session = self.sessions.get(open_id)
            session.status = "awaiting_query_story"
            self._run_bg(
                self.reply_text,
                open_id,
                "请发送需求关键词或 ID（纯数字按 ID 查）。回复「取消」退出。",
            )
            return {"type": "info", "content": "等待关键词"}

        if action_name == "query_story_recent":
            self._run_bg(self._run_query_stories_recent, open_id)
            return {"type": "info", "content": "正在查询当前迭代需求…"}

        if action_name == "close_bug":
            self._run_bg(self._run_close_bug_menu, open_id)
            return {"type": "info", "content": "正在加载可关闭 Bug…"}

        if action_name == "close_bug_form":
            bug_ids = bug_ids_from_form_value(form_value)
            if not bug_ids:
                return {"type": "error", "content": "请至少选择一个 Bug"}
            self._run_bg(self._prepare_close_preview, open_id, bug_ids)
            return {"type": "info", "content": "请确认关闭预览"}

        if action_name == "confirm_close":
            return self._accept_confirm_close(open_id, operation_id)

        if action_name == "close_bug_cancel":
            return self._accept_cancel_close(open_id, operation_id)

        if action_name == "submit_bug_form":
            draft = draft_from_form_value(form_value)
            if not draft.get("title") or not draft.get("module") or not draft.get("description"):
                return {"type": "error", "content": "请填写必填项：标题、模块、描述"}
            self._run_bg(self._do_submit_draft, open_id, draft, [])
            return {"type": "info", "content": "正在提交到 TAPD…"}

        if action_name == "config_view":
            session = self.sessions.get(open_id)
            # 取消配置确认：置灰原卡
            if operation_id:
                op = session.get_operation(operation_id)
                if op and op.kind == "config_edit" and op.status == "pending":
                    session.mark_operation(
                        operation_id, "cancelled", clear_payload=True
                    )
                    session.clear_pending_ops()
                    card = self._disabled_card_for_op(
                        kind="config_edit",
                        summary=op.card_summary,
                        status_label="已取消",
                        template="grey",
                    )
                    self._run_bg(self._send_config_view, open_id)
                    return self._card_response(
                        {"type": "info", "content": "已取消"},
                        card=card,
                    )
            if session.status in ("awaiting_config_confirm", "awaiting_config_value"):
                session.clear_pending_ops()
            self._run_bg(self._send_config_view, open_id)
            return {"type": "info", "content": "已发送配置"}

        if action_name == "config_edit":
            self._run_bg(self._send_config_form, open_id)
            return {"type": "info", "content": "已打开项目配置表单"}

        if action_name == "config_form_submit":
            self._run_bg(self._prepare_config_form_preview, open_id, dict(form_value))
            return {"type": "info", "content": "请确认配置变更"}

        if action_name == "confirm_config":
            return self._accept_confirm_config(open_id, operation_id)

        if action_name in (
            "account_view",
            "account_config",
            "llm_view",
            "llm_config",
            "tapd_view",
            "tapd_config",
        ):
            self._run_bg(self._send_account_view, open_id)
            return {"type": "info", "content": "已发送账号配置"}

        if action_name in ("account_edit", "llm_edit", "tapd_edit"):
            self._run_bg(self._send_account_form, open_id)
            return {"type": "info", "content": "已打开账号配置表单"}

        if action_name == "account_form_submit":
            self._run_bg(self._apply_account_config_form, open_id, dict(form_value))
            return {"type": "success", "content": "正在保存账号配置…"}

        if action_name == "cancel_draft":
            return self._accept_cancel_draft(open_id, operation_id)

        if action_name == "confirm_submit":
            return self._accept_confirm_submit(open_id, operation_id)

        if action_name == "module_frontend":
            self._run_bg(self._set_module, open_id, "前端")
            return {"type": "info", "content": "已设为前端"}

        if action_name == "module_backend":
            self._run_bg(self._set_module, open_id, "后端")
            return {"type": "info", "content": "已设为后端"}

        return {"type": "error", "content": f"未知操作：{action_name}"}

    def _accept_confirm_close(self, open_id: str, operation_id: str) -> Dict[str, Any]:
        session = self.sessions.get(open_id)
        op = session.get_operation(operation_id) if operation_id else session.pending_operation
        if op and op.kind == "close_bug":
            if op.status == "processing":
                return self._card_response(
                    {"type": "info", "content": "操作已受理"},
                    card=self._disabled_card_for_op(
                        kind="close_bug",
                        summary=op.card_summary,
                        status_label="处理中",
                        template="blue",
                    ),
                )
            if op.status in ("completed", "cancelled", "failed"):
                return self._card_response(
                    {"type": "info", "content": "操作已结束"},
                    card=self._disabled_card_for_op(
                        kind="close_bug",
                        summary=op.card_summary,
                        status_label="已结束",
                    ),
                )
            if op.status == "pending":
                if operation_id and op.operation_id != operation_id:
                    return {"type": "error", "content": "确认卡已过期，请重新选择"}
                ids = list(op.payload.get("bug_ids") or session.pending_close_ids)
                if not ids:
                    return {"type": "error", "content": "没有待关闭的 Bug，请重新选择"}
                session.mark_operation(op.operation_id, "processing")
                self._run_bg(self._run_close_bugs, open_id, ids, op.operation_id)
                return self._card_response(
                    {"type": "info", "content": f"正在关闭 {len(ids)} 条 Bug…"},
                    card=self._disabled_card_for_op(
                        kind="close_bug",
                        summary=op.card_summary,
                        status_label="处理中…",
                        template="blue",
                    ),
                )

        # 兼容旧卡（无 operation_id）
        ids = list(session.pending_close_ids)
        if not ids:
            return {"type": "error", "content": "没有待关闭的 Bug，请重新选择"}
        self._run_bg(self._run_close_bugs, open_id, ids, "")
        return {"type": "info", "content": f"正在关闭 {len(ids)} 条 Bug…"}

    def _accept_cancel_close(self, open_id: str, operation_id: str) -> Dict[str, Any]:
        session = self.sessions.get(open_id)
        op = session.get_operation(operation_id) if operation_id else session.pending_operation
        summary = op.card_summary if op else "关闭操作已取消。"
        if op and op.kind == "close_bug" and op.status == "pending":
            session.mark_operation(op.operation_id, "cancelled", clear_payload=True)
        session.clear_pending_ops()
        self._run_bg(self.reply_text, open_id, "已取消关闭操作。")
        return self._card_response(
            {"type": "info", "content": "已取消"},
            card=self._disabled_card_for_op(
                kind="close_bug",
                summary=summary,
                status_label="已取消",
            ),
        )

    def _accept_confirm_config(self, open_id: str, operation_id: str) -> Dict[str, Any]:
        session = self.sessions.get(open_id)
        op = session.get_operation(operation_id) if operation_id else session.pending_operation
        if op and op.kind == "config_edit":
            if op.status == "processing":
                return self._card_response(
                    {"type": "info", "content": "操作已受理"},
                    card=self._disabled_card_for_op(
                        kind="config_edit",
                        summary=op.card_summary,
                        status_label="处理中",
                        template="blue",
                    ),
                )
            if op.status != "pending":
                return self._card_response(
                    {"type": "info", "content": "操作已结束"},
                    card=self._disabled_card_for_op(
                        kind="config_edit",
                        summary=op.card_summary,
                        status_label="已结束",
                    ),
                )
            changes = dict(op.payload.get("changes") or session.pending_config_changes)
            if not changes:
                return {"type": "error", "content": "没有待保存的配置，请重新编辑"}
            session.mark_operation(op.operation_id, "processing")
            self._run_bg(self._commit_config_changes, open_id, changes, op.operation_id)
            return self._card_response(
                {"type": "success", "content": "正在保存配置…"},
                card=self._disabled_card_for_op(
                    kind="config_edit",
                    summary=op.card_summary,
                    status_label="处理中…",
                    template="blue",
                ),
            )

        changes = dict(session.pending_config_changes)
        if not changes:
            return {"type": "error", "content": "没有待保存的配置，请重新编辑"}
        self._run_bg(self._commit_config_changes, open_id, changes, "")
        return {"type": "success", "content": "正在保存配置…"}

    def _accept_confirm_submit(self, open_id: str, operation_id: str) -> Dict[str, Any]:
        session = self.sessions.get(open_id)
        op = session.get_operation(operation_id) if operation_id else None
        if op and op.kind == "submit_bug":
            if op.status == "processing":
                return self._card_response(
                    {"type": "info", "content": "操作已受理"},
                    card=self._disabled_card_for_op(
                        kind="submit_bug",
                        summary=op.card_summary,
                        status_label="处理中",
                        template="blue",
                    ),
                )
            if op.status != "pending":
                return self._card_response(
                    {"type": "info", "content": "操作已结束"},
                    card=self._disabled_card_for_op(
                        kind="submit_bug",
                        summary=op.card_summary,
                        status_label="已结束",
                    ),
                )
            session.mark_operation(op.operation_id, "processing")
            self._run_bg(self._do_submit, open_id, session, op.operation_id)
            return self._card_response(
                {"type": "info", "content": "正在提交…"},
                card=self._disabled_card_for_op(
                    kind="submit_bug",
                    summary=op.card_summary,
                    status_label="处理中…",
                    template="blue",
                ),
            )
        self._run_bg(self._do_submit, open_id, session, "")
        return {"type": "info", "content": "正在提交…"}

    def _accept_cancel_draft(self, open_id: str, operation_id: str) -> Dict[str, Any]:
        session = self.sessions.get(open_id)
        op = session.get_operation(operation_id) if operation_id else session.pending_operation
        summary = op.card_summary if op and op.kind == "submit_bug" else "草稿已取消。"
        if op and op.kind == "submit_bug" and op.status == "pending":
            session.mark_operation(op.operation_id, "cancelled", clear_payload=True)
        self.sessions.reset(open_id)
        self._run_bg(self.reply_text, open_id, "已取消，当前草稿已清空。")
        return self._card_response(
            {"type": "info", "content": "草稿已清空"},
            card=self._disabled_card_for_op(
                kind="submit_bug",
                summary=summary,
                status_label="已取消",
            ),
        )

    def _send_config_view(self, open_id: str) -> None:
        rows = snapshot_editable(whitelist=self.whitelist, open_id=open_id)
        card = config_view_card(rows)
        # #region agent log
        try:
            import time as _time
            from submit_bridge import workspace_root as _wr

            body_els = (card.get("body") or {}).get("elements") or []
            tags = [str(e.get("tag") or "") for e in body_els]
            with (_wr() / "debug-83fe6d.log").open("a", encoding="utf-8") as _lf:
                _lf.write(
                    json.dumps(
                        {
                            "sessionId": "83fe6d",
                            "runId": "post-fix",
                            "hypothesisId": "H_view_v2",
                            "location": "server.py:_send_config_view",
                            "message": "sending config_view_card",
                            "data": {
                                "schema": card.get("schema"),
                                "body_tags": tags,
                                "has_action_tag": "action" in tags,
                            },
                            "timestamp": int(_time.time() * 1000),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        except Exception:
            pass
        # #endregion
        try:
            self.reply_interactive(open_id, card)
            # #region agent log
            try:
                import time as _time
                from submit_bridge import workspace_root as _wr

                with (_wr() / "debug-83fe6d.log").open("a", encoding="utf-8") as _lf:
                    _lf.write(
                        json.dumps(
                            {
                                "sessionId": "83fe6d",
                                "runId": "post-fix",
                                "hypothesisId": "H_view_v2",
                                "location": "server.py:_send_config_view:ok",
                                "message": "config_view_card sent ok",
                                "data": {"ok": True},
                                "timestamp": int(_time.time() * 1000),
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
            except Exception:
                pass
            # #endregion
        except Exception as e:
            # #region agent log
            try:
                import time as _time
                from submit_bridge import workspace_root as _wr

                with (_wr() / "debug-83fe6d.log").open("a", encoding="utf-8") as _lf:
                    _lf.write(
                        json.dumps(
                            {
                                "sessionId": "83fe6d",
                                "runId": "post-fix",
                                "hypothesisId": "H_view_v2",
                                "location": "server.py:_send_config_view:err",
                                "message": "config_view_card send failed",
                                "data": {"err": str(e)[:300]},
                                "timestamp": int(_time.time() * 1000),
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
            except Exception:
                pass
            # #endregion
            raise

    def _send_config_form(self, open_id: str) -> None:
        rows = snapshot_editable(whitelist=self.whitelist, open_id=open_id)
        card = config_form_card(rows)
        # #region agent log
        try:
            import json as _json
            import time as _time
            from submit_bridge import workspace_root as _wr

            body_els = (card.get("body") or {}).get("elements") or []
            tags = [str(e.get("tag") or "") for e in body_els]
            form_els = []
            for e in body_els:
                if e.get("tag") == "form":
                    form_els = [str(x.get("tag") or "") for x in (e.get("elements") or [])]
            with (_wr() / "debug-83fe6d.log").open("a", encoding="utf-8") as _lf:
                _lf.write(
                    _json.dumps(
                        {
                            "sessionId": "83fe6d",
                            "runId": "post-fix",
                            "hypothesisId": "V2_action",
                            "location": "server.py:_send_config_form",
                            "message": "sending config_form_card",
                            "data": {
                                "schema": card.get("schema"),
                                "body_tags": tags,
                                "has_action_tag": "action" in tags,
                                "form_child_tags_tail": form_els[-3:],
                                "row_count": len(rows),
                            },
                            "timestamp": int(_time.time() * 1000),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        except Exception:
            pass
        # #endregion
        try:
            self.reply_interactive(open_id, card)
            # #region agent log
            try:
                import json as _json
                import time as _time
                from submit_bridge import workspace_root as _wr

                with (_wr() / "debug-83fe6d.log").open("a", encoding="utf-8") as _lf:
                    _lf.write(
                        _json.dumps(
                            {
                                "sessionId": "83fe6d",
                                "runId": "post-fix",
                                "hypothesisId": "V2_action",
                                "location": "server.py:_send_config_form:ok",
                                "message": "config_form_card sent ok",
                                "data": {"ok": True},
                                "timestamp": int(_time.time() * 1000),
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
            except Exception:
                pass
            # #endregion
        except Exception as e:
            # #region agent log
            try:
                import json as _json
                import time as _time
                from submit_bridge import workspace_root as _wr

                with (_wr() / "debug-83fe6d.log").open("a", encoding="utf-8") as _lf:
                    _lf.write(
                        _json.dumps(
                            {
                                "sessionId": "83fe6d",
                                "runId": "post-fix",
                                "hypothesisId": "V2_action",
                                "location": "server.py:_send_config_form:err",
                                "message": "config_form_card send failed",
                                "data": {"err": str(e)[:300]},
                                "timestamp": int(_time.time() * 1000),
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
            except Exception:
                pass
            # #endregion
            raise

    def _send_account_view(self, open_id: str) -> None:
        llm_raw = snapshot_llm(open_id, self.cfg)
        tapd_raw = snapshot_tapd(open_id)
        llm_rows = [(p, lab, display_value(p, val)) for p, lab, val in llm_raw]
        tapd_rows = [(p, lab, display_value(p, val)) for p, lab, val in tapd_raw]
        self.reply_interactive(open_id, account_view_card(llm_rows, tapd_rows))

    def _send_account_form(self, open_id: str) -> None:
        llm_raw = snapshot_llm(open_id, self.cfg)
        tapd_raw = snapshot_tapd(open_id)
        self.reply_interactive(open_id, account_form_card(llm_raw, tapd_raw))

    def _apply_project_config_form(self, open_id: str, form_value: Dict[str, Any]) -> None:
        """兼容旧路径：直接保存（一般不再调用，改走预览确认）。"""
        try:
            changes = self._collect_config_form_changes(open_id, form_value)
            if not changes:
                self.reply_text(open_id, "未收到可保存的字段，请重新打开表单。")
                return
            self._commit_config_changes(open_id, {p: nv for p, _l, _o, nv in changes})
        except Exception as e:
            traceback.print_exc()
            self.reply_text(open_id, f"保存失败：{e}")

    def _collect_config_form_changes(
        self, open_id: str, form_value: Dict[str, Any]
    ) -> List[Tuple[str, str, Any, Any]]:
        normalized: Dict[str, Any] = {}
        for k, v in form_value.items():
            normalized[resolve_config_form_path(str(k))] = v
        cfg = resolve_project_config(open_id)
        changes: List[Tuple[str, str, Any, Any]] = []
        for path in self.whitelist:
            if path not in normalized:
                continue
            raw = form_field_str(normalized, path)
            if path == "link_story.enabled":
                val: Any = raw.lower() in ("true", "1", "yes", "on", "开启", "开")
            else:
                val = raw
            old = get_by_path(cfg, path)
            if str(old) == str(val) and type(old) == type(val):
                continue
            if old == val:
                continue
            # 字符串比较：空与 None 视为相同
            if (old is None or old == "") and (val is None or val == ""):
                continue
            if str(old if old is not None else "") == str(val if val is not None else ""):
                if not isinstance(old, bool) and not isinstance(val, bool):
                    continue
            changes.append((path, path_label(path), old, val))
        return changes

    def _prepare_config_form_preview(self, open_id: str, form_value: Dict[str, Any]) -> None:
        try:
            changes = self._collect_config_form_changes(open_id, form_value)
            if not changes:
                self.reply_text(open_id, "没有检测到变更（字段与当前值相同）。")
                self._send_config_view(open_id)
                return
            session = self.sessions.get(open_id)
            session.pending_config_changes = {p: nv for p, _l, _o, nv in changes}
            session.status = "awaiting_config_confirm"
            summary_lines = ["将保存以下配置变更（写入你的个人覆盖，确认后生效）：", ""]
            for path, label, old_v, new_v in changes:
                summary_lines.append(
                    f"- **{label}**（`{path}`）\n  {old_v} → **{new_v}**"
                )
            op = session.begin_operation(
                "config_edit",
                {"changes": dict(session.pending_config_changes)},
                card_summary="\n".join(summary_lines),
            )
            self.reply_interactive(
                open_id,
                config_change_preview_card(changes, operation_id=op.operation_id),
            )
        except Exception as e:
            traceback.print_exc()
            self.reply_text(open_id, f"生成配置预览失败：{e}")

    def _prepare_config_nl_preview(self, open_id: str, path: str, value: Any) -> None:
        try:
            if path not in self.whitelist:
                self.reply_text(open_id, f"配置项不在可修改白名单：{path}")
                return
            cfg = resolve_project_config(open_id)
            old = get_by_path(cfg, path)
            if path == "link_story.enabled" and isinstance(value, str):
                value = value.lower() in ("true", "1", "yes", "on", "开启", "开")
            session = self.sessions.get(open_id)
            session.pending_config_changes = {path: value}
            session.status = "awaiting_config_confirm"
            changes = [(path, path_label(path), old, value)]
            summary = (
                "将保存以下配置变更（写入你的个人覆盖，确认后生效）：\n\n"
                f"- **{path_label(path)}**（`{path}`）\n"
                f"  {old} → **{value}**"
            )
            op = session.begin_operation(
                "config_edit",
                {"changes": {path: value}},
                card_summary=summary,
            )
            card = config_change_preview_card(changes, operation_id=op.operation_id)
            self.reply_interactive(open_id, card)
        except Exception as e:
            traceback.print_exc()
            self.reply_text(open_id, f"生成配置预览失败：{e}")

    def _commit_config_changes(
        self,
        open_id: str,
        changes: Dict[str, Any],
        operation_id: str = "",
    ) -> None:
        try:
            updated: List[str] = []
            for path, val in changes.items():
                if path not in self.whitelist:
                    continue
                update_editable(path, val, whitelist=self.whitelist, open_id=open_id)
                updated.append(path_label(path))
            session = self.sessions.get(open_id)
            if operation_id:
                session.mark_operation(
                    operation_id,
                    "completed" if updated else "failed",
                    clear_payload=True,
                )
            session.clear_pending_ops()
            if not updated:
                self.reply_text(open_id, "未收到可保存的字段。")
                return
            self.reply_text(open_id, f"项目配置已保存（{len(updated)} 项）：{', '.join(updated)}")
            self._send_config_view(open_id)
        except Exception as e:
            traceback.print_exc()
            if operation_id:
                self.sessions.get(open_id).mark_operation(
                    operation_id, "failed", clear_payload=True
                )
            self.reply_text(open_id, f"保存失败：{e}")

    def _apply_account_config_form(self, open_id: str, form_value: Dict[str, Any]) -> None:
        try:
            updated: List[str] = []
            secrets_updated: List[str] = []
            for path in LLM_EDITABLE_PATHS + TAPD_EDITABLE_PATHS:
                if path not in form_value:
                    continue
                raw = form_field_str(form_value, path)
                if is_secret_path(path):
                    if not raw:
                        continue
                    if path.startswith("llm."):
                        update_llm_field(open_id, path, raw, bot_cfg=self.cfg)
                    else:
                        update_tapd_field(open_id, path, raw)
                    secrets_updated.append(path_label(path))
                    continue
                if path.startswith("llm."):
                    update_llm_field(open_id, path, raw, bot_cfg=self.cfg)
                else:
                    update_tapd_field(open_id, path, raw)
                updated.append(path_label(path))
            if not updated and not secrets_updated:
                self.reply_text(open_id, "没有需要保存的变更（密钥留空表示不修改）。")
                return
            parts: List[str] = []
            if updated:
                parts.append(f"已更新：{', '.join(updated)}")
            if secrets_updated:
                parts.append(f"已更新密钥：{', '.join(secrets_updated)}（明文不回显）")
            self.reply_text(open_id, "账号配置已保存。\n" + "\n".join(parts))
            self._send_account_view(open_id)
        except Exception as e:
            traceback.print_exc()
            self.reply_text(open_id, f"保存失败：{e}")

    def _set_module(self, open_id: str, module: str) -> None:
        session = self.sessions.get(open_id)
        if not session.draft:
            self.reply_text(open_id, "当前没有草稿。请先描述要提交的缺陷。")
            return
        session.draft["module"] = module
        session.draft["module_confidence"] = "high"
        session.module_pending = False
        session.status = "awaiting_confirm"
        reply = format_draft_reply(session.draft, image_count=len(session.image_paths))
        summary = format_draft_reply(session.draft, image_count=len(session.image_paths))
        op = session.begin_operation(
            "submit_bug",
            {"draft": dict(session.draft), "image_paths": list(session.image_paths)},
            card_summary=summary,
        )
        self.reply_text(open_id, reply)
        self.reply_interactive(
            open_id,
            draft_card(
                session.draft,
                image_count=len(session.image_paths),
                operation_id=op.operation_id,
            ),
        )

    def _run_close_bug_menu(self, open_id: str) -> None:
        try:
            self.reply_text(open_id, "正在查询最近 resolved（已解决未关闭）Bug…")
            rows, err = query_bugs(recent_open=True, open_id=open_id)
            if err:
                self.reply_text(open_id, f"查询失败：{err}")
                return
            if not rows:
                self.reply_text(
                    open_id,
                    f"未找到可关闭的 Bug（{recent_bug_list_title(open_id).rstrip('：')}）。",
                )
                return
            scope = recent_bug_list_title(open_id).rstrip("：")
            self.reply_interactive(
                open_id,
                close_bug_select_card(rows, scope_title=scope),
            )
        except Exception as e:
            traceback.print_exc()
            self.reply_text(open_id, f"加载关闭列表失败：{e}")

    def _prepare_close_preview(
        self,
        open_id: str,
        bug_ids: List[str],
        *,
        rows: Optional[List[Dict[str, str]]] = None,
    ) -> None:
        try:
            preview_rows: List[Dict[str, str]] = []
            if rows:
                by_id = {str(r.get("id") or ""): r for r in rows}
                for bid in bug_ids:
                    if bid in by_id:
                        preview_rows.append(by_id[bid])
                    else:
                        preview_rows.append(
                            {
                                "id": bid,
                                "title": "（待校验）",
                                "status": "resolved",
                                "url": "",
                                "reporter": "",
                                "current_owner": "",
                            }
                        )
            else:
                for bid in bug_ids:
                    found, err = query_bugs(bug_id=bid, open_id=open_id)
                    if err:
                        preview_rows.append(
                            {"id": bid, "title": f"查询失败：{err[:40]}", "status": "?", "url": ""}
                        )
                    elif found:
                        preview_rows.append(found[0])
                    else:
                        preview_rows.append(
                            {"id": bid, "title": "未找到", "status": "?", "url": ""}
                        )
            session = self.sessions.get(open_id)
            session.pending_close_ids = [
                str(r.get("id") or "").strip() for r in preview_rows if r.get("id")
            ]
            session.pending_close_rows = preview_rows
            session.status = "awaiting_close_confirm"
            # 摘要供确认后置灰卡使用
            summary_lines = [
                "将把以下缺陷状态更新为 **closed**，处理人设为创建人（reporter）。",
                "",
            ]
            for idx, row in enumerate(preview_rows, 1):
                summary_lines.append(
                    "{}. [{}] {}".format(
                        idx, row.get("id"), row.get("title") or "（无标题）"
                    )
                )
            op = session.begin_operation(
                "close_bug",
                {"bug_ids": list(session.pending_close_ids)},
                card_summary="\n".join(summary_lines),
            )
            self.reply_interactive(
                open_id,
                close_bug_preview_card(preview_rows, operation_id=op.operation_id),
            )
        except Exception as e:
            traceback.print_exc()
            self.reply_text(open_id, f"生成关闭预览失败：{e}")

    def _run_close_bugs(
        self, open_id: str, bug_ids: List[str], operation_id: str = ""
    ) -> None:
        try:
            code, parsed, raw = close_bugs(bug_ids, open_id=open_id)
            message = format_close_result(parsed, raw=raw)
            if code != 0 and not parsed:
                message = f"关闭失败：{raw[:300] or '未知错误'}"
            session = self.sessions.get(open_id)
            if operation_id:
                session.mark_operation(
                    operation_id,
                    "completed" if code == 0 or parsed else "failed",
                    result_message=message,
                    clear_payload=True,
                )
            session.clear_pending_ops()
            self.reply_text(open_id, message)
        except Exception as e:
            traceback.print_exc()
            if operation_id:
                self.sessions.get(open_id).mark_operation(
                    operation_id, "failed", clear_payload=True
                )
            self.reply_text(open_id, f"关闭失败：{e}")

    def _run_nl_close_bug(self, open_id: str, slots: Dict[str, Any]) -> None:
        try:
            bug_id = str(slots.get("bug_id") or "").strip()
            keyword = str(slots.get("keyword") or "").strip()
            recent = bool(slots.get("recent"))
            rows: List[Dict[str, str]] = []
            err = ""
            if bug_id:
                rows, err = query_bugs(bug_id=bug_id, open_id=open_id)
            elif keyword or slots.get("days") or slots.get("start_date") or slots.get("mine"):
                rows, err = query_bugs(
                    keyword=keyword or None,
                    mine=bool(slots.get("mine") or recent),
                    days=slots.get("days"),
                    start_date=str(slots.get("start_date") or "") or None,
                    end_date=str(slots.get("end_date") or "") or None,
                    status=str(slots.get("status") or "resolved") or None,
                    open_id=open_id,
                )
                resolved = [
                    r
                    for r in rows
                    if str(r.get("status") or "").strip().lower() == "resolved"
                ]
                if resolved:
                    rows = resolved
            else:
                rows, err = query_bugs(recent_open=True, open_id=open_id)
                recent = True

            if err:
                self.reply_text(open_id, f"查询失败：{err}")
                return
            if not rows:
                tip = "未找到可关闭的 Bug。"
                if keyword:
                    tip = f"未找到与「{keyword}」匹配的 resolved Bug。"
                elif recent or not bug_id:
                    tip = f"未找到可关闭的 Bug（{recent_bug_list_title(open_id).rstrip('：')}）。"
                self.reply_text(open_id, tip)
                return

            if len(rows) == 1:
                self._prepare_close_preview(open_id, [rows[0]["id"]], rows=rows)
                return

            # Agent 路径：多候选用文本列出并追问 ID，不弹多选单元卡
            lines = [
                f"找到 {len(rows)} 条候选，请回复要关闭的 Bug ID（可多个，逗号分隔）：",
                "",
            ]
            for i, r in enumerate(rows[:10], 1):
                lines.append(f"{i}. [{r['id']}] {r['title']}（{r.get('status') or '?'}）")
            session = self.sessions.get(open_id)
            session.status = "awaiting_close_confirm"
            session.pending_close_rows = rows[:10]
            session.pending_close_ids = []
            self.reply_text(open_id, "\n".join(lines))
        except Exception as e:
            traceback.print_exc()
            self.reply_text(open_id, f"关闭流程失败：{e}")

    def _route_intent(self, open_id: str, intent: IntentResult) -> None:
        kind = intent.kind
        slots = intent.slots or {}

        if kind == "help":
            self.reply_interactive(open_id, help_card())
            return

        if kind == "clarify":
            msg = intent.clarification or "请补充更明确的操作说明。"
            self.reply_text(open_id, msg)
            return

        if kind == "config_view":
            self._send_config_view(open_id)
            return

        if kind == "config_edit":
            path = str(slots.get("path") or "").strip()
            value = slots.get("value")
            if path and value is not None and str(value).strip() != "":
                self._prepare_config_nl_preview(open_id, path, value)
                return
            if path and (value is None or str(value).strip() == ""):
                session = self.sessions.get(open_id)
                session.pending_config_path = path
                session.status = "awaiting_config_value"
                self.reply_text(
                    open_id,
                    f"请发送「{path_label(path)}」的新值，或回复「取消」。",
                )
                return
            self.reply_text(
                open_id,
                intent.clarification
                or "请说明要改的配置，例如：把迭代改成 1.2.0；也可使用菜单「修改配置」。",
            )
            return

        if kind == "query_bug":
            self._run_bg(self._run_agent_query_bugs, open_id, slots)
            return

        if kind == "query_story":
            if slots.get("story_id"):
                self._run_query_stories_keyword(open_id, str(slots["story_id"]))
                return
            if slots.get("keyword"):
                self._run_query_stories_keyword(open_id, str(slots["keyword"]))
                return
            if slots.get("recent"):
                self._run_query_stories_recent(open_id)
                return
            session = self.sessions.get(open_id)
            session.status = "awaiting_query_story"
            self.reply_text(
                open_id,
                "请发送需求关键词或 ID（纯数字按 ID 查）。回复「取消」退出。",
            )
            return

        if kind == "close_bug":
            self._run_nl_close_bug(open_id, slots)
            return

        if kind == "submit_bug":
            # 交回消息入口走草稿解析
            return

        self.reply_text(open_id, f"暂不支持该操作：{kind}")

    def _run_agent_query_bugs(self, open_id: str, slots: Dict[str, Any]) -> None:
        try:
            bug_id = str(slots.get("bug_id") or "").strip()
            keyword = str(slots.get("keyword") or "").strip() or None
            days = slots.get("days")
            start_date = str(slots.get("start_date") or "").strip() or None
            end_date = str(slots.get("end_date") or "").strip() or None
            status = str(slots.get("status") or "").strip() or None
            mine = bool(slots.get("mine"))
            recent = bool(slots.get("recent"))
            # 有时间窗/mine 时丢掉残留噪声 keyword
            if keyword and (days or start_date or end_date or mine):
                if re.fullmatch(r"(我|提交|提的|创建的|我的|相关|的|\s)+", keyword):
                    keyword = None

            # #region agent log
            _dbg83(
                "H4,H5",
                "server.py:_run_agent_query_bugs",
                "agent query path",
                {
                    "mine": mine,
                    "days": days,
                    "keyword": keyword,
                    "status": status,
                    "recent": recent,
                    "bug_id": bug_id,
                },
                run_id="post-fix",
            )
            # #endregion

            has_time = bool(days or start_date or end_date)
            if not bug_id and not keyword and not has_time and not recent and not status and not mine:
                session = self.sessions.get(open_id)
                session.status = "awaiting_query_bug"
                self.reply_text(
                    open_id,
                    "请补充查询条件，例如：查我近三天提的 bug、查登录相关 bug，或发送 Bug ID。回复「取消」退出。",
                )
                return

            self.reply_text(open_id, "正在查询 Bug…")
            rows, err = query_bugs(
                bug_id=bug_id or None,
                keyword=keyword,
                recent_open=recent and not has_time and not keyword,
                mine=mine or recent,
                days=int(days) if days not in (None, "") else None,
                start_date=start_date,
                end_date=end_date,
                status=status,
                open_id=open_id,
            )
            if err:
                self.reply_text(open_id, f"查询失败：{err}")
                return
            title = bug_query_title(
                open_id=open_id,
                keyword=keyword,
                bug_id=bug_id or None,
                mine=mine or recent,
                days=int(days) if days not in (None, "") else None,
                start_date=start_date,
                end_date=end_date,
                status=status,
                recent_open=recent and not has_time and not keyword,
            )
            self.reply_text(open_id, format_bug_list(rows, title=title))
        except Exception as e:
            traceback.print_exc()
            self.reply_text(open_id, f"查询失败：{e}")

    def _run_query_bugs_recent(self, open_id: str) -> None:
        try:
            self.reply_text(open_id, "正在查询你提交的最近解决（resolved）Bug…")
            rows, err = query_bugs(recent_open=True, open_id=open_id)
            if err:
                self.reply_text(open_id, f"查询失败：{err}")
                return
            self.reply_text(
                open_id, format_bug_list(rows, title=recent_bug_list_title(open_id))
            )
        except Exception as e:
            traceback.print_exc()
            self.reply_text(open_id, f"查询失败：{e}")

    def _run_query_stories_recent(self, open_id: str) -> None:
        try:
            self.reply_text(open_id, "正在查询当前迭代需求…")
            rows, err = query_stories(recent_in_iteration=True, open_id=open_id)
            if err:
                self.reply_text(open_id, f"查询失败：{err}")
                return
            self.reply_text(
                open_id, format_story_list(rows, title="当前迭代需求（最多 10 条）：")
            )
        except Exception as e:
            traceback.print_exc()
            self.reply_text(open_id, f"查询失败：{e}")

    def _run_query_bugs_keyword(self, open_id: str, text: str) -> None:
        try:
            # #region agent log
            _dbg83(
                "H1,H2",
                "server.py:_run_query_bugs_keyword",
                "keyword query path used",
                {"textPreview": (text or "")[:80]},
            )
            # #endregion
            # 防回归：时间/我提交类句子不得当标题关键词
            from intent import enrich_slots_from_text

            slots = enrich_slots_from_text(text, {})
            if slots.get("days") or slots.get("start_date") or slots.get("mine"):
                # #region agent log
                _dbg83(
                    "H1,H4",
                    "server.py:_run_query_bugs_keyword:redirect",
                    "redirect keyword to agent time/mine query",
                    {"slots": dict(slots)},
                    run_id="post-fix",
                )
                # #endregion
                self._run_agent_query_bugs(open_id, slots)
                return
            if looks_like_id(text):
                rows, err = query_bugs(bug_id=text.strip(), open_id=open_id)
                title = f"Bug ID={text.strip()}："
            else:
                rows, err = query_bugs(keyword=text.strip(), open_id=open_id)
                title = f"标题含「{text.strip()}」的 Bug："
            if err:
                self.reply_text(open_id, f"查询失败：{err}")
                return
            self.reply_text(open_id, format_bug_list(rows, title=title))
        except Exception as e:
            traceback.print_exc()
            self.reply_text(open_id, f"查询失败：{e}")

    def _run_query_stories_keyword(self, open_id: str, text: str) -> None:
        try:
            if looks_like_id(text):
                rows, err = query_stories(story_id=text.strip(), open_id=open_id)
                title = f"需求 ID={text.strip()}："
            else:
                rows, err = query_stories(keyword=text.strip(), open_id=open_id)
                title = f"名称含「{text.strip()}」的需求："
            if err:
                self.reply_text(open_id, f"查询失败：{err}")
                return
            self.reply_text(open_id, format_story_list(rows, title=title))
        except Exception as e:
            traceback.print_exc()
            self.reply_text(open_id, f"查询失败：{e}")

    def _safe_handle_menu(self, event: Dict[str, Any]) -> None:
        try:
            self.handle_menu_event(event)
        except Exception:
            traceback.print_exc()
            try:
                op = event.get("operator") or {}
                open_id = (op.get("operator_id") or {}).get("open_id") or op.get("open_id") or ""
                if open_id:
                    self.reply_text(open_id, "处理菜单时出错，请稍后重试。")
            except Exception:
                traceback.print_exc()

    def handle_menu_event(self, event: Dict[str, Any]) -> None:
        op = event.get("operator") or {}
        open_id = (op.get("operator_id") or {}).get("open_id") or op.get("open_id") or ""
        if not open_id:
            return
        event_key = str(event.get("event_key") or "").strip()
        logical = self.menu_key_map().get(event_key, event_key)
        self.dispatch_action(open_id, logical)

    def _safe_handle_message(self, event: Dict[str, Any]) -> None:
        try:
            self.handle_message_event(event)
        except Exception:
            traceback.print_exc()
            try:
                sender = event.get("sender") or {}
                open_id = (
                    (sender.get("sender_id") or {}).get("open_id")
                    or sender.get("open_id")
                    or ""
                )
                if open_id:
                    self.reply_text(open_id, "处理消息时出错，请稍后重试或联系管理员查看服务日志。")
            except Exception:
                traceback.print_exc()

    def handle_message_event(self, event: Dict[str, Any]) -> None:
        message = event.get("message") or {}
        sender = event.get("sender") or {}
        chat_type = str(message.get("chat_type") or "").strip()
        chat_id = str(message.get("chat_id") or "").strip()
        mentions = message.get("mentions") if isinstance(message.get("mentions"), list) else []
        is_group = bool(chat_type and chat_type != "p2p")

        if is_group:
            # 群聊仅处理 @机器人，避免刷屏
            if not mentions:
                # region agent log
                _agent_debug_log(
                    "H1",
                    "server.py:handle_message_event:drop_group_no_mention",
                    "Dropped group message without mention",
                    {"chatType": chat_type, "hasChatId": bool(chat_id)},
                    run_id="group-post",
                )
                # endregion
                return
            # region agent log
            _agent_debug_log(
                "H1,H4",
                "server.py:handle_message_event:accept_group",
                "Accepted group @bot message",
                {
                    "chatType": chat_type,
                    "hasChatId": bool(chat_id),
                    "mentionCount": len(mentions),
                    "msgType": str(message.get("message_type") or ""),
                },
                run_id="group-post",
            )
            # endregion

        message_id = str(message.get("message_id") or "")
        with self._lock:
            if message_id and message_id in self._handled_message_ids:
                return
            if message_id:
                self._handled_message_ids.add(message_id)
                if len(self._handled_message_ids) > 5000:
                    self._handled_message_ids.clear()

        open_id = (sender.get("sender_id") or {}).get("open_id") or ""
        if not open_id:
            return

        session = self.sessions.get(open_id)
        session.reply_chat_id = chat_id if is_group else None

        msg_type = str(message.get("message_type") or "text")
        content_raw = str(message.get("content") or "{}")
        text, image_keys = parse_message_content(msg_type, content_raw)
        # 去掉飞书 @_user_N 占位，避免干扰意图识别
        if text:
            text = re.sub(r"@_user_\d+", " ", text)
            text = re.sub(r"\s+", " ", text).strip()

        for key in image_keys:
            dest = self.inbox_dir / f"{message_id}_{key}.png"
            try:
                self.feishu.download_message_resource(
                    message_id, key, resource_type="image", dest_path=dest
                )
                session.image_paths.append(str(dest))
            except Exception as e:
                self.reply_text(open_id, f"下载图片失败：{e}")

        text = (text or "").strip()
        if not text and not image_keys:
            self.reply_text(open_id, "未识别到文本或图片。请直接描述缺陷，或发送截图。")
            return

        # #region agent log
        _dbg83(
            "H1,H2",
            "server.py:handle_message:entry",
            "message entry",
            {
                "status": session.status,
                "textPreview": text[:80],
                "hasImage": bool(image_keys),
                "codeHasAgentQuery": hasattr(self, "_run_agent_query_bugs"),
            },
        )
        # #endregion

        # 查询态：若用户直接发完整查询句，重新走 agent，勿当纯标题关键词
        if session.status in ("awaiting_query_bug", "awaiting_query_story"):
            # #region agent log
            _dbg83(
                "H2",
                "server.py:awaiting_query",
                "in awaiting query state",
                {"status": session.status, "textPreview": text[:80]},
            )
            # #endregion
            if text and CANCEL_RE.match(text):
                session.status = "idle"
                self.reply_text(open_id, "已取消查询。")
                return
            if not text:
                self.reply_text(open_id, "请发送关键词或 ID，或回复「取消」。")
                return
            qstatus = session.status
            # 完整自然语言查询 → 退出追问态，走 agent
            if qstatus == "awaiting_query_bug" and re.search(
                r"(查|查询|搜).{0,16}(bug|缺陷)|近\s*[0-9一二两三四五六七八九十]+\s*天|我.{0,6}(提|提交)",
                text,
                re.I,
            ):
                session.status = "idle"
                llm_cfg = resolve_llm(open_id, self.cfg)
                intent = classify_intent(
                    text, llm_cfg=llm_cfg, use_llm=True, agent_first=True
                )
                # #region agent log
                _dbg83(
                    "H2,H4",
                    "server.py:awaiting_query:reclassify",
                    "reclassified awaiting query text",
                    {"kind": intent.kind, "slots": dict(intent.slots or {})},
                )
                # #endregion
                if intent.kind == "query_bug":
                    self.reply_text(open_id, "收到，正在处理…")
                    self._run_bg(self._run_agent_query_bugs, open_id, intent.slots or {})
                    return
            session.status = "idle"
            if qstatus == "awaiting_query_bug":
                threading.Thread(
                    target=self._run_query_bugs_keyword, args=(open_id, text), daemon=True
                ).start()
            else:
                threading.Thread(
                    target=self._run_query_stories_keyword, args=(open_id, text), daemon=True
                ).start()
            return

        # 关闭确认态
        if session.status == "awaiting_close_confirm":
            if text and CANCEL_RE.match(text):
                session.clear_pending_ops()
                self.reply_text(open_id, "已取消关闭操作。")
                return
            if text and CONFIRM_CLOSE_RE.match(text):
                ids = list(session.pending_close_ids)
                op = session.pending_operation
                if op and op.kind == "close_bug" and op.status == "pending":
                    ids = list(op.payload.get("bug_ids") or ids)
                if not ids:
                    self.reply_text(open_id, "没有待关闭的 Bug，请先回复 Bug ID 或在卡片上确认。")
                    return
                op_id = op.operation_id if op and op.kind == "close_bug" else ""
                if op_id:
                    session.mark_operation(op_id, "processing")
                threading.Thread(
                    target=self._run_close_bugs,
                    args=(open_id, ids, op_id),
                    daemon=True,
                ).start()
                return
            # Agent 多候选：用户回复 ID 列表 → 进入确认预览
            if text and not session.pending_close_ids:
                id_candidates = re.findall(r"\d{6,}", text)
                if id_candidates:
                    allowed = {
                        str(r.get("id") or "").strip() for r in session.pending_close_rows
                    }
                    picked = [i for i in id_candidates if not allowed or i in allowed]
                    if not picked:
                        picked = id_candidates
                    self._run_bg(self._prepare_close_preview, open_id, picked)
                    return
                self.reply_text(
                    open_id,
                    "请回复要关闭的 Bug ID（可多个，逗号分隔），或回复「取消」。",
                )
                return
            self.reply_text(
                open_id,
                "请在卡片上点「确认关闭」，或回复「确认关闭」/「取消」。",
            )
            return

        # 配置值追问态
        if session.status == "awaiting_config_value":
            if text and CANCEL_RE.match(text):
                session.clear_pending_ops()
                self.reply_text(open_id, "已取消配置修改。")
                return
            path = session.pending_config_path or ""
            if not path:
                session.clear_pending_ops()
                self.reply_text(open_id, "配置项已失效，请重新说明要修改的字段。")
                return
            if not text:
                self.reply_text(open_id, "请发送新值，或回复「取消」。")
                return
            threading.Thread(
                target=self._prepare_config_nl_preview,
                args=(open_id, path, text),
                daemon=True,
            ).start()
            return

        # 配置确认态
        if session.status == "awaiting_config_confirm":
            if text and CANCEL_RE.match(text):
                session.clear_pending_ops()
                self.reply_text(open_id, "已取消配置保存。")
                return
            if text and CONFIRM_CONFIG_RE.match(text):
                changes = dict(session.pending_config_changes)
                op = session.pending_operation
                if op and op.kind == "config_edit" and op.status == "pending":
                    changes = dict(op.payload.get("changes") or changes)
                if not changes:
                    session.clear_pending_ops()
                    self.reply_text(open_id, "没有待保存的配置。")
                    return
                op_id = op.operation_id if op and op.kind == "config_edit" else ""
                if op_id:
                    session.mark_operation(op_id, "processing")
                threading.Thread(
                    target=self._commit_config_changes,
                    args=(open_id, changes, op_id),
                    daemon=True,
                ).start()
                return
            self.reply_text(
                open_id,
                "请在卡片上点「确认保存」，或回复「确认保存」/「取消」。",
            )
            return

        if text and CANCEL_RE.match(text):
            self.sessions.reset(open_id)
            self.reply_text(open_id, "已取消，当前草稿已清空。")
            return

        if text and MODULE_RE.match(text) and session.status in ("drafting", "awaiting_confirm", "idle"):
            if session.draft:
                self._set_module(open_id, text)
                return

        if text and CONFIRM_RE.match(text) and session.status in ("drafting", "awaiting_confirm"):
            op = session.pending_operation
            op_id = op.operation_id if op and op.kind == "submit_bug" else ""
            if op_id and op and op.status == "pending":
                session.mark_operation(op_id, "processing")
            self._do_submit(open_id, session, op_id)
            return

        if not text and image_keys:
            session.status = "drafting"
            self.reply_text(
                open_id,
                f"已收到 {len(image_keys)} 张图片。请用文字补充问题描述（可说明前端/后端）。",
            )
            return

        # 草稿态：只继续提单流程，不走命令意图（避免误打断）
        if session.status in ("drafting", "awaiting_confirm"):
            llm_cfg = resolve_llm(open_id, self.cfg)
            if not str(llm_cfg.get("api_key") or "").strip():
                self.reply_text(
                    open_id,
                    "尚未配置 LLM API Key。请在「账号配置」→「修改配置」表单中填写 Key，"
                    "或由管理员在 feishu_bot.json 配置全局兜底。",
                )
                self._send_account_view(open_id)
                return
            session.history.append({"role": "user", "content": text})
            self.reply_text(open_id, "正在整理缺陷草稿，请稍候…")
            draft = parse_bug_from_nl(
                llm_cfg=llm_cfg,
                user_text=text,
                prior_draft=session.draft or None,
                history=session.history[:-1],
            )
            if session.draft.get("module") and not draft.get("module"):
                draft["module"] = session.draft["module"]
                draft["module_confidence"] = "high"
            session.draft = draft
            session.module_pending = not bool(draft.get("module")) or draft.get("module_confidence") == "low"
            session.status = "awaiting_confirm" if not session.module_pending else "drafting"
            reply = format_draft_reply(draft, image_count=len(session.image_paths))
            session.history.append({"role": "assistant", "content": reply})
            op = session.begin_operation(
                "submit_bug",
                {"draft": dict(draft), "image_paths": list(session.image_paths)},
                card_summary=reply,
            )
            self.reply_text(open_id, "草稿已生成，请在卡片上操作或继续文字补充。")
            self.reply_interactive(
                open_id,
                draft_card(
                    draft,
                    image_count=len(session.image_paths),
                    operation_id=op.operation_id,
                ),
            )
            return

        # 空闲态：Agent 优先理解并调用能力（菜单仍走原卡片流程）
        llm_cfg = resolve_llm(open_id, self.cfg)
        intent = classify_intent(
            text, llm_cfg=llm_cfg, use_llm=True, agent_first=True
        )
        # #region agent log
        _dbg83(
            "H3,H4,H5",
            "server.py:idle:classify",
            "idle intent result",
            {
                "kind": intent.kind,
                "confidence": intent.confidence,
                "slots": {
                    k: intent.slots.get(k)
                    for k in (
                        "bug_id",
                        "keyword",
                        "mine",
                        "days",
                        "start_date",
                        "end_date",
                        "status",
                        "recent",
                    )
                    if k in (intent.slots or {})
                    or intent.slots.get(k) not in (None, "", False)
                },
                "allSlotKeys": sorted((intent.slots or {}).keys()),
            },
        )
        # #endregion
        if intent.kind == "submit_bug":
            if not str(llm_cfg.get("api_key") or "").strip():
                self.reply_text(
                    open_id,
                    "尚未配置 LLM API Key。请在「账号配置」→「修改配置」表单中填写 Key，"
                    "或由管理员在 feishu_bot.json 配置全局兜底。"
                    "\n也可发送明确指令，如「查我近三天提的 bug」。",
                )
                self._send_account_view(open_id)
                return
            session.history.append({"role": "user", "content": text})
            self.reply_text(open_id, "正在整理缺陷草稿，请稍候…")
            draft = parse_bug_from_nl(
                llm_cfg=llm_cfg,
                user_text=text,
                prior_draft=session.draft or None,
                history=session.history[:-1],
            )
            if session.draft.get("module") and not draft.get("module"):
                draft["module"] = session.draft["module"]
                draft["module_confidence"] = "high"
            session.draft = draft
            session.module_pending = not bool(draft.get("module")) or draft.get(
                "module_confidence"
            ) == "low"
            session.status = (
                "awaiting_confirm" if not session.module_pending else "drafting"
            )
            reply = format_draft_reply(draft, image_count=len(session.image_paths))
            session.history.append({"role": "assistant", "content": reply})
            op = session.begin_operation(
                "submit_bug",
                {"draft": dict(draft), "image_paths": list(session.image_paths)},
                card_summary=reply,
            )
            self.reply_text(open_id, "草稿已生成，请在卡片上确认提交，或继续文字补充。")
            self.reply_interactive(
                open_id,
                draft_card(
                    draft,
                    image_count=len(session.image_paths),
                    operation_id=op.operation_id,
                ),
            )
            return

        self.reply_text(open_id, "收到，正在处理…")
        self._route_intent(open_id, intent)
        return

    def _do_submit_draft(
        self, open_id: str, draft: Dict[str, Any], image_paths: List[str]
    ) -> bool:
        self.reply_text(open_id, "正在提交到 TAPD，请稍候…")
        code, result, raw = submit_bug(
            draft, image_paths=list(image_paths), open_id=open_id
        )
        if code == 0 and result.get("bug_url"):
            bug_url = result["bug_url"]
            bug_id = result.get("bug_id") or ""
            msg = f"提交成功。\nBug ID：{bug_id}\n链接：{bug_url}"
            story = result.get("story_link") or {}
            if story.get("linked") or story.get("action") == "linked":
                msg += f"\n已关联需求：{story.get('story_name') or story.get('story_id')}"
            self.reply_text(open_id, msg)
            return True
        err = result.get("error") or raw or f"exit={code}"
        self.reply_text(open_id, f"提交失败：{err}\n可修改后再次提交（表单菜单或回复「确认提交」）。")
        return False

    def _do_submit(self, open_id: str, session: Any, operation_id: str = "") -> None:
        if not session.draft:
            self.reply_text(open_id, "当前没有草稿。请先描述要提交的缺陷。")
            if operation_id:
                session.mark_operation(operation_id, "failed", clear_payload=True)
            return
        if not session.draft.get("module"):
            self.reply_text(open_id, "请先确认模块：回复「前端」或「后端」，或点卡片按钮。")
            if operation_id:
                session.mark_operation(operation_id, "pending")
            return
        if not str(session.draft.get("title") or "").strip():
            self.reply_text(open_id, "草稿缺少标题，请补充问题描述后再确认提交。")
            if operation_id:
                session.mark_operation(operation_id, "pending")
            return
        ok = self._do_submit_draft(open_id, session.draft, list(session.image_paths))
        if operation_id:
            session.mark_operation(
                operation_id,
                "completed" if ok else "failed",
                clear_payload=True,
            )
        if ok:
            self.sessions.reset(open_id)


_APP: Optional[BotApp] = None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8"))

    def _write_json(self, code: int, obj: Dict[str, Any]) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            # 飞书/隧道已超时断开：业务多半已处理完，忽略回写失败
            pass

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/health"):
            self._write_json(200, {"ok": True, "service": "feishu_bug_bot"})
            return
        self._write_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        global _APP
        path = urlparse(self.path).path
        assert _APP is not None
        try:
            body = self._read_json()
            payload = _APP.unwrap_event(body)

            if path in ("/feishu/card", "/card"):
                result = _APP.handle_card_payload(payload)
                self._write_json(200, result)
                return

            if path in ("/feishu/event", "/event", "/"):
                result = _APP.handle_event_payload(payload)
                self._write_json(200, result)
                return

            self._write_json(404, {"error": "not found"})
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # 客户端已断开，勿再尝试写 500
            return
        except Exception as e:
            traceback.print_exc()
            self._write_json(500, {"error": str(e)})


def main() -> None:
    global _APP
    cfg = load_bot_config()
    _APP = BotApp(cfg)
    host = str(cfg.get("listen_host") or "127.0.0.1")
    port = int(cfg.get("listen_port") or 8765)
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"feishu_bug_bot listening on http://{host}:{port}")
    print("event callback path: /feishu/event")
    print("card callback path:  /feishu/card")
    print("health: GET /health")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
        server.shutdown()


if __name__ == "__main__":
    main()
