---
name: tapd-config-init
description: 初始化或升级 TAPD 项目本地配置文件（.tapd/project_config.json）。当用户首次接入 TAPD、需要生成本地配置、或模板新增字段需补齐缺失项时使用。
---

# TAPD Config Init Skill

用于初始化与升级 TAPD 项目本地配置文件（`.tapd/project_config.json`）。

## 何时使用

- 首次接入 TAPD Skill，需要快速生成本地配置文件。
- 模板新增字段后，需要把缺失字段自动补齐到本地配置。
- 需要保证升级过程不覆盖用户已有配置值。

## 默认路径

- 项目模板：`.tapd/project_config.template.json` → 本地 `.tapd/project_config.json`
- 认证模板：`.tapd/tapd_env.template.json` → 本地 `.tapd/tapd_env.json`（**勿提交**）

本地文件用途：

| 文件 | 内容 |
|------|------|
| `project_config.json` | `workspace_id`、提单默认值（含 `defaults.name`）、`module_owner_map`（`module` 由用户或 AI 高置信写入，无 defaults 兜底） |
| `tapd_env.json` | `access_token`（或 API 账号密码）、可选 `current_user_nick` |

填写 `tapd_env.json` 后，**无需在 Windows CMD 中 setx 环境变量**；各 TAPD Skill 脚本启动时自动加载。

## 执行方式（直接执行）

```powershell
python ".cursor/skills/tapd-config-init/scripts/init_tapd_config.py"
```

可选参数：

```powershell
python ".cursor/skills/tapd-config-init/scripts/init_tapd_config.py" `
  --template ".tapd/project_config.template.json" `
  --target ".tapd/project_config.json"
```

## 行为规则

1. 若目标配置不存在：直接按模板创建。
2. 若目标配置已存在：递归补齐缺失 key。
3. 已有 key 永不覆盖（包括基础字段和嵌套字段）。
4. 输出 JSON 结果，包含：
   - `status`：`created` / `merged` / `skipped`（项目配置）
   - `template_path` / `target_path` / `added_keys`
   - `tapd_env_status` / `tapd_env_path` / `tapd_env_added_keys`（认证配置）

## 结果判定

- `created`：首次创建成功。
- `merged`：已有文件，本次补齐了缺失字段。
- `skipped`：已有文件且无缺失字段（幂等）。
