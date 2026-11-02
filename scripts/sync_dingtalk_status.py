"""Read DingTalk AITable records and synchronize TAPD child Story statuses."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATUS_MAP = {"已提测": "开始测试", "待上线": "测试完成", "测试完成": "已上线"}


class SyncError(ValueError):
    """A local diagnostic safe to display without remote response payloads."""


@contextmanager
def sync_lock(enabled: bool):
    if not enabled:
        yield
        return
    path = ROOT / ".tapd/dingtalk_status_sync.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        handle = path.open("x", encoding="utf-8")
    except FileExistsError:
        raise SyncError("Another sync is active. If it crashed, reconcile pending creations before removing .tapd/dingtalk_status_sync.lock") from None
    try:
        with handle:
            handle.write(str(os.getpid()))
        yield
    finally:
        path.unlink()


def load_config() -> dict:
    def required(name):
        value = os.environ.get(name, "").strip()
        if not value:
            raise SyncError(f"Missing environment variable: {name}")
        return value

    def local_json(name):
        path = ROOT / ".tapd" / name
        return json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else {}

    project = local_json("project_config.json")
    auth = local_json("tapd_env.json")
    user = (os.environ.get("TAPD_SYNC_USER") or os.environ.get("CURRENT_USER_NICK")
            or auth.get("current_user_nick") or project.get("defaults", {}).get("reporter") or "").strip()
    if not user:
        raise SyncError("Configure CURRENT_USER_NICK, tapd_env.current_user_nick or defaults.reporter")
    workspace = os.environ.get("TAPD_WORKSPACE_ID", "").strip()
    if not workspace:
        workspace = str(project.get("workspace_id") or "")
    if not workspace.isdigit():
        raise SyncError("Set TAPD_WORKSPACE_ID or workspace_id in .tapd/project_config.json")
    config = {
        "workspace_id": workspace,
        "source": {
            "base_id": required("DINGTALK_AITABLE_BASE_ID"),
            "table_id": required("DINGTALK_AITABLE_TABLE_ID"),
            "story_field": os.environ.get("DINGTALK_AITABLE_STORY_FIELD", "需求链接"),
            "status_field": os.environ.get("DINGTALK_AITABLE_STATUS_FIELD", "任务进度"),
            "owner_field": os.environ.get("DINGTALK_AITABLE_OWNER_FIELD", "负责人"),
            "title_field": os.environ.get("DINGTALK_AITABLE_TITLE_FIELD", "需求"),
        },
        "status_map": json.loads(os.environ.get("TAPD_STATUS_SYNC_MAP", json.dumps(DEFAULT_STATUS_MAP))),
        "qa_type_id": os.environ.get("TAPD_QA_TYPE_ID", "").strip(),
        "user_name": user,
        "qa_owner": os.environ.get("TAPD_QA_OWNER", user).strip(),
        "pending_path": ROOT / ".tapd/dingtalk_status_sync_pending.json",
    }
    if os.environ.get("DINGTALK_AITABLE_FILTERS_JSON"):
        config["source"]["filters"] = json.loads(os.environ["DINGTALK_AITABLE_FILTERS_JSON"])
    return config


def dws_command() -> list[str]:
    executable = shutil.which("dws")
    if not executable:
        raise ValueError("DWS CLI is not installed or is missing from PATH")
    path = Path(executable)
    if path.suffix.lower() in (".cmd", ".bat", ".ps1") or (os.name == "nt" and path.suffix.lower() != ".exe"):
        # Invoke the npm entry point directly so JSON arguments never pass a shell.
        entry = path.parent / "node_modules/dingtalk-workspace-cli/bin/dws.js"
        node = shutil.which("node")
        if not node or not entry.is_file():
            raise ValueError("Cannot resolve the DWS npm entry point; install DWS with its Node runtime")
        return [node, str(entry)]
    return [executable]


def run_dws(arguments: list[str], service: str = "aitable") -> dict:
    allowed = (service == "aitable" and arguments[:2] in (["field", "get"], ["record", "query"]))
    allowed = allowed or (service == "aisearch" and arguments[:1] == ["person"])
    if not allowed:
        raise SyncError("This synchronization only permits read-only DingTalk commands")
    result = subprocess.run(
        [*dws_command(), service, *arguments, "--format", "json"],
        capture_output=True, encoding="utf-8", timeout=120,
    )
    try:
        value = json.loads(result.stdout or result.stderr)
    except (ValueError, TypeError):
        raise ValueError("DWS did not return JSON; check CLI login and permissions") from None
    if not isinstance(value, dict):
        raise ValueError("Unexpected DWS result envelope")
    if (result.returncode or value.get("ok") is False
            or value.get("success") is False
            or value.get("outcome") in ("partial_failure", "partial_success", "failure", "error")
            or value.get("status") in ("error", "failed", "partial_success")):
        # Do not echo arbitrary CLI errors: they can contain credentials or records.
        raise ValueError("DWS query failed or returned incomplete results; no TAPD writes performed")
    return value


def resolve_person(name: str) -> str:
    response = run_dws(["person", "--query", name, "--dimension", "name"], service="aisearch")
    people = response.get("result")
    if not isinstance(people, list):
        raise SyncError("Personnel search did not return candidates")
    exact = [p for p in people if p.get("_searchEvidence", {}).get("exactAliasContainsQuery") is True
             and name in p.get("_searchEvidence", {}).get("aliases", [])]
    if len(exact) != 1 or not exact[0].get("userId"):
        raise SyncError("Configured user must resolve to one exact DingTalk person")
    return str(exact[0]["userId"])


def payload_with(value: dict, key: str) -> dict:
    for _ in range(4):
        if key in value:
            return value
        value = value.get("data", value.get("result"))
        if not isinstance(value, dict):
            break
    raise ValueError(f"DWS response is missing {key}")


def resolve_field(fields: list[dict], reference: str) -> dict:
    matches = [f for f in fields if reference in (f.get("fieldId"), f.get("name"), f.get("fieldName"))]
    if len(matches) != 1:
        raise ValueError(f"Field must resolve uniquely: {reference}")
    return matches[0]


def cell_text(value: Any, field: dict) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        if len(value) != 1:
            if not value:
                return ""
            raise ValueError("A mapping cell must contain exactly one value")
        return cell_text(value[0], field)
    if isinstance(value, dict):
        for key in ("value", "url", "link", "text", "name", "id"):
            if key in value:
                return cell_text(value[key], field)
        raise ValueError("Unsupported cell structure; inspect the actual table field type")
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("Mapping cells must contain text, a URL, an ID, or a single option")
    text = str(value).strip()
    for option in field.get("config", {}).get("options", []):
        if str(option.get("id", "")) == text:
            return str(option["name"]).strip()
    return text


def read_source(config: dict, query=run_dws) -> tuple[list[dict], dict, dict]:
    source = config["source"]
    common = ["--base-id", source["base_id"], "--table-id", source["table_id"]]
    fields = payload_with(query(["field", "get", *common]), "fields")["fields"]
    if not isinstance(fields, list) or not all(isinstance(f, dict) for f in fields):
        raise ValueError("Invalid field catalog")
    story_field = resolve_field(fields, source["story_field"])
    status_field = resolve_field(fields, source["status_field"])
    owner_field = resolve_field(fields, source["owner_field"])
    title_field = resolve_field(fields, source["title_field"])
    if owner_field.get("type") != "user":
        raise SyncError("The owner field must be a DingTalk user field")
    user_id = config.get("source_user_id")
    if not user_id:
        raise SyncError("Configured user must be resolved before querying records")
    story_field = {**story_field, "_title_field": title_field["fieldId"]}
    field_ids = [story_field["fieldId"], status_field["fieldId"], owner_field["fieldId"], title_field["fieldId"]]
    if len(set(field_ids)) != 4:
        raise SyncError("Story link, title, owner and status must use different fields")
    arguments = ["record", "query", *common, "--field-ids", ",".join(field_ids), "--limit", "100"]
    options = status_field.get("config", {}).get("options", [])
    option_ids = [o["id"] for o in options if o.get("name") in {*config["status_map"], "测试中"}]
    if status_field.get("type") != "singleSelect" or not option_ids:
        raise SyncError("Status must be a singleSelect field with configured source options")
    conditions = [
        {"operator": "any_of", "operands": [owner_field["fieldId"], [{"userId": user_id}]]},
        {"operator": "any_of", "operands": [status_field["fieldId"], option_ids]},
    ]
    if source.get("filters"):
        extra = source["filters"]
        if not isinstance(extra, dict) or extra.get("operator") != "and" or not isinstance(extra.get("operands"), list):
            raise SyncError("Extra filters must be a flat AND group; user scope is always retained")
        if any(not isinstance(c, dict) or c.get("operator") in ("and", "or") for c in extra["operands"]):
            raise SyncError("Nested filter groups are not supported by DWS record query")
        conditions.extend(extra["operands"])
    filters = {"operator": "and", "operands": conditions}
    arguments += ["--filters", json.dumps(filters, ensure_ascii=False)]
    records, seen, cursors = [], set(), set()
    cursor = ""
    # Bound the initial integration to 1000 records; partial scans never write.
    for page in range(10):
        if page:
            time.sleep(0.2)
        args = arguments + (["--cursor", cursor] if cursor else [])
        data = payload_with(query(args), "records")
        rows = data["records"]
        if not isinstance(rows, list) or data.get("complete") is False:
            raise ValueError("Incomplete or malformed record result")
        for row in rows:
            if not isinstance(row, dict) or not row.get("recordId") or row["recordId"] in seen:
                raise ValueError("Invalid or duplicate record; discard this scan")
            seen.add(row["recordId"])
            owners = row.get("cells", {}).get(owner_field["fieldId"])
            if not isinstance(owners, list) or not any(isinstance(p, dict) and str(p.get("userId")) == user_id for p in owners):
                raise SyncError("Source returned a record outside the configured user's scope")
            records.append(row)
        cursor = data.get("nextCursor")
        if cursor in (None, ""):
            return records, story_field, status_field
        if not isinstance(cursor, str) or cursor in cursors:
            raise ValueError("Invalid or repeated cursor; discard this scan")
        cursors.add(cursor)
    raise ValueError("Source exceeds 10 pages; narrow source.filters before synchronizing")


def story_id(text: str, workspace: str, client) -> str:
    if text.isdigit():
        return str(client.to_long_id(text, int(workspace)))
    url = urlparse(text)
    match = re.fullmatch(r"/(\d+)/(?:prong/)?stories/view/(\d+)/?", url.path)
    if not match:
        match = re.fullmatch(r"/tapd_fe/(\d+)/story/detail/(\d+)/?", url.path)
    if url.scheme not in ("http", "https") or url.hostname != "www.tapd.cn" or not match:
        raise ValueError("Expected a TAPD Story ID or a full www.tapd.cn Story link")
    if match[1] != workspace:
        raise ValueError("Story URL belongs to a different TAPD workspace")
    return str(client.to_long_id(match[2], int(workspace)))


def resolve_source_story(client, workspace: str, link: str, title: str) -> dict | None:
    if link.isdigit():
        return get_story(client, workspace, story_id(link, workspace, client))
    references = []
    for text in (link, title):
        for url in re.findall(r"https?://www\.tapd\.cn/[^\s<>\"'）)]+", text):
            references.append(story_id(url.rstrip(".,，。"), workspace, client))
    references = list(dict.fromkeys(references))
    if len(references) > 1:
        raise SyncError("A record contains multiple TAPD Story links")
    if references:
        return get_story(client, workspace, references[0])
    if not title:
        return None
    data = tapd_data(client.request("GET", "stories", params={
        "workspace_id": workspace, "entity_type": "stories", "name": title,
        "limit": 100, "fields": "id,name,status,parent_id,workitem_type_id,iteration_id",
    }))
    if not isinstance(data, list) or len(data) >= 100:
        raise SyncError("Title lookup is invalid or incomplete; provide an explicit TAPD link")
    rows = [r.get("Story", r) for r in data if isinstance(r, dict)]
    exact = [r for r in rows if str(r.get("name", "")).strip() == title and str(r.get("parent_id") or "0") == "0"]
    if len(exact) > 1:
        raise SyncError("Multiple parent Stories share this title; provide an explicit TAPD link")
    return exact[0] if exact else None


def tapd_data(response: dict):
    if not isinstance(response, dict) or response.get("status") != 1:
        raise ValueError("TAPD request failed")
    return response.get("data")


def get_story(client, workspace: str, sid: str) -> dict:
    data = tapd_data(client.request("GET", "stories", params={
        "workspace_id": workspace, "id": sid, "entity_type": "stories", "limit": 2,
        "fields": "id,name,status,parent_id,workitem_type_id,iteration_id,owner",
    }))
    if not isinstance(data, list):
        raise ValueError("Unexpected TAPD Story response")
    rows = [r.get("Story", r) for r in data if isinstance(r, dict)]
    rows = [r for r in rows if str(r.get("id")) == sid]
    if len(rows) != 1:
        raise ValueError(f"TAPD Story not uniquely found: {sid}")
    return rows[0]


def qa_type_id(config: dict, client) -> str:
    data = tapd_data(client.request("GET", "workitem_types", params={
        "workspace_id": config["workspace_id"], "limit": 200,
    }))
    if not isinstance(data, list) or len(data) >= 200:
        raise SyncError("Work item type catalog is invalid or incomplete")
    types = [row.get("WorkitemType", row) for row in data if isinstance(row, dict)]
    candidates = [str(t["id"]) for t in types if
                  str(t.get("english_name", "")).upper() == "QA" or t.get("name") == "测试子需求"]
    configured = config.get("qa_type_id")
    if configured:
        if configured not in candidates:
            raise SyncError("TAPD_QA_TYPE_ID must refer to a verified QA work item type")
        return configured
    if len(candidates) != 1:
        raise SyncError("QA work item type is ambiguous; set TAPD_QA_TYPE_ID")
    return candidates[0]


def find_qa_child(client, workspace: str, parent_id: str, type_id: str) -> dict | None:
    data = tapd_data(client.request("GET", "stories", params={
        "workspace_id": workspace, "entity_type": "stories", "parent_id": parent_id,
        "workitem_type_id": type_id, "limit": 2,
        "fields": "id,name,status,parent_id,workitem_type_id,iteration_id",
    }))
    if not isinstance(data, list) or len(data) > 1:
        raise SyncError(f"Multiple QA children or invalid response for parent: {parent_id}")
    if not data:
        return None
    child = data[0].get("Story", data[0])
    if str(child.get("parent_id")) != parent_id or str(child.get("workitem_type_id")) != type_id:
        raise SyncError("Child query returned a Story outside the requested QA scope")
    return child


def build_plan(config: dict, records: list[dict], story_field: dict, status_field: dict, client) -> list[dict]:
    workspace = str(config["workspace_id"])
    mapping = config["status_map"]
    if not isinstance(mapping, dict) or not mapping:
        raise ValueError("status_map must explicitly map source statuses to TAPD statuses")
    type_id, states = None, None
    plans, targets = [], {}
    for record in records:
        cells = record.get("cells")
        if not isinstance(cells, dict):
            raise ValueError("Record is missing cells")
        source_status = cell_text(cells.get(status_field["fieldId"]), status_field)
        source_story = cell_text(cells.get(story_field["fieldId"]), story_field)
        title = cell_text(cells.get(story_field.get("_title_field")), {})
        item = {"record_id": record["recordId"], "source_status": source_status}
        if not source_status or (source_status not in mapping and source_status != "测试中"):
            plans.append({**item, "action": "skipped_unmapped"})
            continue
        linked = resolve_source_story(client, workspace, source_story, title)
        if linked is None:
            plans.append({**item, "action": "skipped_unmatched_story"})
            continue
        source_id = str(linked["id"])
        if source_status == "测试中":
            parent_id = str(linked.get("parent_id") or "0")
            if parent_id == "0":
                parent_id = source_id
            key = f"config:{parent_id}"
            if key not in targets:
                targets[key] = "refresh_config"
                plans.append({**item, "parent_id": parent_id, "action": "would_refresh_config"})
            else:
                plans.append({**item, "parent_id": parent_id, "action": "skipped_duplicate"})
            continue
        if type_id is None:
            type_id = qa_type_id(config, client)
            states = tapd_data(client.get_workflows_status_map(int(workspace), type_id, system="story"))
        parent = None
        if str(linked.get("parent_id") or "0") == "0":
            parent = linked
            story = find_qa_child(client, workspace, source_id, type_id)
        else:
            story = linked
            if str(story.get("workitem_type_id")) != type_id:
                raise SyncError(f"Linked child is not a QA Story: {source_id}")
        if story is None and source_status != "已提测":
            plans.append({**item, "parent_id": source_id, "action": "skipped_missing_qa"})
            continue
        sid = str(story["id"]) if story else ""
        raw = mapping[source_status]
        if not isinstance(states, dict) or not isinstance(raw, str) or not raw:
            raise ValueError("Invalid workflow or status mapping")
        candidates = [k for k, label in states.items() if raw in (k, label)]
        if len(candidates) != 1:
            raise SyncError(f"Target status is unknown or ambiguous for QA workflow: {raw}")
        target = candidates[0]
        # A parent link and its explicit QA child link represent the same target.
        target_id = sid or f"parent:{source_id}"
        if target_id in targets:
            if targets[target_id] != target:
                raise SyncError(f"Conflicting source statuses for QA target: {target_id}")
            plans.append({**item, "story_id": sid, "action": "skipped_duplicate"})
            continue
        targets[target_id] = target
        if story is None:
            plans.append({**item, "action": "would_create", "parent_id": source_id,
                          "name": parent["name"], "iteration_id": parent.get("iteration_id"),
                          "workitem_type_id": type_id, "to_status": target})
            continue
        before = str(story.get("status") or "")
        if not before:
            raise ValueError(f"Story has no current status: {sid}")
        plans.append({**item, "story_id": sid, "from_status": before,
                      "to_status": target, "workitem_type_id": type_id,
                      "parent_id": str(story["parent_id"]),
                      "action": "skipped_same" if before == target else "would_update"})
    return plans


def post_once(client, data: dict):
    old_retries = os.environ.get("TAPD_HTTP_RETRIES")
    os.environ["TAPD_HTTP_RETRIES"] = "1"
    try:
        return tapd_data(client.request("POST", "stories", data=data))
    finally:
        if old_retries is None:
            os.environ.pop("TAPD_HTTP_RETRIES", None)
        else:
            os.environ["TAPD_HTTP_RETRIES"] = old_retries


def create_qa(config: dict, item: dict, client) -> bool:
    workspace, parent_id = str(config["workspace_id"]), item["parent_id"]
    # Recheck existence immediately before a non-idempotent create.
    child = find_qa_child(client, workspace, parent_id, item["workitem_type_id"])
    if child:
        item.update(story_id=str(child["id"]), from_status=str(child["status"]), action="would_update")
        return True
    path = Path(config["pending_path"])
    pending = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    key = f"{workspace}:{parent_id}"
    if key in pending:
        item["action"] = "blocked_pending_creation"
        return False
    # Persist intent before POST so timeout or process termination cannot trigger
    # an automatic duplicate create on the next run.
    path.parent.mkdir(parents=True, exist_ok=True)
    pending[key] = {"parent_id": parent_id, "workitem_type_id": item["workitem_type_id"]}
    path.write_text(json.dumps(pending), encoding="utf-8")
    data = {"workspace_id": workspace, "parent_id": parent_id, "name": item["name"],
            "workitem_type_id": item["workitem_type_id"], "status": item["to_status"]}
    if config.get("qa_owner"):
        data["owner"] = config["qa_owner"]
    if item.get("iteration_id") and str(item["iteration_id"]) != "0":
        data["iteration_id"] = item["iteration_id"]
    try:
        response = post_once(client, data)
        created = response.get("Story", response) if isinstance(response, dict) else {}
        sid = str(created.get("id") or "")
        # Even after an unknown POST, a fresh child query can establish existence.
        child = get_story(client, workspace, sid) if sid else find_qa_child(client, workspace, parent_id, item["workitem_type_id"])
    except Exception:
        try:
            child = find_qa_child(client, workspace, parent_id, item["workitem_type_id"])
        except Exception:
            child = None
    if not child:
        item["action"] = "creation_unknown"
        return False
    if str(child.get("parent_id")) != parent_id or str(child.get("workitem_type_id")) != item["workitem_type_id"]:
        item["action"] = "creation_verification_failed"
        return False
    item["story_id"] = str(child["id"])
    pending.pop(key)
    path.write_text(json.dumps(pending), encoding="utf-8")
    if str(child.get("status")) == item["to_status"]:
        item["action"] = "created_verified"
    else:
        item.update(from_status=str(child.get("status")), action="would_update")
    return True


def apply_plan(config: dict, plans: list[dict], client) -> bool:
    workspace = str(config["workspace_id"])
    for item in plans:
        if item["action"] == "would_create":
            try:
                if not create_qa(config, item, client):
                    return False
            except Exception:
                item["action"] = "creation_blocked"
                return False
        if item["action"] != "would_update":
            continue
        sid = item["story_id"]
        try:
            fresh = get_story(client, workspace, sid)
            if (str(fresh.get("workitem_type_id")) != item["workitem_type_id"]
                    or str(fresh.get("parent_id")) != item["parent_id"]):
                raise ValueError("Story type or parent changed after preflight")
            current = str(fresh.get("status"))
            if current == item["to_status"]:
                item["action"] = "skipped_same"
                continue
            if current != item["from_status"]:
                raise ValueError("Story status changed after preflight")
        except Exception:
            item["action"] = "blocked_changed_or_unreadable"
            return False
        try:
            # The shared client retries transport errors by default. A write with
            # an unknown result must instead be reconciled by an independent GET.
            post_once(client, {"workspace_id": workspace, "id": sid, "status": item["to_status"]})
        except Exception:
            item["action"] = "write_unknown"
        else:
            item["action"] = "verification_failed"
        try:
            latest = get_story(client, workspace, sid)
            item["observed_status"] = str(latest.get("status"))
            if item["observed_status"] == item["to_status"]:
                item["action"] = "updated_verified"
                continue
        except Exception:
            pass
        return False  # Preserve partial results and stop; never replay a write.
    return True


def load_client():
    path = ROOT / "skills/tapd-plus/scripts/tapd_client_stdlib.py"
    spec = importlib.util.spec_from_file_location("tapd_sync_client", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_config_initializer():
    path = ROOT / "skills/tapd-config-init/scripts/init_tapd_config.py"
    spec = importlib.util.spec_from_file_location("tapd_sync_config_init", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_local_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def refresh_story_configs(config: dict, plans: list[dict], client, initializer=None) -> dict:
    """Refresh configs directly for this user's source records marked testing."""
    workspace = str(config["workspace_id"])
    local_dir = ROOT / ".tapd"
    queue_path = local_dir / "dingtalk_config_refresh_pending.json"
    pending = json.loads(queue_path.read_text(encoding="utf-8")) if queue_path.exists() else {}
    eligible = set()
    for item in plans:
        if item.get("source_status") == "测试中" and item.get("action") == "would_refresh_config":
            parent = str(item["parent_id"])
            if not parent.isdigit() or not workspace.isdigit():
                raise SyncError("Invalid parent or workspace ID for config refresh")
            key = f"{workspace}:{parent}"
            eligible.add(key)
            pending[key] = {
                "workspace_id": workspace, "parent_id": parent,
            }
    if not pending:
        return {"ok": True, "results": []}
    # Save before refreshing. A later run retries the refresh without replaying
    # the successful Story creation or status update.
    write_local_json(queue_path, pending)
    initializer = initializer or load_config_initializer()
    active_path = local_dir / "project_config.json"
    active = json.loads(active_path.read_text(encoding="utf-8-sig")) if active_path.exists() else {}
    active_workspace = str(active.get("workspace_id") or "")
    active_parent = str(active.get("link_story", {}).get("story_id") or "")
    if active_parent and active_workspace.isdigit():
        active_parent = str(client.to_long_id(active_parent, int(active_workspace)))
    template = local_dir / "project_config.template.json"
    results = []
    for key, entry in list(pending.items()):
        if str(entry.get("workspace_id")) != workspace:
            continue
        parent = str(entry.get("parent_id") or "")
        item = {"parent_id": parent, "action": "config_refresh_failed"}
        try:
            if not parent.isdigit():
                raise SyncError("Invalid queued parent ID")
            if key not in eligible:
                item["action"] = "skipped_not_source_testing"
                pending.pop(key)
                write_local_json(queue_path, pending)
                results.append(item)
                continue
            target = local_dir / "story_configs" / workspace / f"{parent}.json"
            response = initializer.sync_story_config(
                target_path=target, workspace_id=workspace, story_ref={"story_id": parent},
            )
            if response.get("status") != "matched" or not isinstance(response.get("payload"), dict):
                raise SyncError("tapd-config-init could not uniquely resolve the parent")
            payload = {**response["payload"], "workspace_id": workspace}
            if str(payload.get("link_story", {}).get("story_id")) != parent:
                raise SyncError("tapd-config-init returned a different parent")
            initializer.init_project_config(target, template, payload)
            item["active_config_updated"] = False
            if active_workspace == workspace and active_parent == parent:
                initializer.init_project_config(active_path, template, payload)
                item["active_config_updated"] = True
            item["action"] = "config_refreshed"
            item["config_path"] = str(target.relative_to(ROOT))
            pending.pop(key)
            write_local_json(queue_path, pending)
        except Exception:
            # Keep the queued refresh and the original verified TAPD result.
            item["action"] = "config_refresh_failed"
        results.append(item)
    return {"ok": all(r["action"] != "config_refresh_failed" for r in results), "results": results}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write and independently verify changes; default is preview")
    parser.add_argument("--source-only", action="store_true", help="Inspect this user's source records without accessing TAPD")
    args = parser.parse_args()
    try:
        config = load_config()
        if args.apply and args.source_only:
            raise SyncError("--source-only cannot be combined with --apply")
        with sync_lock(args.apply):
            config["source_user_id"] = resolve_person(config["user_name"])
            records, story_field, status_field = read_source(config)
            if args.source_only:
                items = [{"record_id": r["recordId"],
                          "title": cell_text(r["cells"].get(story_field["_title_field"]), {}),
                          "status": cell_text(r["cells"].get(status_field["fieldId"]), status_field)} for r in records]
                print(json.dumps({"ok": True, "source_only": True, "user": config["user_name"], "count": len(items), "records": items}, ensure_ascii=False))
                return 0
            client = load_client()
            plans = build_plan(config, records, story_field, status_field, client)
            ok = apply_plan(config, plans, client) if args.apply else True
            try:
                config_refresh = refresh_story_configs(config, plans, client) if args.apply else {"ok": True, "action": "skipped_preview"}
            except Exception:
                config_refresh = {"ok": False, "action": "config_refresh_failed"}
            ok = ok and config_refresh["ok"]
        print(json.dumps({"ok": ok, "user": config["user_name"], "dry_run": not args.apply,
                          "count": len(plans), "results": plans, "config_refresh": config_refresh}, ensure_ascii=False))
        return 0 if ok else 1
    except Exception as exc:
        # Avoid exposing remote error payloads or local credentials in stdout.
        print(json.dumps({"ok": False, "error_type": type(exc).__name__,
                          "message": str(exc) if isinstance(exc, SyncError) else "Sync stopped. Check table fields, scope, mapping, CLI login and TAPD permissions."}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
