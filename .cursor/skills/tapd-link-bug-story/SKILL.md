---
name: tapd-link-bug-story
description: >-
  将 TAPD 缺陷与需求（story）建立关联：支持显式指定 story_id、按需求标题匹配，或根据缺陷标题/描述自动匹配当前迭代需求并调用 POST relations。
  当用户要求关联需求、绑定 story、补关联需求、bug 关联需求、自动匹配需求，或在提 bug 后补充「关联需求」时使用。
  为 tapd-submit-bug 的配套子 Skill。需配置 .tapd/tapd_env.json 与 project_config.json。
---

# TAPD Link Bug Story（缺陷关联需求）

`tapd-submit-bug` 只调用 `POST bugs`，**不会**自动写入「关联需求」。本 Skill 负责第二步：定位缺陷与需求 → 匹配（可选）→ `POST relations`。

## 何时使用

- 提 bug 后「关联需求」为空，需要补关联。
- 用户指定 bug + story，要求建立关联。
- 仅想预览「该 bug 应关联哪条需求」（`--match-only`）。

### 触发词

关联需求、绑定需求、关联 story、补关联、bug 关联需求、自动匹配需求、link story

## 必要输入（三选一定位缺陷）

| 字段 | 说明 |
|------|------|
| `bug_id` | 长 id 或短 id |
| `bug_url` | TAPD 缺陷链接 |
| `title` | 缺陷标题（须唯一命中） |

需求侧（三选一；均不提供则**自动匹配**）：

| 字段 | 说明 |
|------|------|
| `story_id` | 需求 ID |
| `story_url` | 需求链接 |
| `story_name` | 需求标题（精确或唯一模糊命中） |

也可在 `project_config.json` → `link_story` 中配置 `story_id` / `story_name`（非空时直接关联；为空则走自动匹配）。

**优先级**：payload/CLI > `link_story` 配置 > 自动标题匹配。

可选：

- `query`：覆盖自动匹配的查询文本（默认 = 缺陷标题 + 描述摘要）
- `iteration`：限定匹配范围；默认 `defaults.iteration`
- `force`：低置信度也强制关联（须已明确 story 或用户确认）
- `match_only`：只输出候选，不调用 relations
- `dry_run`：预览将执行的操作

## 标准流程

1. `GET bugs` 定位缺陷。
2. 若未指定 story：`GET stories` 拉取迭代内需求 → 按标题相似度打分排序。
3. `GET bugs/get_related_stories` 检查是否已关联（幂等跳过）。
4. 置信度 **high** 或显式指定 story → `POST relations`。
5. 置信度 **medium/low** → **不自动关联**，展示候选并请用户确认。

### 置信度规则

| 级别 | 条件 |
|------|------|
| high | score ≥ 0.55 且与第二名差距 ≥ 0.20 |
| medium | score ≥ 0.35 |
| low | 其余 |

仅 **high** 或 `--force` / 显式 `story_id` 时正式关联。

## 命令示例

### 自动匹配并关联（推荐）

```powershell
$env:PYTHONIOENCODING = "utf-8"
python ".cursor/skills/tapd-link-bug-story/scripts/link_bug_story.py" `
  --bug-id 1166882899001055405 `
  --dry-run
```

确认后去掉 `--dry-run` 正式关联。

### 显式指定需求

```powershell
python ".cursor/skills/tapd-link-bug-story/scripts/link_bug_story.py" `
  --bug-id 1055405 `
  --story-id 1166882899001022116
```

### 仅预览匹配候选

```powershell
python ".cursor/skills/tapd-link-bug-story/scripts/link_bug_story.py" `
  --bug-id 1055405 `
  --match-only
```

### payload 文件（避免 PowerShell 中文乱码）

```powershell
$payload = Join-Path $env:TEMP "tapd_link_payload.json"
@'
{
  "bug_id": "1166882899001055405",
  "story_id": "1166882899001022116"
}
'@ | Set-Content -Encoding UTF8 $payload

python ".cursor/skills/tapd-link-bug-story/scripts/link_bug_story.py" `
  --payload-file $payload `
  --config-file ".tapd/project_config.json"
```

## 与提 bug 流程配合

提 bug 成功后，若用户未指定需求且希望自动关联：

1. 先 `tapd-submit-bug` 创建缺陷，拿到 `bug_id`。
2. 再调用本 Skill：`link_bug_story.py --bug-id <id> --dry-run`。
3. 展示候选与置信度；用户确认后正式执行（去掉 `--dry-run`）。

## 返回字段

| 字段 | 说明 |
|------|------|
| `action` | `linked` / `skip_already_linked` / `would_link` / `need_confirm` / `match_only` |
| `bug_url` / `story_url` | 前端链接 |
| `match.candidates` | 自动匹配时的 Top 候选 |
| `confidence` | high / medium / low |
| `already_linked` | 是否已存在关联 |

## 注意事项

- TAPD 关联需求与创建 bug **是两个 API**：`POST bugs` + `POST relations`。
- 已关联同一需求时脚本**不会重复创建**。
- 自动匹配默认限定 `defaults.iteration` 内需求；迭代内得分偏低时会 **fallback** 到全项目（`link_story.fallback_project`，默认 true）。
- 可选配置 `project_config.json` → `link_story.match_scope`：`iteration`（默认）或 `project`。
- `link_story.story_id` / `story_name`：项目级固定关联；与 payload 同时存在时 payload 优先。
- relations 参数：`source_type=story`、`target_type=bug`。

## 依赖

- `.tapd/tapd_env.json`（认证）
- `.tapd/project_config.json`（`workspace_id`、`defaults.iteration`）
- `tapd-plus/scripts/tapd_client_stdlib.py`
