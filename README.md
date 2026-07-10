# TAPD Skills

基于 TAPD 开放 API 的自动化工具集，供 Cursor / Codebuddy Agent 在对话中完成缺陷提单、关单、统计、关联需求等操作。

## 功能

| Skill | 说明 |
|-------|------|
| `tapd-plus` | 通用 TAPD API：需求、任务、缺陷、迭代、评论、工时等 |
| `tapd-submit-bug` | 提交缺陷，支持截图/日志附件与自动关联需求 |
| `tapd-close-bug` | 关闭或解决缺陷（单个/批量） |
| `tapd-link-bug-story` | 将缺陷与需求（story）建立关联 |
| `tapd-bug-stats` | 按时间、负责人、平台等统计缺陷并导出 Excel |
| `tapd-config-init` | 初始化或升级本地项目配置 |

## 快速开始

### 1. 配置认证

复制模板并填写 TAPD 凭证：

```powershell
copy .tapd\tapd_env.template.json .tapd\tapd_env.json
```

在 `tapd_env.json` 中填入 `access_token`（推荐），或 `api_user` + `api_password`。

### 2. 初始化项目配置

```powershell
python .cursor\skills\tapd-config-init\scripts\init_tapd_config.py
```

编辑 `.tapd/project_config.json`，填写 `workspace_id`、提单默认值、模块负责人等。

### 3. 在 Cursor 中使用

配置完成后，直接在对话中描述需求即可，例如：

- 「帮我提一个 bug，截图在桌面」
- 「统计本周未解决的缺陷」
- 「把 bug 12345 关了」

Agent 会根据对话内容自动加载对应 Skill 并执行脚本。

## 目录结构

```
.cursor/skills/          # Cursor Agent Skills
.codebuddy/skills/       # Codebuddy 同步副本
.tapd/                   # 本地配置与运行时状态（部分文件勿提交）
scripts/                 # 其他辅助脚本
```

## 注意事项

- `.tapd/tapd_env.json` 和 `.tapd/project_config.json` 含敏感信息，已加入 `.gitignore`，请勿提交。
- 脚本仅依赖 Python 标准库，无需额外安装依赖。
- 环境变量（如 `TAPD_ACCESS_TOKEN`）优先级高于本地配置文件。
