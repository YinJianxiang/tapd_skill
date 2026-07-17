"""按 open_id 维护内存会话：草稿 / 待确认操作 / 图片路径 / 查询。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class PendingOperation:
    """写操作统一待办：关闭 / 提单 / 改配置。"""

    operation_id: str
    kind: str  # close_bug | submit_bug | config_edit
    status: str = "pending"  # pending | processing | completed | cancelled | failed
    payload: Dict[str, Any] = field(default_factory=dict)
    card_summary: str = ""
    result_message: str = ""


@dataclass
class Session:
    open_id: str
    status: str = "idle"
    # idle | drafting | awaiting_confirm
    # | awaiting_query_bug | awaiting_query_story
    # | awaiting_close_confirm | awaiting_config_confirm | awaiting_config_value
    draft: Dict[str, Any] = field(default_factory=dict)
    image_paths: List[str] = field(default_factory=list)
    history: List[Dict[str, str]] = field(default_factory=list)  # {role, content}
    module_pending: bool = False
    pending_config_path: Optional[str] = None
    pending_close_ids: List[str] = field(default_factory=list)
    pending_close_rows: List[Dict[str, str]] = field(default_factory=list)
    pending_config_changes: Dict[str, Any] = field(default_factory=dict)
    pending_operation: Optional[PendingOperation] = None
    # 群聊回复目标：有值时文本/卡片发到群，否则私聊 open_id
    reply_chat_id: Optional[str] = None

    def clear(self) -> None:
        self.status = "idle"
        self.draft = {}
        self.image_paths = []
        self.history = []
        self.module_pending = False
        self.pending_config_path = None
        self.pending_close_ids = []
        self.pending_close_rows = []
        self.pending_config_changes = {}
        self.pending_operation = None
        self.reply_chat_id = None

    def clear_pending_ops(self) -> None:
        """清查询/关闭/配置待办，保留草稿相关字段。"""
        self.pending_config_path = None
        self.pending_close_ids = []
        self.pending_close_rows = []
        self.pending_config_changes = {}
        # 保留终态 operation 供拒绝重复回调；仅在 pending/processing 时清空可执行 payload
        if self.pending_operation and self.pending_operation.status in (
            "pending",
            "processing",
        ):
            self.pending_operation = None
        if self.status in (
            "awaiting_query_bug",
            "awaiting_query_story",
            "awaiting_close_confirm",
            "awaiting_config_confirm",
            "awaiting_config_value",
        ):
            self.status = "idle"

    def begin_operation(
        self,
        kind: str,
        payload: Dict[str, Any],
        *,
        card_summary: str = "",
    ) -> PendingOperation:
        op = PendingOperation(
            operation_id=uuid.uuid4().hex[:16],
            kind=kind,
            status="pending",
            payload=dict(payload),
            card_summary=card_summary,
        )
        self.pending_operation = op
        return op

    def get_operation(self, operation_id: str) -> Optional[PendingOperation]:
        op = self.pending_operation
        if not op:
            return None
        if op.operation_id != str(operation_id or "").strip():
            return None
        return op

    def mark_operation(
        self,
        operation_id: str,
        status: str,
        *,
        result_message: str = "",
        clear_payload: bool = False,
    ) -> Optional[PendingOperation]:
        op = self.get_operation(operation_id)
        if not op:
            return None
        op.status = status
        if result_message:
            op.result_message = result_message
        if clear_payload:
            op.payload = {}
        return op


class SessionStore:
    def __init__(self) -> None:
        self._sessions: Dict[str, Session] = {}

    def get(self, open_id: str) -> Session:
        if open_id not in self._sessions:
            self._sessions[open_id] = Session(open_id=open_id)
        return self._sessions[open_id]

    def reset(self, open_id: str) -> None:
        if open_id in self._sessions:
            self._sessions[open_id].clear()
        else:
            self._sessions[open_id] = Session(open_id=open_id)
