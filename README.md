# TAPD 自动化工具集

本项目包含两类能力：

1. **飞书机器人**：在飞书会话中查询、提交和关闭 TAPD 缺陷，并管理个人配置。
2. **Agent Skills**：在 Cursor、Claude Code 或 CodeBuddy 中通过自然语言调用 TAPD 能力。

项目脚本基于 Python 标准库实现，无需安装额外 Python 依赖。

> 安全提示：仓库中只应提交模板和源码。访问令牌、应用密钥、用户标识、项目 ID、回调域名及个人配置必须保存在本地配置中，禁止写入 README、模板示例、日志或提交记录。

---

# 第一部分：飞书机器人

## 功能

- 通过表单或自然语言提交缺陷，支持图片。
- 按关键词、缺陷 ID、时间范围或当前迭代查询缺陷和需求。
- 对符合条件的缺陷发起关闭操作。
- 通过交互卡片预览并确认写操作。
- 按飞书用户隔离 LLM、TAPD 和项目配置。
- 使用操作 ID 防止重复提交。

群聊中需要 @机器人；私聊可直接使用。

## 准备配置

复制机器人配置模板：

```powershell
Copy-Item .tapd\feishu_bot.template.json .tapd\feishu_bot.json
```

在本地填写 `.tapd/feishu_bot.json`。主要配置项如下：

- `app_id`：飞书应用标识。
- `app_secret`：飞书应用密钥。
- `verification_token`：事件订阅校验令牌。
- `encrypt_key`：开启事件加密时使用。
- `listen_host`、`listen_port`：本地服务监听地址和端口。
- `llm`：兼容 OpenAI API 的模型地址、模型名和密钥。
- `menu_event_keys`：机器人菜单事件映射。
- `config_editable_paths`：允许用户通过机器人修改的项目配置字段。

同时准备 TAPD 认证和项目配置：

```powershell
Copy-Item .tapd\tapd_env.template.json .tapd\tapd_env.json
Copy-Item .tapd\project_config.template.json .tapd\project_config.json
```

请直接编辑本地文件，不要把真实值写入模板或命令行示例。

## 飞书开放平台配置

创建应用后，按实际需要开通以下能力并发布应用版本：

- 接收机器人私聊和群聊消息。
- 以应用身份发送消息。
- 获取和下载消息中的图片或文件。
- 接收机器人自定义菜单事件。

事件订阅地址：

```text
https://<你的回调域名>/feishu/event
```

卡片交互回调地址：

```text
https://<你的回调域名>/feishu/card
```

建议配置以下菜单事件：

| 菜单 | event_key |
|------|-----------|
| 使用帮助 | `help` |
| 提 Bug | `submit_bug` |
| 查 Bug | `query_bug` |
| 查需求 | `query_story` |
| 关 Bug | `close_bug` |
| 查看配置 | `config_view` |
| 修改配置 | `config_edit` |
| 账号配置 | `account_view` |
| 取消草稿 | `cancel_draft` |

## 启动服务

先通过可信的反向代理或内网穿透工具暴露本地端口，再启动服务：

```powershell
$env:PYTHONIOENCODING = "utf-8"
python scripts\feishu_bug_bot\server.py
```

本地健康检查：

```text
http://127.0.0.1:<监听端口>/health
```

生产或长期运行时应使用 HTTPS，并限制回调入口的访问范围。

## 使用方式

- 菜单「提 Bug」：填写表单并确认后提交。
- 自然语言描述缺陷：生成草稿和确认卡片。
- 菜单「查 Bug / 查需求」：按条件查询并返回列表。
- 菜单「关 Bug」：选择缺陷、预览并确认关闭。
- 菜单「账号配置」：维护当前用户自己的 LLM 和 TAPD 配置。
- 「取消」：清除当前未提交草稿。

所有写操作均应在卡片或文字确认后执行。确认或取消后，原操作应失效。

## 多用户配置

全局配置用于提供默认值，个人配置保存在：

```text
.tapd/users/<用户标识>.json
```

个人配置可覆盖项目、LLM 和 TAPD 认证字段。界面只显示脱敏结果，不应回显完整密钥。用户标识及个人配置文件均不得提交到 Git。

机器人代码位于 `scripts/feishu_bug_bot/`。

## 钉钉多维表同步测试子需求状态

读取配置用户负责的多维表记录，按状态同步 **TAPD 测试子需求（QA Story）**，或调用 `tapd-config-init` 更新本地需求配置。运行不依赖 Agent 或大模型，需要 Python、已安装并登录的 DWS CLI，以及现有 TAPD 认证配置。

**多维表始终只读，禁止修改其状态或其他数据。** 线上写操作只针对 TAPD 测试子需求。

### 手动触发

在项目根目录打开 PowerShell。先预览：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_dingtalk_status_sync.ps1
```

预览会读取多维表和 TAPD，显示拟执行操作，不修改 TAPD 工作项或需求配置。确认后正式执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_dingtalk_status_sync.ps1 -Apply
```

这两个命令均自动加载 `.tapd/dingtalk_status_sync.ps1`，无需手动设置环境变量。运行时间、退出码和结果保存在 `.tapd/dingtalk_status_sync_last_result.json`；预览结果也会保存到该文件。手动执行不会安装、修改或触发定时任务。

### 状态处理规则

| 多维表状态 | TAPD 测试子需求状态 | 没有测试子需求时 |
|------------|--------------------|------------------|
| 已提测 | 开始测试 | 创建测试子需求，挂到关联父需求下 |
| 测试中 | 直接调用 tapd-config-init 刷新本地配置 | 不创建或修改 TAPD 状态 |
| 待上线 | 测试完成 | 报告缺失并跳过该记录 |
| 测试完成 | 已上线 | 报告缺失并跳过该记录 |

涉及 TAPD 状态变更时，目标状态名称必须存在于项目的 QA 工作流中，脚本会动态校验，遇到未知或歧义状态时停止。「测试中」按多维表中的状态判断，直接刷新本地配置，不要求 TAPD 当前状态也是测试中。

只读取配置用户负责的记录，按「负责人」筛选，创建人不参与筛选。用户依次取 `TAPD_SYNC_USER`、`CURRENT_USER_NICK`、`.tapd/tapd_env.json` 的 `current_user_nick`、项目配置的 `defaults.reporter`，然后通过钉钉姓名精确解析唯一人员。无法唯一解析时停止，禁止回退到读取所有人的记录。

默认字段与实际业务表一致：`负责人`（人员列）、`任务进度`（单选状态）、`需求`（标题正文）、`需求链接`（链接）。优先从链接列或需求正文中提取明确的 TAPD Story 链接，兼容 `/tapd_fe/<项目>/story/detail/<ID>`；没有 TAPD 链接时，只接受唯一同名父需求。无精确匹配的记录标为 `skipped_unmatched_story`，不会按近似标题更新。

关联父需求时，自动定位其唯一测试子需求。前端、后端子需求不会被更新。多条测试子需求、同一目标存在冲突映射时，会在写入前停止。

### 本地配置

在 `.tapd/dingtalk_status_sync.ps1` 中配置读取哪张表、各列的名称或字段 ID。`BASE_ID` 定位多维表文件，`TABLE_ID` 定位该文件内的具体数据表。更换数据表时，需要一起核对四个字段配置。

配置文件内容示例，真实值只保存在本机：

```powershell
$env:DINGTALK_AITABLE_BASE_ID = "<Base ID>"
$env:DINGTALK_AITABLE_TABLE_ID = "<Table ID>"
$env:DINGTALK_AITABLE_STORY_FIELD = "需求链接"
$env:DINGTALK_AITABLE_STATUS_FIELD = "任务进度"
$env:DINGTALK_AITABLE_OWNER_FIELD = "负责人"
$env:DINGTALK_AITABLE_TITLE_FIELD = "需求"
$env:TAPD_WORKSPACE_ID = "<TAPD 项目 ID>"
```

`TAPD_WORKSPACE_ID` 未设置时读取 `.tapd/project_config.json`。脚本先读完整表头解析字段与选项，再只查询上述四列；服务端同时筛选配置用户和映射中涉及的状态，返回后再次核验人员 ID。最多读取 10 页、每页 100 条；分页失败或结果不完整时不写入 TAPD。

`.tapd/dingtalk_status_sync.ps1` 被 Git 忽略，手动触发入口和定时任务都会自动加载它。底层 Python 脚本不自动加载该文件或 `.env`。真实表 ID、人员 ID 等不要写入仓库源码或 README。

可选环境变量：

| 变量 | 说明 |
|------|------|
| `TAPD_STATUS_SYNC_MAP` | JSON 状态映射，默认包含已提测、待上线、测试完成三项；设置后替换这三项映射。「测试中」固定触发本地配置更新 |
| `TAPD_SYNC_USER` | 显式指定同步用户；默认复用现有 TAPD 用户配置，必须唯一解析钉钉人员 |
| `TAPD_QA_TYPE_ID` | 测试子需求类型 ID；通常自动识别，有歧义时指定，仍会核验 QA 类型 |
| `TAPD_QA_OWNER` | 新建测试子需求的处理人；默认与同步配置用户相同 |
| `DINGTALK_AITABLE_FILTERS_JSON` | 附加的扁平 AND 筛选 JSON；与人员、状态条件取交集，不能扩大用户范围；字段和选项须使用实际 ID |

例如，显式设置与默认值相同的映射：

```powershell
$env:TAPD_STATUS_SYNC_MAP = '{"已提测":"开始测试","待上线":"测试完成","测试完成":"已上线"}'
```

仅需检查多维表记录、不访问 TAPD 时，可在加载配置后直接调用底层脚本：

```powershell
$env:PYTHONIOENCODING = "utf-8"
. .\.tapd\dingtalk_status_sync.ps1
python scripts\sync_dingtalk_status.py --source-only
```

### 执行结果与异常处理

重复执行会跳过已是目标状态的子需求。正式执行前会重新检查目标状态或子需求是否已存在，写后独立查询验证。遇到未确认的写入结果会停止后续操作，不自动重放写请求；已完成项保留在输出中，其余 `would_update` / `would_create` / `would_refresh_config` 表示尚未执行。

原有三种状态同步规则保持不变。配置回填以多维表为准：扫描配置用户负责的记录，**当多维表“任务进度”为“测试中”时，直接调用公共 `tapd-config-init` 更新对应需求的本地配置**。不要求先修改 TAPD 状态，也不再要求 TAPD 当前状态为测试中；“测试中”分支本身不创建或更新 TAPD 工作项。失败待重试项仅在本轮源表中仍属于该用户且为“测试中”时继续回填。

配置按父需求刷新处理人、版本、迭代和关联需求，保存在 `.tapd/story_configs/<workspace_id>/<parent_id>.json`，同一父需求只刷新一次。仅当该父需求与当前 `.tapd/project_config.json` 的关联一致时，才同时刷新全局配置，批量同步不会将全局关联切换到最后一条需求。预览不修改本地配置。

回填失败会保留 TAPD 已完成结果，并在 `config_refresh` 中报告；待刷新需求记录在 `.tapd/dingtalk_config_refresh_pending.json`，下次正式同步会重试配置回填，不重复创建或更新 TAPD。

创建前的意图保存在 `.tapd/dingtalk_status_sync_pending.json`。若创建结果未知且仍查不到子需求，后续运行会阻止再次创建；需要先在 TAPD 核实，确定没有创建后才能清理对应父需求的待核实条目。`.tapd/dingtalk_status_sync.lock` 防止本仓库同时执行多个同步进程；异常退出留下的锁也应在核实待创建记录后清理。这两个文件均不提交 Git。

### 自动同步（Windows）

配置并登录 DWS 后，在项目根目录执行一次，即可安装当前 Windows 用户的定时任务。默认每天北京时间 **19:00** 自动执行一次正式同步，不需要保持 PowerShell 窗口打开；电脑需要开机且该用户保持登录，锁屏不影响运行。安装脚本要求 Windows 使用无夏令时的 UTC+08:00 时区；不会修改系统时区。错过当天时间时不补跑，等待次日 19:00。

```powershell
powershell -ExecutionPolicy Bypass -File scripts\manage_dingtalk_status_sync.ps1 -Action Install
```

定时任务与手动 `-Apply` 使用同一套处理逻辑，每次自动加载本地表格和用户配置。多维表只读，「测试中」刷新本地配置，另外三种状态按规则处理 TAPD 测试子需求。同一任务不会重叠运行，不自动重放未知写入。不同仓库使用独立任务名称，重复安装会更新当前仓库的任务。可加 `-DailyAt "19:00"` 显式指定执行时间，加 `-WhatIf` 仅预览安装操作。修改执行时间后须重新安装任务；修改本地表格配置无需重新安装。

```powershell
# 查看运行状态
powershell -File scripts\manage_dingtalk_status_sync.ps1 -Action Status
# 暂停后续自动运行，已运行的同步可能继续完成
powershell -File scripts\manage_dingtalk_status_sync.ps1 -Action Pause
# 恢复自动运行
powershell -File scripts\manage_dingtalk_status_sync.ps1 -Action Resume
# 删除定时任务，保留本地配置和结果
powershell -File scripts\manage_dingtalk_status_sync.ps1 -Action Remove
```

最近一次运行的时间、退出码和结果保存在 `.tapd/dingtalk_status_sync_last_result.json`。返回结果失败时，根据其中的诊断修复配置或核实待创建记录；任务仍按计划运行。需要常年运行且不依赖个人电脑登录时，应部署到持续在线的运行环境，并单独准备该环境的 DWS 登录与 TAPD 配置。

---

# 第二部分：Agent Skills

## Skill 一览

| Skill | 用途 | 示例说法 |
|-------|------|----------|
| `tapd-config-init` | 初始化或升级项目配置 | 「初始化 TAPD 配置」 |
| `tapd-create-sub-stories` | 自动创建子需求并关联到父需求 | 「给这条需求补前后端子需求」 |
| `tapd-submit-bug` | 提交缺陷并处理图片、附件和需求关联 | 「帮我提个 bug」 |
| `tapd-link-bug-story` | 将缺陷关联到需求 | 「把这个 bug 关联需求」 |
| `tapd-close-bug` | 关闭或解决单个、多个缺陷 | 「把刚才的 bug 关掉」 |
| `tapd-bug-stats` | 统计缺陷并导出 Excel | 「统计本周未解决缺陷」 |
| `tapd-qa-status` | 查询或更新测试子需求状态 | 「我负责的测试子任务有未上线的吗」 |
| `tapd-plus` | 查询或更新 TAPD 通用实体 | 「查询当前迭代需求」 |

## 准备认证

从模板创建本地认证文件：

```powershell
Copy-Item .tapd\tapd_env.template.json .tapd\tapd_env.json
```

认证文件支持个人访问令牌，或 API 账号与密码。推荐使用权限范围最小、可定期轮换的令牌。

也可以通过环境变量提供认证信息。环境变量优先于本地配置文件。不要在 README、脚本参数、终端截图或日志中展示真实凭证。

## 初始化项目配置

用户只需提供需求标题、ID 或链接。脚本会关联该需求并写入 `link_story`，再按子需求回填测试 / 前端 / 后端处理人。在项目根目录执行公共脚本：

```powershell
python skills\tapd-config-init\scripts\init_tapd_config.py --story "需求标题"
```

不传需求时，脚本仍会从模板创建或补齐 `.tapd/project_config.json`。主要字段包括：

- `workspace_id`：TAPD 项目标识。
- `defaults`：提单人、版本、迭代、标签和标题前缀。
- `module_owner_map`：模块到负责人的映射。
- `module_field_map`：模块级自定义字段。
- `link_story`：自动关联需求的策略。
- `embed_images`：图片内嵌策略。
- `attachments`：附件上传策略。

项目名称、人员姓名、项目 ID、迭代名称等信息只应填写在本地配置中。

## 接入 Agent（Cursor / CodeBuddy / Claude Code）

公共实现和完整 Skill 文档统一存放在 `skills/`。飞书机器人直接调用公共脚本；`.tapd/` 负责本地配置和运行时状态。

| 目录 | 职责 |
|------|------|
| `skills/` | 唯一维护的 Skill 文档、执行脚本和参考资料 |
| `.cursor/skills/` | Cursor 发现入口，文档指向公共 Skill，旧脚本路径转发到公共实现 |
| `.codebuddy/skills/` | CodeBuddy 发现入口，文档指向公共 Skill，旧脚本路径转发到公共实现 |

可以删除 `.cursor/`、`.codebuddy/`，公共命令行和飞书机器人仍可运行。对应 IDE 的自动发现入口会随之移除。

接入 Claude Code 或其他 Agent 时，将完整的 `skills/` 内容复制到该 Agent 的 Skill 目录。跨项目复用时，复制完整公共 Skill 集合，保证同级 Skill 依赖可用，并在目标项目准备 `.tapd/` 配置。仓库中的 IDE 兼容入口依赖本仓库布局，不适合单独复制。

Skill 文档里的 `{baseDir}` 表示公共 Skill 根目录，例如 `skills/tapd-submit-bug/`。日常修改只维护 `skills/`，无需同步两套业务实现。

## 常见对话

- 「登录页面白屏，先生成缺陷草稿。」
- 「确认提交，并关联到匹配的需求。」
- 「统计本周我创建的未解决缺陷。」
- 「把指定缺陷标记为已解决。」
- 「查询当前迭代的需求。」

具体字段、匹配规则和命令参数见各 Skill 目录中的 `SKILL.md`。

---

# 本地文件与安全边界

| 文件或目录 | 用途 | 是否提交 |
|------------|------|----------|
| `.tapd/*.template.json` | 无真实值的配置模板 | 是 |
| `.tapd/tapd_env.json` | TAPD 认证信息 | 否 |
| `.tapd/project_config.json` | 项目和人员配置 | 否 |
| `.tapd/feishu_bot.json` | 飞书与 LLM 配置 | 否 |
| `.tapd/users/` | 用户标识和个人配置 | 否 |
| `.tapd/feishu_inbox/` | 消息图片缓存 | 否 |
| `.tapd/submit_state.json` | 提单幂等状态 | 否 |
| `*.log` | 本地日志 | 否 |

安全要求：

- 模板中的令牌、密码、密钥和应用标识必须保持为空或使用明显占位符。
- 不要提交本地配置、缓存、导出文件、日志或调试数据。
- 不要在日志中记录请求头、完整凭证、用户消息原文或个人标识。
- 机器人回显密钥时只能显示掩码，不显示完整值或可推断内容。
- 令牌应使用最小权限并定期轮换；疑似泄露时立即吊销。
- 提交前使用 `git status` 和 `git diff` 检查待提交内容。

# 目录结构

```text
skills/                 公共 TAPD Skills、执行脚本及参考资料
.cursor/skills/          Cursor 可选发现与兼容入口
.codebuddy/skills/       CodeBuddy 可选发现与兼容入口
scripts/feishu_bug_bot/  飞书机器人服务
.tapd/                   本地配置、模板和运行时状态（各 Agent 共用）
```

Python 缓存、日志、导出文件和 `.tapd/` 下的本地配置均应由 `.gitignore` 排除。
