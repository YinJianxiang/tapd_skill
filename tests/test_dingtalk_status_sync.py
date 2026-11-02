import copy
import importlib.util
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("status_sync", ROOT / "scripts/sync_dingtalk_status.py")
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)
STORY = {"fieldId": "story", "name": "TAPD"}
STATUS = {"fieldId": "state", "name": "状态", "config": {"options": [{"id": "testing", "name": "已提测"}]}}
OWNER = {"fieldId": "owner", "fieldName": "负责人", "type": "user"}
TITLE = {"fieldId": "title", "fieldName": "需求", "type": "text"}
STATUS["type"] = "singleSelect"


def source_config():
    return {"source_user_id": "tester", "status_map": sync.DEFAULT_STATUS_MAP,
            "source": {"base_id": "base", "table_id": "table", "story_field": "TAPD", "status_field": "状态",
                       "owner_field": "负责人", "title_field": "需求"}}


def row(status="已提测", story="100", record_id="rec1"):
    return {"recordId": record_id, "cells": {"story": story, "state": status}}


class FakeTapd:
    def __init__(self):
        self.stories = {
            "100": {"id": "100", "name": "Parent", "parent_id": "0", "status": "open", "workitem_type_id": "root"},
            "200": {"id": "200", "name": "QA", "parent_id": "100", "status": "open", "workitem_type_id": "qa"},
        }
        self.posts = []
        self.timeout_after_write = False
        self.ignore_write = False

    def to_long_id(self, value, workspace):
        return value

    def get_workflows_status_map(self, *args, **kwargs):
        return {"status": 1, "data": {"open": "未开始", "testing": "开始测试", "done": "测试完成", "online": "已上线"}}

    def request(self, method, endpoint, params=None, data=None):
        if endpoint == "workitem_types":
            return {"status": 1, "data": [{"WorkitemType": {"id": "qa", "english_name": "QA"}}]}
        if method == "GET":
            if params.get("id"):
                stories = [self.stories[params["id"]]] if params["id"] in self.stories else []
            elif params.get("name"):
                stories = [s for s in self.stories.values() if params["name"] in s["name"]]
            else:
                stories = [s for s in self.stories.values() if s["parent_id"] == params["parent_id"] and s["workitem_type_id"] == params["workitem_type_id"]]
            return {"status": 1, "data": [{"Story": copy.deepcopy(s)} for s in stories]}
        self.posts.append(copy.deepcopy(data))
        assert os.environ["TAPD_HTTP_RETRIES"] == "1"
        if "id" in data:
            story = self.stories[data["id"]]
            if not self.ignore_write:
                story["status"] = data["status"]
        else:
            story = {**data, "id": "300"}
            if not self.ignore_write:
                self.stories["300"] = story
        if self.timeout_after_write:
            raise TimeoutError()
        return {"status": 1, "data": {"Story": copy.deepcopy(story)}}


class StatusSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = {"workspace_id": "1", "status_map": sync.DEFAULT_STATUS_MAP.copy(),
                       "pending_path": Path(self.temp.name) / "pending.json", "qa_owner": ""}
        self.client = FakeTapd()

    def plan(self, rows):
        return sync.build_plan(self.config, rows, STORY, STATUS, self.client)

    def test_env_configuration_and_requested_default_mapping(self):
        with patch.dict(os.environ, {"TAPD_SYNC_USER": "Tester", "TAPD_WORKSPACE_ID": "1", "DINGTALK_AITABLE_BASE_ID": "base",
                                    "DINGTALK_AITABLE_TABLE_ID": "table", "DINGTALK_AITABLE_STORY_FIELD": "TAPD",
                                    "DINGTALK_AITABLE_STATUS_FIELD": "状态"}, clear=True):
            config = sync.load_config()
        self.assertEqual(config["status_map"], {"已提测": "开始测试", "待上线": "测试完成", "测试完成": "已上线"})
        self.assertEqual(config["source"]["story_field"], "TAPD")

    def test_preview_and_requested_three_mappings(self):
        for source, target in (("已提测", "testing"), ("待上线", "done"), ("测试完成", "online")):
            self.assertEqual(self.plan([row(source)])[0]["to_status"], target)
        self.assertEqual(self.client.posts, [])

    def test_missing_qa_is_created_only_for_submitted_testing(self):
        del self.client.stories["200"]
        plan = self.plan([row()])
        self.assertEqual(plan[0]["action"], "would_create")
        self.assertEqual(self.client.posts, [])
        self.assertEqual(self.plan([row("待上线")])[0]["action"], "skipped_missing_qa")
        self.assertTrue(sync.apply_plan(self.config, plan, self.client))
        self.assertEqual(plan[0]["action"], "created_verified")
        self.assertEqual(self.client.stories["300"]["parent_id"], "100")
        self.assertEqual(self.client.stories["300"]["status"], "testing")
        self.assertEqual(len(self.client.posts), 1)
        self.assertEqual(self.plan([row()])[0]["action"], "skipped_same")

    def test_existing_qa_link_and_parent_link_do_not_duplicate(self):
        plan = self.plan([row(), row(story="200", record_id="rec2")])
        self.assertEqual(plan[1]["action"], "skipped_duplicate")
        self.assertTrue(sync.apply_plan(self.config, plan, self.client))
        self.assertEqual(len(self.client.posts), 1)
        self.assertEqual(plan[0]["action"], "updated_verified")

    def test_conflicts_and_non_qa_children_block_preflight(self):
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            self.plan([row(), row("待上线", record_id="rec2")])
        self.client.stories["200"]["workitem_type_id"] = "frontend"
        with self.assertRaisesRegex(ValueError, "not a QA"):
            self.plan([row(story="200")])
        self.assertEqual(self.client.posts, [])

    def test_multiple_qa_children_block_preflight(self):
        self.client.stories["201"] = {**self.client.stories["200"], "id": "201"}
        with self.assertRaisesRegex(ValueError, "Multiple QA"):
            self.plan([row()])

    def test_unknown_workflow_status_blocks_preflight(self):
        self.config["status_map"]["已提测"] = "not-a-real-status"
        with self.assertRaisesRegex(ValueError, "unknown or ambiguous"):
            self.plan([row()])
        self.assertEqual(self.client.posts, [])

    def test_concurrent_status_change_is_not_overwritten(self):
        plan = self.plan([row()])
        self.client.stories["200"]["status"] = "online"
        self.assertFalse(sync.apply_plan(self.config, plan, self.client))
        self.assertEqual(self.client.posts, [])

    def test_timeout_is_reconciled_without_replaying_write(self):
        plan = self.plan([row()])
        self.client.timeout_after_write = True
        self.assertTrue(sync.apply_plan(self.config, plan, self.client))
        self.assertEqual(len(self.client.posts), 1)
        self.assertEqual(plan[0]["action"], "updated_verified")

    def test_unknown_creation_is_persisted_and_never_automatically_replayed(self):
        del self.client.stories["200"]
        self.client.timeout_after_write = True
        self.client.ignore_write = True
        plan = self.plan([row()])
        self.assertFalse(sync.apply_plan(self.config, plan, self.client))
        self.assertEqual(plan[0]["action"], "creation_unknown")
        next_plan = self.plan([row()])
        self.assertFalse(sync.apply_plan(self.config, next_plan, self.client))
        self.assertEqual(next_plan[0]["action"], "blocked_pending_creation")
        self.assertEqual(len(self.client.posts), 1)

    def test_create_race_uses_existing_child(self):
        del self.client.stories["200"]
        plan = self.plan([row()])
        self.client.stories["201"] = {"id": "201", "name": "QA", "parent_id": "100", "status": "open", "workitem_type_id": "qa"}
        self.assertTrue(sync.apply_plan(self.config, plan, self.client))
        self.assertIn("id", self.client.posts[0])
        self.assertEqual(plan[0]["story_id"], "201")

    def test_source_pagination_selection_options_and_empty_last_page(self):
        config = source_config()
        calls = []
        def query(args):
            calls.append(args)
            if args[0] == "field":
                status = copy.deepcopy(STATUS)
                status["config"]["options"].append({"id": "in-progress", "name": "测试中"})
                return {"ok": True, "data": {"fields": [STORY, status, OWNER, TITLE]}}
            if "--cursor" in args:
                return {"data": {"records": [], "nextCursor": ""}}
            record = row("testing")
            record["cells"]["owner"] = [{"userId": "tester"}]
            return {"data": {"records": [record], "nextCursor": "next"}}
        with patch.object(sync.time, "sleep"):
            records, _, status_field = sync.read_source(config, query)
        self.assertEqual(len(records), 1)
        self.assertEqual(sync.cell_text(records[0]["cells"]["state"], status_field), "已提测")
        self.assertIn("--field-ids", calls[1])
        self.assertEqual(calls[2][-2:], ["--cursor", "next"])
        filters = json.loads(calls[1][calls[1].index("--filters") + 1])
        self.assertEqual(filters["operands"][0]["operands"], ["owner", [{"userId": "tester"}]])
        self.assertIn("in-progress", filters["operands"][1]["operands"][1])

    def test_cursor_loop_rejects_partial_scan(self):
        config = source_config()
        def query(args):
            if args[0] == "field":
                return {"fields": [STORY, STATUS, OWNER, TITLE]}
            return {"records": [], "nextCursor": "loop"}
        with patch.object(sync.time, "sleep"), self.assertRaisesRegex(ValueError, "repeated cursor"):
            sync.read_source(config, query)

    def test_source_rejects_other_users_even_if_query_returns_them(self):
        config = source_config()
        def query(args):
            if args[0] == "field":
                return {"fields": [STORY, STATUS, OWNER, TITLE]}
            record = row()
            record["cells"]["owner"] = [{"userId": "someone-else"}]
            return {"records": [record], "nextCursor": ""}
        with self.assertRaisesRegex(ValueError, "outside the configured user's scope"):
            sync.read_source(config, query)

    def test_title_and_link_cells_match_real_table_protocol(self):
        story_field = {**STORY, "_title_field": "title"}
        record = row(story="https://alidocs.dingtalk.com/i/nodes/some-doc")
        record["cells"]["title"] = "Parent\nhttps://www.tapd.cn/tapd_fe/1/story/detail/100"
        plan = sync.build_plan(self.config, [record], story_field, STATUS, self.client)
        self.assertEqual(plan[0]["story_id"], "200")
        record["cells"]["title"] = "Parent"
        self.assertEqual(sync.build_plan(self.config, [record], story_field, STATUS, self.client)[0]["story_id"], "200")
        record["cells"]["title"] = "No exact matching parent"
        self.assertEqual(sync.build_plan(self.config, [record], story_field, STATUS, self.client)[0]["action"], "skipped_unmatched_story")

    def test_ambiguous_person_and_title_are_not_used(self):
        with patch.object(sync, "run_dws", return_value={"result": []}), self.assertRaisesRegex(ValueError, "one exact"):
            sync.resolve_person("Tester")
        self.client.stories["101"] = {**self.client.stories["100"], "id": "101"}
        with self.assertRaisesRegex(ValueError, "Multiple parent"):
            sync.resolve_source_story(self.client, "1", "", "Parent")

    def test_dingtalk_write_commands_are_explicitly_forbidden(self):
        with patch.object(sync.subprocess, "run") as invoke:
            for command in (["record", "update"], ["record", "create"], ["field", "update"]):
                with self.assertRaisesRegex(ValueError, "read-only DingTalk"):
                    sync.run_dws(command)
            invoke.assert_not_called()

    def config_refresh_fixture(self):
        root = Path(self.temp.name)
        local = root / ".tapd"
        local.mkdir(exist_ok=True)
        template = ROOT / ".tapd/project_config.template.json"
        (local / "project_config.template.json").write_bytes(template.read_bytes())
        (local / "project_config.json").write_text(json.dumps({
            "workspace_id": "1", "link_story": {"story_id": "100"},
            "defaults": {"reporter": "Old"},
        }), encoding="utf-8")
        actual = sync.load_config_initializer()
        self.config["user_name"] = "Tester"
        self.client.stories["200"].update(owner="Tester", status="testing")
        self.client.stories["201"] = {**self.client.stories["200"], "id": "201", "parent_id": "101"}
        def resolve(**kwargs):
            parent = kwargs["story_ref"]["story_id"]
            return {"status": "matched", "payload": {
                "link_story": {"story_id": parent, "story_name": f"Parent {parent}"},
                "defaults": {"reporter": "Tester"},
            }}
        initializer = SimpleNamespace(sync_story_config=Mock(side_effect=resolve),
                                      init_project_config=actual.init_project_config)
        return root, initializer

    def test_testing_source_refreshes_configs_once_per_parent_and_preserves_active_link(self):
        root, initializer = self.config_refresh_fixture()
        plans = [
            {"parent_id": "100", "source_status": "测试中", "action": "would_refresh_config"},
            {"parent_id": "100", "source_status": "测试中", "action": "would_refresh_config"},
            {"parent_id": "101", "source_status": "测试中", "action": "would_refresh_config"},
            {"parent_id": "102", "action": "verification_failed"},
        ]
        with patch.object(sync, "ROOT", root):
            report = sync.refresh_story_configs(self.config, plans, self.client, initializer)
        self.assertTrue(report["ok"])
        self.assertEqual(initializer.sync_story_config.call_count, 2)
        active = json.loads((root / ".tapd/project_config.json").read_text(encoding="utf-8"))
        self.assertEqual(active["link_story"]["story_id"], "100")
        self.assertEqual(active["defaults"]["reporter"], "Tester")
        self.assertTrue((root / ".tapd/story_configs/1/101.json").is_file())
        self.assertFalse((root / ".tapd/story_configs/1/102.json").exists())

    def test_config_refresh_failure_retries_without_replaying_tapd_write(self):
        root, initializer = self.config_refresh_fixture()
        original = initializer.sync_story_config.side_effect
        initializer.sync_story_config.side_effect = RuntimeError("unavailable")
        with patch.object(sync, "ROOT", root):
            report = sync.refresh_story_configs(self.config, [{"parent_id": "100", "source_status": "测试中", "action": "would_refresh_config"}], self.client, initializer)
            self.assertFalse(report["ok"])
            initializer.sync_story_config.side_effect = original
            report = sync.refresh_story_configs(self.config, [{"parent_id": "100", "source_status": "测试中", "action": "would_refresh_config"}], self.client, initializer)
        self.assertTrue(report["ok"])

    def test_config_refresh_follows_source_testing_even_when_tapd_status_differs(self):
        root, initializer = self.config_refresh_fixture()
        self.client.stories["200"].update(owner="Other Tester", status="online")
        with patch.object(sync, "ROOT", root):
            report = sync.refresh_story_configs(self.config, [{
                "parent_id": "100", "source_status": "测试中", "action": "would_refresh_config",
            }], self.client, initializer)
        self.assertTrue(report["ok"])
        initializer.sync_story_config.assert_called_once()
        self.assertEqual(self.client.posts, [])

    def test_pending_refresh_is_dropped_when_source_is_no_longer_testing(self):
        root, initializer = self.config_refresh_fixture()
        original = initializer.sync_story_config.side_effect
        initializer.sync_story_config.side_effect = RuntimeError("unavailable")
        with patch.object(sync, "ROOT", root):
            sync.refresh_story_configs(self.config, [{
                "parent_id": "100", "source_status": "测试中", "action": "would_refresh_config",
            }], self.client, initializer)
            initializer.sync_story_config.side_effect = original
            initializer.sync_story_config.reset_mock()
            report = sync.refresh_story_configs(self.config, [], self.client, initializer)
        self.assertTrue(report["ok"])
        initializer.sync_story_config.assert_not_called()
        self.assertEqual(report["results"][0]["action"], "skipped_not_source_testing")
        self.assertEqual(self.client.posts, [])

    def test_source_testing_directly_plans_config_refresh_without_status_api_or_write(self):
        with patch.object(self.client, "get_workflows_status_map", side_effect=AssertionError("No status lookup needed")):
            plan = self.plan([row("测试中")])
        self.assertEqual(plan[0]["action"], "would_refresh_config")
        self.assertEqual(plan[0]["parent_id"], "100")
        self.assertTrue(sync.apply_plan(self.config, plan, self.client))
        self.assertEqual(self.client.posts, [])

    def test_unchanged_and_unverified_stories_do_not_trigger_config_refresh(self):
        root, initializer = self.config_refresh_fixture()
        plans = [{"parent_id": "100", "action": "skipped_same"},
                 {"parent_id": "101", "action": "would_update"}]
        with patch.object(sync, "ROOT", root):
            report = sync.refresh_story_configs(self.config, plans, self.client, initializer)
        initializer.sync_story_config.assert_not_called()
        self.assertTrue(report["ok"])


if __name__ == "__main__":
    unittest.main()
