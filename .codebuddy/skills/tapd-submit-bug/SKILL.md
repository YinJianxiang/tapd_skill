---
name: tapd-submit-bug
description: 提交 TAPD 缺陷并上传截图或日志附件。当用户要求提 bug、提交缺陷、上传 bug 截图，或需要创建 TAPD 工单时使用。需配置 .tapd/tapd_env.json（认证）与 project_config.json（项目）。
---

# TAPD Submit Bug Skill

聚焦 TAPD 缺陷提交与附件上传场景。基于 TAPD 开放 API 与 `tapd_client_stdlib.py`，完成「创建 bug -> 上传截图/日志附件 -> 返回链接」的标准流程。
优先使用通用包装脚本 `scripts/submit_bug_with_attachment.py`，避免 Windows PowerShell 下中文参数直传引起的乱码或转义问题。

## 何时使用

- 用户明确要求提交 TAPD bug。
- 用户给出 bug 标题、描述、优先级、严重程度、负责人、提单人等字段。
- 用户需要把本地截图、日志或录屏作为附件关联到 bug。

## 必要输入

- `title`：缺陷标题（创建必填）。
- `description`：问题描述与复现步骤。
- 可选 `file`：本地附件路径（截图/日志等）。

以下固定字段可通过本地配置自动补齐（推荐）：

- `workspace_id`：项目编号。
- `current_owner`：当前负责人昵称（可由模块自动映射）。
- `reporter`：提单人昵称。
- `version`：发现版本（提交时映射为 TAPD 字段 `version_report`）。
- `iteration`：迭代名称（如 `【国内智投】26-06`，提交时自动解析为 `iteration_id`）。

以下字段由脚本按约定自动推导（无需手写）：

| 页面字段 | TAPD API 字段 | 推导规则 |
|---------|--------------|---------|
| 测试人员 | `te` | = `reporter`（创建人） |
| 开发人员 | `de` | = `current_owner`（处理人） |
| 软件平台 | `platform` | = `module`（模块） |
| 发现版本 | `version_report` | = 配置/ payload 的 `version` |
| 优先级标签 | `priority_label` | 由 `priority` 值自动映射中文 |
| 缺陷标签 | `label` | 用户指定标签 + AI 高置信可附带 `简单问题`；多标签用 `|` 分隔 |

以下字段需由 AI 判断或人工显式指定（不再由配置默认）：

- `priority`：优先级（如 `high`、`medium`、`low`）。
- `severity`：严重程度（如 `fatal`、`serious`、`normal`、`prompt`）。
- `label` / `labels`：缺陷标签（可选）。其中 **「简单问题」** 由 AI 按下文规则判定；多个标签用 `|` 分隔，也支持数组或逗号分隔字符串。

## 环境与认证

**用户无需手动开 CMD。** Agent 在对话中直接执行脚本。

- **认证**：`.tapd/tapd_env.json` 中填写 `access_token`（或 `api_user` + `api_password`）；环境变量可覆盖。
- **项目**：`.tapd/project_config.json` 中 `workspace_id` 及默认值（见下文）。
- 可选：`api_base_url`（默认 `https://api.tapd.cn`）、`base_url`（默认 `https://www.tapd.cn`）。

## 标准执行流程

1. 调用 `POST bugs` 创建缺陷。
2. 从返回体读取 bug 长 id（示例：`1169725253001000006`）。
3. 若有附件，调用 `upload-attachment` 子命令上传并关联：
   - `--workspace-id` = 项目编号
   - `--entry-id` = bug 长 id
   - `--type` = `bug`
   - `--file` = 本地文件路径
4. 返回 bug id、附件 id（如有）和前端可访问链接。

## 附件失败恢复（防重复提单）

- 脚本会基于 `workspace_id + title + description + reporter` 计算 `idempotency_key`，并将状态落盘到：
  - `.tapd/submit_state.json`
- 当出现“Bug 已创建成功，但附件上传失败”时：
  1. 状态会记录 `bug_id` 且 `attachment_uploaded=false`；
  2. 下次同内容重试会优先命中状态，直接对已有 `bug_id` 重试附件上传；
  3. 不会再次执行 `POST bugs`，避免重复提单。
- 支持显式恢复参数：
  - `--resume-only`：仅从状态恢复并继续附件上传；若没有可恢复状态则报错，不会新建 bug。

恢复结果说明：

- 成功返回里会附带：
  - `recovered`：是否命中恢复路径；
  - `idempotent_skip`：是否因为同一附件已上传而直接幂等返回；
  - `idempotency_key`：本次幂等键，便于排障。
- 附件失败时会返回结构化错误：
  - `error=attachment_upload_failed`
  - `next_action=retry_attachment_only`
  - 同时包含 `bug_id` 与 `bug_url`，可直接人工查看已创建缺陷。

## 本地配置（一次性预设）

推荐先执行配置初始化 Skill 自动生成/升级配置：

```powershell
python ".cursor/skills/tapd-config-init/scripts/init_tapd_config.py"
```

1. 复制模板为本地私有配置文件（不入库）：
   - 模板：`.tapd/project_config.template.json`
   - 本地：`.tapd/project_config.json`
2. 在本地文件中维护固定信息：
   - `workspace_id`
   - `defaults.reporter` / `defaults.version`（发现版本）/ `defaults.iteration`（迭代名称）
   - `defaults.name`：标题前缀名称，提交时格式化为 `【name】xxx`（如 `全域投放`）
   - **勿配置 `defaults.module`**：`module` 须用户确认或 AI 高置信写入 payload，脚本不会读取该默认项
   - `module_owner_map`（模块 -> 开发负责人）
   - 可选 `module_field_map`（模块级自定义字段）

示例：

```json
{
  "workspace_id": "69725253",
  "defaults": {
    "reporter": "张三",
    "version": "【国内智投】26-06",
    "iteration": "【国内智投】26-06",
    "label": "自动化提单|回归",
    "name": "全域投放"
  },
  "module_owner_map": {
    "前端": "李四",
    "后端": "王五"
  }
}
```

`iteration` 填迭代名称（与 TAPD 页面显示一致），脚本提交前会调 API 解析为 `iteration_id`；通常与发现版本名相同。可用 `tapd_client_stdlib.py iterations --workspace-id <ID>` 核对名称。

## 命令示例（推荐：payload 文件 + 通用脚本）

### Windows（PowerShell）

```powershell
# baseDir 为当前 skill 根目录（运行时注入）
$env:PYTHONIOENCODING = "utf-8"
$payload = Join-Path $env:TEMP "tapd_bug_payload.json"

@'
{
  "workspace_id": "69725253",
  "title": "素材配置批量上传200个视频提交时出现封面错误",
  "description": "问题描述：...复现步骤：...",
  "priority": "high",
  "severity": "fatal",
  "current_owner": "开发同学昵称",
  "reporter": "提单人昵称"
}
'@ | Set-Content -Encoding UTF8 $payload

python "{baseDir}/scripts/submit_bug_with_attachment.py" `
  --payload-file $payload `
  --file "C:\path\to\screenshot.jpg"
```

### Windows（PowerShell，配置兜底最简提单）

```powershell
$env:PYTHONIOENCODING = "utf-8"
$payload = Join-Path $env:TEMP "tapd_bug_payload_min.json"

@'
{
  "title": "登录页弱网环境下点击登录时出现白屏",
  "summary": "弱网切换后登录页出现白屏，影响登录流程",
  "description": "复现步骤：1) 打开登录页 2) 网络切为3G 3) 输入账号密码点击登录；实际结果：白屏卡住；期望结果：可正常进入首页。",
  "module": "前端",
  "name": "登录"
}
'@ | Set-Content -Encoding UTF8 $payload

python "{baseDir}/scripts/submit_bug_with_attachment.py" `
  --payload-file $payload `
  --config-file ".tapd/project_config.json" `
  --file "C:\path\to\screenshot.jpg" `
  --dry-run
```

### Windows（PowerShell，附件失败后仅恢复绑定）

```powershell
$env:PYTHONIOENCODING = "utf-8"
python "{baseDir}/scripts/submit_bug_with_attachment.py" `
  --payload-file $payload `
  --config-file ".tapd/project_config.json" `
  --file "C:\path\to\screenshot.jpg" `
  --resume-only
```

### macOS / Linux（Bash/Zsh）

```bash
# baseDir 为当前 skill 根目录（运行时注入）
export PYTHONIOENCODING="utf-8"
payload="${TMPDIR:-/tmp}/tapd_bug_payload.json"

cat > "$payload" <<'EOF'
{
  "workspace_id": "69725253",
  "title": "素材配置批量上传200个视频提交时出现封面错误",
  "description": "问题描述：...复现步骤：...",
  "priority": "high",
  "severity": "fatal",
  "current_owner": "开发同学昵称",
  "reporter": "提单人昵称"
}
EOF

python3 "{baseDir}/scripts/submit_bug_with_attachment.py" \
  --payload-file "$payload" \
  --file "/path/to/screenshot.jpg"
```

## 兼容方式（fallback）

```powershell
# 仍可分两步调用 tapd_client_stdlib.py
# 1) POST bugs
python ".cursor/skills/tapd-plus/scripts/tapd_client_stdlib.py" post --endpoint "bugs" `
  -p workspace_id=69725253 `
  -p title="登录页弱网环境下点击登录时出现白屏" `
  -p description="问题描述：...复现步骤：..." `
  -p priority=high `
  -p severity=fatal `
  -p current_owner="开发同学昵称" `
  -p reporter="提单人昵称"

# 2) upload-attachment
python ".cursor/skills/tapd-plus/scripts/tapd_client_stdlib.py" upload-attachment `
  --workspace-id 69725253 `
  --entry-id 1169725253001000006 `
  --type bug `
  --file "C:\path\to\screenshot.jpg"
```

## 返回结果格式

- Bug 链接：`{TAPD_BASE_URL}/{workspace_id}/bugtrace/bugs/view/{id}`
- 返回建议字段：
  - `bug_id`
  - `bug_url`
  - `attachment_id`（有附件时）
  - `attachment_filename`（有附件时）

## 注意事项

- 附件上传必须在 bug 创建成功之后执行。
- `upload-attachment` 的 `--type` 必须是 `bug`，不要与其他接口参数混用。
- 若只使用 `setx` 设置过 `TAPD_ACCESS_TOKEN`，当前会话拿不到变量时，脚本会尝试从 `HKCU\Environment` 读取。
- 若实例字段有定制（如严重程度候选值），应按项目实际配置传值。
- 推荐优先使用 `submit_bug_with_attachment.py --payload-file ...` 传中文字段，减少 PowerShell 引号与编码干扰。
- 示例中统一使用 `{baseDir}`，避免写死 `.cursor` 导致跨仓库复用困难。
- 支持参数优先级：CLI 显式参数 > payload 字段 > 本地配置默认值。
- `module` 已提供但未命中 `module_owner_map` 时会报错，避免负责人为空直接提单。
- 默认配置路径为 `.tapd/project_config.json`，可通过 `--config-file` 指定其他路径覆盖。

## AI 解析与确认提交流程（会话约定）

日常使用建议输入：`Bug截图 + 简述 + 模块（前端/后端，可选）`。AI 需先提炼以下结构化字段：

- `title`：缺陷标题正文（脚本会格式化为 `【name】xxx`）
- `name`：标题前缀（如 `全域投放`）；未指定时用 `defaults.name`；**仅拼 title，不提交 TAPD**
- `summary`：问题概述（用于拼进 `description`）
- `description`：包含复现步骤、实际结果、期望结果、影响范围
- `module`：提单路由模块（`前端` / `后端`），用于映射 `current_owner`；**必填，且不得走 `defaults.module`**
- `priority`：AI 判定优先级
- `severity`：AI 判定严重程度
- `label`：缺陷标签；**「简单问题」** 见下文判定规则（其它标签由用户指定）

### 前后端判定（module）

`module` 仅取 `前端` 或 `后端`，用于映射 `current_owner`，**不得**写入 title 的【】前缀。

#### 判定信号（高置信度才可自动填）

| 倾向前端 | 倾向后端 |
|---------|---------|
| 白屏、布局错乱、样式/对齐、按钮无响应（无接口报错） | 接口 4xx/5xx、超时、网关错误 |
| 列表/图表展示异常，但接口返回数据正确 | 返回数据错误、统计口径不对、缺字段 |
| 路由跳转、弹窗、表单校验提示异常 | 任务/job 失败、导出文件内容错误 |
| 仅特定浏览器/分辨率复现 | 权限/鉴权在服务端拒绝（401/403） |
| 控制台仅有前端报错（组件、undefined） | 日志有 SQL/服务栈 trace、RPC 失败 |

#### 判定步骤

1. 从描述 + 截图 + 日志提炼「现象」和「失败层级」（UI / 接口 / 数据 / 任务）。
2. 有 Network/接口状态码时优先参考：5xx/超时 → 后端；200 且数据正确仅展示错 → 前端。
3. **仅在高置信度**时 AI 可写入 `module`；须在草稿中附一行「模块判定理由」。
4. **低置信度或场景复杂**（前后端耦合、信息不足、接口与 UI 同时异常、无法区分数据/展示问题）时：
   - **禁止**主观臆测、禁止猜测填 `module`、**禁止**使用 `defaults.module`；
   - **禁止**执行 `--dry-run` 或正式提交；
   - 向用户展示已整理草稿（标题、描述、优先级/严重程度建议等），并**明确请用户填写模块**：

```text
请确认以下字段后再继续：
- 模块 / 软件平台：请填写「前端」或「后端」
（可选说明：我未能可靠判断归属，因为 …）
```

5. 用户回复「前端」或「后端」后，再进入 `--dry-run` → 用户确认提交 → 正式提交。

#### 字段优先级（module）

用户显式指定（对话 / payload / CLI）> AI 高置信判定值。**无 `defaults.module` 兜底。**

### 「简单问题」标签判定（label）

`label` 与 `priority_label`（优先级）、`module`（前后端）**无关**。本项目 TAPD 缺陷标签候选值（须**完全一致**，勿自造）：

`重复bug`｜`无需解决`｜`无法重现`｜`简单问题`｜`阻塞`｜`开发受阻`｜`有风险`｜`等待设计走查`｜`方案已沟通`｜`等待转测`

本节仅约定 AI 是否自动附带 **`简单问题`**；其它标签仅用户显式指定时写入。

#### 可标「简单问题」的信号（须同时满足多项，高置信度）

| 维度 | 条件 |
|------|------|
| 现象 | 纯展示/文案/样式/对齐/图标/颜色/状态灯；或单一交互无响应且无接口异常 |
| 范围 | 单一页面/组件，不影响核心下单、投放、支付等主流程 |
| 根因 | 复现步骤明确，改动面小（常见 1 处判断或展示映射），无需跨服务联调 |
| 数据 | 无数据算错、无接口字段缺失、无任务/job 失败 |
| 分级 | 通常 `severity` 为 `prompt` 或 `normal`，`priority` 为 `low` 或 `medium` |

**典型可标**：错别字、按钮样式错位、固定条件下 Toast 文案错误、状态图标颜色与展示内容明显不一致且仅展示层问题。

#### 不得标「简单问题」的信号

- 根因不明、需查接口/日志/数据库才能定位
- 多模块状态不一致，可能牵涉校验规则或服务端结论
- 接口 4xx/5xx、超时、返回数据错误
- 阻塞、崩溃、白屏、核心流程不可用
- `severity` 为 `fatal` / `serious`，或 `priority` 为 `high` / `urgent`
- 用户明确说「不简单」「复杂」「先别标」

#### 判定步骤

1. 在 `module` 已确认的前提下，根据描述 + 截图 + 日志判断是否满足「可标」条件。
2. **高置信度**：在 payload 的 `label` 写入 `简单问题`（若已有其它用户指定标签，用 `|` 拼接，如 `回归|简单问题`）。
3. **低置信度或不确定**：**禁止**猜测标 `简单问题`；草稿展示为「标签：无」或「标签：待确认（是否简单问题）」；**不阻塞** dry-run/提交（与 `module` 不同）。
4. 须在草稿中附一行 **「简单问题判定理由」**（标了写为何标，未标写为何不标或待确认原因）。

#### 字段优先级（label / 简单问题）

用户显式指定（含「标/simple/不要标简单问题」）> AI 高置信判定 `简单问题` > 不标。  
`defaults.label` **不得**用于自动附带 `简单问题`（可含其它固定标签如 `回归`，但与 `简单问题` 分开判断）。

### 标题（title）生成规则

**格式**：`【{name}】{功能场景}{操作/行为}{问题时态词}{现象}`

- **`name`**：来自 payload / `defaults.name`（如 `全域投放`），写入 `【】` 内；与 `module`（前端/后端）无关。
- **`title` 正文**：AI 按 `description` 压缩；payload 可只写正文，脚本自动拼成 `【name】正文`。
- 问题时态词常用「时出现」「失败」「报错」「无法」等。

**示例**（`defaults.name` = `全域投放`）：

| payload title 正文 | 提交到 TAPD |
|-------------------|-------------|
| `素材配置批量上传200个视频提交时出现封面错误` | `【全域投放】素材配置批量上传200个视频提交时出现封面错误` |

**生成步骤**：

1. 取 `name`（payload > `defaults.name`）。
2. 从描述提炼 title **正文**（功能 + 操作 + 现象）。
3. 脚本格式化为 `【name】正文`；自检为中文。

**反例**：

- `批量上传视频封面报错`（未配置 name 且 title 无【】）
- `【后端】素材上传失败`（误用 module 当前缀）
- `Upload failed when submitting videos`（英文整句）

低置信度处理：

- **`module`**：低置信度时**不得**填值、不得 dry-run、不得提交；必须等用户明确「前端」或「后端」。
- **`简单问题`（label）**：低置信度时**不得**猜测标注；可继续 dry-run/提交，标签留空或标注待确认。
- **`priority` / `severity`**：无法可靠判断时给出建议值并标注「待确认」；用户确认后再提交。

字段来源优先级：

- 人工显式传值（CLI / payload）> AI 判定值 > 配置默认（仅 `name` 可走 `defaults.name`；`module` / `priority` / `severity` / `简单问题` 均无配置兜底）。

执行约定：

1. **`module` 未确认前**：只展示整理后的草稿 + 请用户补全模块；不调用脚本。
2. **`module` 已确认后**：执行 `--dry-run` 展示完整缺陷单草稿（含配置自动带入字段）。
3. 用户回复「确认提交」后，再执行正式提交（去掉 `--dry-run`）。
4. 正式提交时必须与「已确认草稿」一致，禁止切换为英文标题/英文描述，禁止擅自改 `module`。

草稿展示语言约定：

- `--dry-run` 后给用户展示的“草稿内容”必须使用中文提示，不使用英文字段说明句式。
- 标题展示值必须是中文语义；若内部草稿标题是英文，需先转换/改写为中文再展示给用户确认。
- 即使 payload 内部字段名是英文（如 `title`/`description`），对用户展示时也要转成中文标签。
- 推荐展示模板如下：

```text
草稿内容：
- 标题：【name】xxx（name=xxx）
- 空间ID：xxx
- 当前处理人：xxx
- 提单人：xxx
- 迭代：xxx（配置名称，非 ID）
- 发现版本：xxx
- 模块 / 软件平台：xxx（用户确认或 AI 高置信；附判定理由一行）
- 测试人员：xxx（同提单人）
- 开发人员：xxx（同处理人）
- 优先级：xxx（建议，可改）
- 严重程度：xxx（建议，可改）
- 标签：xxx（无 / 简单问题 / 待确认；附简单问题判定理由一行）
- 描述：
  复现步骤：
  1) xxx
  2) xxx
  实际结果：xxx
  期望结果：xxx
  影响范围：xxx
- 附件：xxx（已带上/无）
```

正式提交语言约定：

- 提交到 TAPD 的 `title`、`summary`、`description` 默认使用中文业务表达，不得使用英文整句。
- 若用户最初提供英文信息，AI 需要先翻译并给出中文草稿，用户确认后再提交。
- 提交前必须做一次自检：
  - `title` 符合 `【name】功能场景+操作+现象` 格式（`name` 来自配置或 payload），且不是英文句子。
  - `description` 必须包含中文小节：`复现步骤`、`实际结果`、`期望结果`、`影响范围`。
- 换行要求：`description` 需按段落组织，提交前转换为 TAPD 友好的 `<br/>` 换行，避免页面展示成一行。
- 若发现 payload 为英文内容，必须先中止提交并回到“草稿确认”步骤，不得直接提单。
