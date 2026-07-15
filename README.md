# TAPD Skills

基于 TAPD 开放 API 的自动化工具集，供 **Cursor** / **Claude Code**（以及 Codebuddy）在对话中完成缺陷提单、关单、统计、关联需求等操作。脚本仅依赖 Python 标准库，无需额外安装依赖。

## 功能一览

| Skill | 说明 | 典型说法 |
|-------|------|----------|
| `tapd-config-init` | 初始化或升级本地项目配置 | 「初始化配置」「改后端负责人」 |
| `tapd-submit-bug` | 提交缺陷，支持截图内嵌与自动关联需求 | 「帮我提个 bug」「截图提单」 |
| `tapd-link-bug-story` | 将缺陷与需求（story）建立关联 | 「关联需求」「绑定 story」 |
| `tapd-close-bug` | 关闭或解决缺陷（单个/批量） | 「把 bug 关了」「标记已解决」 |
| `tapd-bug-stats` | 按时间、负责人、平台等统计并导出 Excel | 「统计本周未解决缺陷」 |
| `tapd-plus` | 通用 TAPD API：需求、任务、迭代、评论、工时等 | 「查一下当前迭代需求」 |

配置完成后，在 Agent 对话里直接描述需求即可，会自动加载对应 Skill 并执行脚本，**无需手动开 CMD**。

---

## 快速开始

### 前置条件

- Python 3.8+
- 已安装 [Cursor](https://cursor.com) 和/或 [Claude Code](https://code.claude.com)
- 本仓库作为独立工作区打开，或把 Skills 拷贝/软链到你的业务项目中

### 1. 配置认证

复制模板并填写 TAPD 凭证：

```powershell
copy .tapd\tapd_env.template.json .tapd\tapd_env.json
```

编辑 `.tapd/tapd_env.json`：

```json
{
  "access_token": "你的_TAPD_Access_Token",
  "api_user": "",
  "api_password": "",
  "api_base_url": "",
  "base_url": "",
  "current_user_nick": ""
}
```

| 字段 | 说明 |
|------|------|
| `access_token` | **推荐**。TAPD 个人 Access Token |
| `api_user` + `api_password` | 备选：API 账号 + 密码（未填 token 时使用） |
| `api_base_url` | 可选，默认 `https://api.tapd.cn` |
| `base_url` | 可选，默认 `https://www.tapd.cn`（用于拼缺陷链接） |
| `current_user_nick` | 可选，当前用户昵称 |

> 也可通过环境变量覆盖（优先级高于本地文件）：`TAPD_ACCESS_TOKEN`、`TAPD_API_USER`、`TAPD_API_PASSWORD` 等。填写本地 JSON 后，一般**无需**再 `setx` 环境变量。

### 2. 初始化项目配置

```powershell
python .cursor\skills\tapd-config-init\scripts\init_tapd_config.py
```

会从模板生成/补齐 `.tapd/project_config.json`。再编辑必填项，例如：

```json
{
  "workspace_id": "你的项目ID",
  "defaults": {
    "reporter": "张三",
    "version": "【示例项目】26-06",
    "iteration": "【示例项目】26-06",
    "label": "",
    "name": "示例产品"
  },
  "module_owner_map": {
    "前端": "李四",
    "后端": "王五"
  },
  "module_field_map": {},
  "link_story": {
    "enabled": true,
    "story_id": "",
    "story_name": "",
    "match_scope": "iteration",
    "fallback_project": true
  },
  "embed_images": {
    "enabled": true,
    "also_attach": false
  },
  "attachments": {
    "enabled": false
  }
}
```

也可用命令快速改字段（会覆盖）：

```powershell
python .cursor\skills\tapd-config-init\scripts\init_tapd_config.py `
  --set "defaults.name=示例产品" `
  --set "module_owner_map.后端=王五"
```

或对话里说：「把标题前缀改成示例产品，后端负责人改成王五」。

### 3. 接入 Cursor / Claude 并开始使用

配置就绪后，把本仓库的 Skills 放到对应工具能发现的目录，再在对话里用自然语言即可。

#### Cursor

本仓库已自带 `.cursor/skills/`，用 Cursor **打开本仓库**（或把该目录拷到业务项目的 `.cursor/skills/`）即可。

| 范围 | 路径 |
|------|------|
| 项目级（推荐） | `<项目根>/.cursor/skills/<skill-name>/SKILL.md` |
| 个人全局 | `~/.cursor/skills/<skill-name>/SKILL.md` |

使用方式：

1. 用 Cursor 打开已配置好的项目工作区。
2. 打开 Agent / Chat，用自然语言描述任务（见下方示例）。
3. Agent 会根据 `SKILL.md` 的 `description` 自动匹配并加载对应 Skill，再执行脚本。
4. 也可在对话中显式点名，例如「用 tapd-submit-bug 提个缺陷」。

示例：

- 「登录页白屏，帮我提个 bug，截图在桌面」
- 「先预览草稿，确认后再提交」
- 「统计本周我创建的未解决缺陷」
- 「把刚才那个 bug 关了」
- 「把 bug 12345 关联到需求『用户登录优化』」

#### Claude Code

Claude Code 从 `.claude/skills/`（项目）或 `~/.claude/skills/`（个人）加载 Skills，格式与 Cursor 相同（目录 + `SKILL.md`）。

任选一种接入方式：

```powershell
# 方式 A：项目级 — 把仓库内 Skills 同步到 .claude/skills
New-Item -ItemType Directory -Force .claude\skills | Out-Null
Copy-Item -Recurse .cursor\skills\* .claude\skills\

# 方式 B：个人全局 — 拷到用户目录（对所有项目生效）
New-Item -ItemType Directory -Force $HOME\.claude\skills | Out-Null
Copy-Item -Recurse .cursor\skills\* $HOME\.claude\skills\
```

| 范围 | 路径 |
|------|------|
| 项目级 | `<项目根>/.claude/skills/<skill-name>/SKILL.md` |
| 个人全局 | `~/.claude/skills/<skill-name>/SKILL.md` |

使用方式：

1. 在项目根目录启动 Claude Code（`claude`）。
2. 可用 `/skills` 确认已加载 `tapd-submit-bug` 等 Skill。
3. 直接说自然语言，或用 `/tapd-submit-bug` 这类命令显式调用。
4. 认证与项目配置仍走工作区根目录下的 `.tapd/`（与 Cursor 共用同一套配置即可）。

> **提示**：Skill 脚本通过相对路径解析仓库根目录下的 `.tapd/`。请在**配置了 `.tapd/` 的项目根**里对话；若 Skills 装在全局目录，仍建议在业务项目根启动 Agent，以便读到本地配置。

#### Codebuddy

本仓库同时维护 `.codebuddy/skills/` 副本，用 Codebuddy 打开本仓库即可，用法与 Cursor 类似。

---

## 配置说明

本地文件均在 `.tapd/` 下（模板可提交，本地文件已加入 `.gitignore`，**勿提交**）。

| 文件 | 用途 | 是否提交 |
|------|------|----------|
| `tapd_env.template.json` | 认证模板 | ✅ |
| `tapd_env.json` | 认证凭证 | ❌ |
| `project_config.template.json` | 项目配置模板 | ✅ |
| `project_config.json` | 项目默认值与映射 | ❌ |
| `submit_state.json` | 提单幂等状态（自动生成） | ❌ |

### `project_config.json` 字段

| 路径 | 说明 |
|------|------|
| `workspace_id` | **必填**。TAPD 项目 ID |
| `defaults.reporter` | 默认提单人（同时映射为测试人员 `te`） |
| `defaults.version` | 发现版本（提交时映射为 `version_report`） |
| `defaults.iteration` | 迭代名称（与 TAPD 页面一致，脚本会解析为 `iteration_id`） |
| `defaults.name` | 标题前缀，提交时格式化为 `【name】xxx` |
| `defaults.label` | 默认缺陷标签（多标签用 `\|` 分隔） |
| `module_owner_map` | 模块 → 开发负责人，如 `前端` / `后端` |
| `module_field_map` | 可选，模块级自定义字段 |
| `link_story.*` | 提 bug 后是否自动关联需求；可固定 `story_id` / `story_name`，留空则按标题自动匹配 |
| `embed_images.enabled` | 截图是否内嵌到描述「实际结果」（默认 `true`） |
| `embed_images.also_attach` | 内嵌同时还挂附件区（默认 `false`） |
| `attachments.enabled` | 是否默认上传到附件区（默认 `false`） |

注意：

- **不要**配置 `defaults.module`：模块须由用户或 AI 高置信指定，无配置兜底。
- `iteration` 填迭代**名称**（不是 ID）。可用 `tapd-plus` 的 `iterations` 核对名称是否与页面一致。

### 获取凭证与 workspace_id

1. **Access Token**：TAPD 个人设置 → API → 生成 Access Token。
2. **workspace_id**：打开项目任意页面，URL 中 `https://www.tapd.cn/<数字>/...` 里的数字即为项目 ID；或对话里用 `tapd-plus` 查询参与项目。

---

## 各 Skill 怎么用

### 初始化 / 改配置 — `tapd-config-init`

```powershell
# 仅补齐模板缺失字段（不覆盖已有值）
python .cursor\skills\tapd-config-init\scripts\init_tapd_config.py

# 用 JSON payload 更新
python .cursor\skills\tapd-config-init\scripts\init_tapd_config.py `
  --payload-file .tapd\config_update.json
```

对话示例：

- 「初始化 TAPD 配置」
- 「默认提单人改成张三」
- 「前端负责人李四，后端王五」

### 提缺陷 — `tapd-submit-bug`

推荐流程：对话描述问题 → Agent 组装草稿（可 `--dry-run`）→ 你确认 → 正式创建。

行为摘要：

1. 截图默认 `upload_image` 后内嵌到描述「实际结果」
2. 创建 bug，自动补全负责人（按模块）、测试/开发人员、发现版本等
3. 创建后自动搜索并关联需求（可配置关闭）
4. 附件失败时基于 `.tapd/submit_state.json` 幂等恢复，避免重复建单

对话示例：

- 「素材批量上传封面错误，前端 bug，附上截图」
- 「先 dry-run 看看草稿」
- 「确认提交」
- 「附件上传失败，只重传附件」

常用开关：`link_story.enabled=false` 或 `--no-link-story` 可跳过关联需求。

### 关联需求 — `tapd-link-bug-story`

提 bug 后「关联需求」为空，或要单独补绑时使用。

| 定位缺陷 | 指定需求（可选） |
|----------|------------------|
| `bug_id` / 链接 / 标题 | `story_id` / 链接 / 标题；都不给则自动匹配当前迭代 |

对话示例：

- 「把刚才的 bug 关联到需求『用户登录优化』」
- 「bug 1166… 自动匹配关联需求」
- 「只预览候选，先不关联」

中/低置信度不会自动写关联，会返回候选让你确认。

### 关单 — `tapd-close-bug`

支持按 id、链接、标题或对话上下文定位；默认状态 `closed`，也可 `resolved`。

对话示例：

- 「把 bug 12345 关了，备注：已验证」
- 「刚才那两个都关了」
- 「先预览，确认后再关闭」

### 统计 — `tapd-bug-stats`

按创建时间、创建人、软件平台、简单问题标签等筛选，导出 Excel，并在对话中展示明细。

对话示例：

- 「统计本周未解决 bug」
- 「按平台汇总张三创建的缺陷」
- 「只要简单问题标签的」

### 通用查询 — `tapd-plus`

需求、任务、缺陷、迭代、评论、工时、Wiki、工作流、企业微信通知等通用能力。具体字段与命令见 `.cursor/skills/tapd-plus/SKILL.md`。

---

## 目录结构

```
.cursor/skills/          # Cursor Agent Skills（主副本）
.claude/skills/          # Claude Code 使用（需自行从 .cursor/skills 同步）
.codebuddy/skills/       # Codebuddy 同步副本
.tapd/                   # 本地配置与运行时状态（多数勿提交）
  ├── tapd_env.template.json
  ├── project_config.template.json
  ├── tapd_env.json              # 本地生成，勿提交
  ├── project_config.json       # 本地生成，勿提交
  └── submit_state.json         # 提单幂等状态，自动生成
```

各 Skill 目录下通常包含 `SKILL.md`（给 Agent 的说明）与 `scripts/`（可执行 Python）。

---

## 注意事项

- `.tapd/tapd_env.json`、`.tapd/project_config.json` 含项目与凭证信息，已 gitignore，请勿提交。
- 环境变量优先级高于本地配置文件。
- Windows 下中文路径/参数建议由 Agent 走 payload JSON 文件，避免 PowerShell 转义乱码。
- Cursor 用 `.cursor/skills/`，Claude Code 用 `.claude/skills/`；两边可共用同一套 `.tapd/` 配置。
- 更细的字段映射、匹配规则与 CLI 参数，见各 Skill 的 `SKILL.md`。
