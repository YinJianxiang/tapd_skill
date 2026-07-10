---
name: tapd-config-init
description: 初始化或升级 TAPD 项目本地配置文件（.tapd/project_config.json）。当用户首次接入 TAPD、需要生成本地配置、模板新增字段需补齐缺失项、或显式更新 defaults/module_owner_map 等已有字段时使用。
---

# TAPD Config Init Skill

用于初始化与升级 TAPD 项目本地配置文件（`.tapd/project_config.json`）。

## 何时使用

- 首次接入 TAPD Skill，需要快速生成本地配置文件。
- 模板新增字段后，需要把缺失字段自动补齐到本地配置。
- 需要**更新已有配置值**（如 `defaults.name`、`module_owner_map.后端`）。
- 需要保证「模板补齐」过程不意外覆盖未指定的字段。

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

### 1. 仅初始化 / 补齐模板缺失字段（不覆盖已有值）

```powershell
python ".cursor/skills/tapd-config-init/scripts/init_tapd_config.py"
```

### 2. 用 payload 文件更新指定字段（会覆盖）

```powershell
python ".cursor/skills/tapd-config-init/scripts/init_tapd_config.py" `
  --payload-file ".tapd/config_update.json"
```

`config_update.json` 示例：

```json
{
  "defaults": {
    "name": "批创"
  },
  "module_owner_map": {
    "后端": "曹慧斌"
  }
}
```

### 3. 用 `--set` 快速更新（会覆盖）

```powershell
python ".cursor/skills/tapd-config-init/scripts/init_tapd_config.py" `
  --set "defaults.name=批创" `
  --set "module_owner_map.后端=曹慧斌"
```

`--set` 的 VAL 支持 JSON 字面量，例如：

```powershell
--set 'module_owner_map={"前端":"马超","后端":"曹慧斌"}'
```

### 4. 同时补齐模板 + 更新字段

先按模板补缺失 key，再应用 `--payload-file` / `--set` 覆盖指定字段（推荐顺序）。

```powershell
python ".cursor/skills/tapd-config-init/scripts/init_tapd_config.py" `
  --payload-file ".tapd/config_update.json"
```

### 5. 更新 tapd_env.json（可选）

```powershell
python ".cursor/skills/tapd-config-init/scripts/init_tapd_config.py" `
  --env-payload-file ".tapd/tapd_env_update.json"
```

可选路径参数：

```powershell
python ".cursor/skills/tapd-config-init/scripts/init_tapd_config.py" `
  --template ".tapd/project_config.template.json" `
  --target ".tapd/project_config.json"
```

## 对话里怎么传参

`/tapd-config-init` **不会**自动解析「需求 / 前端 / 后端」为配置字段。Agent 应：

1. 把要改的字段写成 JSON payload 文件（推荐放 `.tapd/config_update.json`）。
2. 调用脚本并带上 `--payload-file` 或 `--set`。

示例（用户说：后端曹慧斌，标题前缀批创）：

```powershell
$env:PYTHONIOENCODING = "utf-8"
@'
{
  "defaults": { "name": "批创" },
  "module_owner_map": { "后端": "曹慧斌" }
}
'@ | Set-Content -Encoding UTF8 ".tapd/config_update.json"

python ".cursor/skills/tapd-config-init/scripts/init_tapd_config.py" `
  --payload-file ".tapd/config_update.json"
```

可更新字段示例：

| 用户意图 | payload 路径 |
|---------|-------------|
| 标题前缀 | `defaults.name` |
| 提单人 | `defaults.reporter` |
| 迭代/版本 | `defaults.iteration` / `defaults.version` |
| 后端负责人 | `module_owner_map.后端` |
| 前端负责人 | `module_owner_map.前端` |
| 空间 ID | `workspace_id` |

**注意**：`defaults.module` 不建议写入配置（提单 Skill 要求 module 由用户或 AI 高置信指定，无 defaults 兜底）。

## 行为规则

1. 若目标配置不存在：直接按模板创建。
2. 若目标配置已存在：递归补齐模板中**缺失** key（不覆盖已有值）。
3. 若提供 `--payload-file` 或 `--set`：对 payload 中出现的字段**递归合并并覆盖**。
4. `--set` 与 `--payload-file` 可同时使用；同路径时 `--set` 后写入的覆盖前者。
5. 输出 JSON 结果，包含：
   - `status`：见下文
   - `added_keys`：模板补齐新增的路径
   - `updated_keys`：payload 实际变更的路径
   - `tapd_env_status` / `tapd_env_added_keys` / `tapd_env_updated_keys`

## 结果判定

| status | 含义 |
|--------|------|
| `created` | 首次按模板创建 |
| `merged` | 仅补齐模板缺失字段 |
| `updated` | 仅 payload 更新了已有字段 |
| `merged_and_updated` | 既补齐缺失字段，又更新了指定字段 |
| `created_and_updated` | 首次创建后立即应用 payload 更新 |
| `skipped` | 无缺失字段且 payload 无变更 |

## 与提单 Skill 的分工

| 场景 | 用 config-init | 用 submit-bug payload |
|------|----------------|----------------------|
| 改默认后端负责人 | ✅ `module_owner_map.后端` | 单次可传 `current_owner` |
| 改默认标题前缀 | ✅ `defaults.name` | 单次可传 `name` |
| 关联某次需求 | ❌ | ✅ `story_id` / `story_name` |
| 本次缺陷描述 | ❌ | ✅ `title` / `description` |
