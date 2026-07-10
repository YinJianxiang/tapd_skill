---
name: tapd-close-bug
description: 关闭或解决 TAPD 缺陷（支持单个/批量；按 bug id、链接、标题或对话上下文定位）。当用户要求关 bug、关闭缺陷、批量关单、标记已解决、resolved/closed 时使用。需配置 .tapd/tapd_env.json 与 project_config.json。
---

# TAPD Close Bug Skill

聚焦 TAPD 缺陷关闭场景。基于 `tapd-plus/scripts/tapd_client_stdlib.py`，通过 `scripts/close_bug.py` 完成「定位 bug（可多条）→ 预览 → 确认 → 更新状态 → 可选追加评论」。

## 何时使用

- 用户要求关闭、解决 TAPD bug（单个或批量）。
- 用户提供 bug id、TAPD 链接、标题，或**结合对话上下文**指代缺陷（如「刚才那两个」「封面相关的都关了」）。
- 用户要求批量关单前的预览确认。

## 定位方式（可组合，支持批量）

| 方式 | 字段 | 说明 |
|------|------|------|
| 单个 ID | `bug_id` | 长 id 或短 id |
| 多个 ID | `bug_ids` | 数组，或逗号/空格分隔字符串 |
| 结构化列表 | `bugs` | 数组；元素可为 id 字符串、链接字符串、标题字符串，或 `{bug_id/bug_url/title, comment?}` |
| 单个链接 | `bug_url` | 仅单条时使用 |
| 标题匹配 | `title` | 须唯一命中；批量时放入 `bugs` |

可选全局字段：

- `status`：`closed`（默认，已关闭）或 `resolved`（已解决）
- `comment`：关闭说明（写入评论；可被 `bugs[].comment` 覆盖）
- `author`：评论作者；默认 `defaults.reporter`

`workspace_id` 从 `.tapd/project_config.json` 自动读取，或通过 payload / CLI 覆盖。

## 环境与认证

**用户无需手动开 CMD。** Agent 在对话中直接执行脚本。

- **认证**：`.tapd/tapd_env.json`（或环境变量）
- **项目**：`.tapd/project_config.json` 中 `workspace_id`
- **依赖**：同仓库 `tapd-plus/scripts/tapd_client_stdlib.py`
- **上下文辅助**：`.tapd/submit_state.json`（本会话/历史提单记录的 `bug_id` + `title`）

## 标准执行流程

1. **根据上下文解析**一条或多条目标 bug（见下文「AI 上下文定位」）。
2. 组装 payload（单条用 `bug_id`；多条用 `bug_ids` 或 `bugs`）。
3. `GET bugs` 读取每条当前状态与标题。
4. **`--dry-run`** 展示关闭预览（批量时列表展示），等用户确认。
5. 用户回复「确认关闭」后，执行 `POST bugs` 更新各条 `status`。
6. 若提供 `comment`，对每条追加 `POST comments`（支持单条专属说明）。
7. 返回各条 `bug_id`、`bug_url`、原/新状态及批量汇总。

## 状态说明

| status | 含义 |
|--------|------|
| `closed` | 已关闭（默认） |
| `resolved` | 已解决 |

若缺陷已是目标状态（如已是 `closed`），该条 **跳过**；`resolved` → `closed` 会正常执行。批量时继续处理其余条目。

## 命令示例

### 单个关闭

```powershell
python ".cursor/skills/tapd-close-bug/scripts/close_bug.py" `
  --bug-id 1166882899001055244 `
  --dry-run
```

### 批量关闭（多个 ID）

```powershell
python ".cursor/skills/tapd-close-bug/scripts/close_bug.py" `
  --bug-ids "1166882899001055243,1166882899001055244" `
  --comment "复测通过，批量关闭。" `
  --dry-run
```

### payload 文件（推荐：批量 + 中文 comment）

```powershell
@'
{
  "status": "closed",
  "comment": "已在测试环境验证通过。",
  "bugs": [
    { "bug_id": "1166882899001055243" },
    { "bug_id": "1166882899001055244", "comment": "封面问题单独备注：转码后复测通过。" }
  ]
}
'@ | Set-Content -Encoding UTF8 ".tapd/tapd_close_payload.json"

python ".cursor/skills/tapd-close-bug/scripts/close_bug.py" `
  --payload-file ".tapd/tapd_close_payload.json" `
  --dry-run
```

### 按标题批量（标题须各自唯一命中）

```json
{
  "bugs": [
    "【全域投放】素材配置批量上传200个视频提交时出现封面错误",
    "【全域投放】批量上传200个视频时后端封面获取接口报错"
  ],
  "comment": "联调验证通过，关闭。"
}
```

## AI 上下文定位（核心）

用户常不显式给 id，Agent 须**结合当前对话与本地状态**解析要关闭的缺陷，再写入 payload。

### 上下文来源（按优先级）

1. **用户本轮明确给出**：bug id、TAPD 链接、完整标题。
2. **对话内近期已出现的信息**：同会话中刚提交/刚返回的 `bug_id`、`bug_url`、标题列表。
3. **`.tapd/submit_state.json`**：`records` 中 `bug_id` + `title` + `updated_at`（用于「刚才提的」「上次那个封面 bug」）。
4. **用户自然语言指代**（须能唯一对应或经确认）：
   - 「刚才两个 / 这两个」→ 最近 2 条相关 `bug_id`
   - 「封面相关的」→ 在 state 或会话标题中匹配「封面」
   - 「广告详情那个」→ 标题关键词匹配
   - 「1055244 和 1055245」→ 解析为两个 id

### 解析步骤

1. 从用户话术提取：**数量**（几个）、**范围**（全部/这两个/封面相关）、**动作**（关闭/已解决）、**说明**（comment）。
2. 汇总候选 `bug_id` 列表并**去重**。
3. **唯一命中**：直接进入 dry-run 预览。
4. **多条且明确**（如用户列举两个链接）：批量 dry-run。
5. **模糊或歧义**（匹配到 0 条或多条无法取舍）：
   - **禁止**主观臆测关单；
   - 列出候选（id + 标题 + 链接）请用户勾选或补充 id；
   - 用户确认后再组 `bugs` / `bug_ids`。

### 与 submit-bug 的衔接

- 若本会话刚执行过 `submit_bug_with_attachment.py`，优先用返回的 `bug_id` / `bug_url`。
- 若用户说「把刚才提交的关了」，读取 `submit_state.json` 按 `updated_at` 倒序取最近 1 条或用户指定条数。
- 同一 `idempotency_key` 对应唯一 `bug_id`，勿重复加入批量列表。

### 状态与 comment 推断

- 默认 `status=closed`；用户说「已解决」「resolved」→ `resolved`。
- `comment` 可来自用户原话（如「复测通过」「环境问题已恢复」）；批量可共用一条，必要时对单条写 `bugs[i].comment`。

## AI 会话约定

1. 先完成**上下文定位**，再调用脚本；定位结果写入 payload，不单靠 CLI 猜 id。
2. 默认 `status=closed`；用户说「已解决」时用 `resolved`。
3. 先 **`--dry-run`**：

**单条预览：**

```text
关闭预览：
- 标题：xxx
- Bug ID：xxx
- 链接：xxx
- 当前状态：assigned
- 目标状态：closed
- 关闭说明：xxx（无则省略）
```

**批量预览：**

```text
批量关闭预览（共 N 条，目标状态：closed）：
1) 【全域投放】xxx
   - Bug ID：xxx | 当前：assigned → closed
   - 链接：xxx
2) 【全域投放】yyy
   - Bug ID：yyy | 当前：reopened → closed
   - 链接：yyy
- 统一关闭说明：xxx
- 定位来源：用户给出 id / 会话上下文 / submit_state 匹配「封面」
```

4. 用户回复 **「确认关闭」** 后再正式执行（去掉 `--dry-run`）。
5. 已是 `closed`/`resolved` 的条目告知跳过，其余继续。
6. 批量正式执行后汇总：成功 X 条、跳过 Y 条、失败 Z 条。

## 返回结果格式

**单条**（与历史兼容）：扁平 JSON，含 `bug_id` / `bug_url` / `title` / `current_status` / `new_status` / `skipped` / `success`。

**批量**：

```json
{
  "batch": true,
  "dry_run": false,
  "summary": { "total": 2, "success": 1, "skipped": 1, "failed": 0 },
  "results": [ ... ]
}
```

## 注意事项

- 标题匹配不唯一时会报错，请改用 id、链接，或让用户从候选中选择。
- 部分项目工作流若禁止直改 status，API 可能失败；此时查 `workflows/status_map` 或人工在 TAPD 页面流转。
- 参数优先级：CLI > payload > 本地配置。
- 推荐 `--payload-file` 传中文 `comment` 与 `bugs` 数组，避免 PowerShell 编码问题。
- 批量时单条失败不中断其余条目；最终在 `results` 与 `summary.failed` 中体现。
