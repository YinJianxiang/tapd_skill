---
name: tapd-qa-status
description: >-
  查询或更新 TAPD 测试子需求状态（未开始/已提测/测试中/测试通过/已上线等）。
  支持按处理人列出未上线、按 ID/标题改状态。当用户问测试子任务是否上线、
  查我的测试子需求状态、把测试子需求标成已上线/测试中时使用。
  需 .tapd/tapd_env.json 与 project_config.json。
---

# TAPD 测试子需求状态

针对工作项类型 **测试子需求**（`english_name=QA`）查询状态与更新 `status`。不是 TAPD Task，而是挂在父需求下的子 Story。

## 何时使用

- 查我负责的测试子需求 / 测试子任务是否有未上线
- 查看某条测试子需求当前状态
- 把测试子需求改为：未开始、已提测、测试中、测试通过、已上线、需求作废
- 需要先看项目测试工作流有哪些状态（`status-map`）

## 状态说明

状态以项目工作流为准，常见映射（以 `workflows/status_map` 为准）：

| 英文 key | 中文 |
|----------|------|
| `open` | 未开始 |
| `status_15` | 已提测 |
| `status_16` | 测试中 |
| `status_17` | 测试通过 |
| `status_19` | 已上线 |
| `status_20` | 需求作废 |

脚本接受 **中文名或英文 key**。结束状态通常含「已上线」「需求作废」。

`--not-online`：排除「已上线」；默认同时排除「需求作废」（可用 `--include-cancelled` 保留）。

## 标准流程

### 查询（list）

1. 解析 `workspace_id`，定位测试子需求 `workitem_type_id`。
2. 拉取状态映射。
3. 按 `owner` / `parent_id` / `story_id` / `status` / `not_online` 过滤。
4. 返回列表：`id`、`name`、`status`、`status_label`、`url` 等。

默认 `owner`：`CURRENT_USER_NICK` → `tapd_env.current_user_nick` → `defaults.reporter`。查全部时在 payload 写 `"owner": ""`。

### 更新（update）

1. 用 `story_id`（推荐）或唯一 `story_name` / 唯一父需求下测试子需求定位。
2. 将目标状态解析为英文 key。
3. `--dry-run` 预览；确认后去掉 dry-run，`POST stories` 写 `status`。
4. 已是目标状态则 `skipped_same`。

对话约定：**改状态先 dry-run**，用户确认后再正式更新。部分项目工作流可能禁止直改 status，失败时提示人工在 TAPD 页面流转。

## 命令

查询我负责的未上线测试子需求：

```powershell
$env:PYTHONIOENCODING = "utf-8"
@'
{ "action": "list", "not_online": true }
'@ | Set-Content -Encoding UTF8 ".tapd/qa_status.json"

python "{baseDir}/scripts/qa_status.py" --payload-file ".tapd/qa_status.json"
```

查看状态映射：

```powershell
python "{baseDir}/scripts/qa_status.py" --action status-map
```

更新为已上线（先预览）：

```powershell
$env:PYTHONIOENCODING = "utf-8"
@'
{
  "action": "update",
  "story_id": "1166882899001025058",
  "status": "已上线",
  "dry_run": true
}
'@ | Set-Content -Encoding UTF8 ".tapd/qa_status.json"

python "{baseDir}/scripts/qa_status.py" --payload-file ".tapd/qa_status.json"
```

## 结果字段

| 字段 | 说明 |
|------|------|
| `items[]` / `results[]` | 查询列表或更新结果 |
| `status_map` | 英文→中文 |
| `last_steps` | 结束状态 |
| `results[].action` | `would_update` / `updated` / `skipped_same` / `failed` |

## 与其它 Skill

| 场景 | Skill |
|------|--------|
| 创建测试子需求 | `tapd-create-sub-stories` |
| 按子需求回填配置处理人 | `tapd-config-init` |
| 关缺陷 | `tapd-close-bug` |
| 通用 API | `tapd-plus` + 本 Skill 封装的 QA 状态能力 |
