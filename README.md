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

---

# 第二部分：Agent Skills

## Skill 一览

| Skill | 用途 | 示例说法 |
|-------|------|----------|
| `tapd-config-init` | 初始化或升级项目配置 | 「初始化 TAPD 配置」 |
| `tapd-submit-bug` | 提交缺陷并处理图片、附件和需求关联 | 「帮我提个 bug」 |
| `tapd-link-bug-story` | 将缺陷关联到需求 | 「把这个 bug 关联需求」 |
| `tapd-close-bug` | 关闭或解决单个、多个缺陷 | 「把刚才的 bug 关掉」 |
| `tapd-bug-stats` | 统计缺陷并导出 Excel | 「统计本周未解决缺陷」 |
| `tapd-plus` | 查询或更新 TAPD 通用实体 | 「查询当前迭代需求」 |

## 准备认证

从模板创建本地认证文件：

```powershell
Copy-Item .tapd\tapd_env.template.json .tapd\tapd_env.json
```

认证文件支持个人访问令牌，或 API 账号与密码。推荐使用权限范围最小、可定期轮换的令牌。

也可以通过环境变量提供认证信息。环境变量优先于本地配置文件。不要在 README、脚本参数、终端截图或日志中展示真实凭证。

## 初始化项目配置

运行初始化脚本：

```powershell
python .cursor\skills\tapd-config-init\scripts\init_tapd_config.py
```

脚本会从模板创建或补齐 `.tapd/project_config.json`。主要字段包括：

- `workspace_id`：TAPD 项目标识。
- `defaults`：提单人、版本、迭代、标签和标题前缀。
- `module_owner_map`：模块到负责人的映射。
- `module_field_map`：模块级自定义字段。
- `link_story`：自动关联需求的策略。
- `embed_images`：图片内嵌策略。
- `attachments`：附件上传策略。

项目名称、人员姓名、项目 ID、迭代名称等信息只应填写在本地配置中。

## 接入 Cursor

本仓库已经包含 `.cursor/skills/`。使用 Cursor 打开仓库后，可直接在 Agent 对话中描述任务。

如需在其他项目使用，可将所需 Skill 复制到：

```text
<项目根目录>/.cursor/skills/<skill-name>/
```

也可以安装到个人 Skill 目录。无论采用哪种方式，都应在包含本地 `.tapd/` 配置的项目根目录中运行。

## 接入 Claude Code

将 Skills 复制到项目目录：

```powershell
New-Item -ItemType Directory -Force .claude\skills | Out-Null
Copy-Item -Recurse .cursor\skills\* .claude\skills\
```

然后在项目根目录启动 Claude Code，并通过自然语言或 Skill 名称调用。

## 接入 CodeBuddy

仓库中的 `.codebuddy/skills/` 为 CodeBuddy 使用的 Skill 副本。配置方式与其他 Agent 相同，并共用项目根目录下的本地 `.tapd/` 配置。

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
.cursor/skills/          Cursor Skills
.codebuddy/skills/       CodeBuddy Skills
scripts/feishu_bug_bot/  飞书机器人服务
.tapd/                   本地配置、模板和运行时状态
```

Python 缓存、日志、导出文件和 `.tapd/` 下的本地配置均应由 `.gitignore` 排除。
