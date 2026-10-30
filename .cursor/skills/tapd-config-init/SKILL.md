---
name: tapd-config-init
description: >-
  初始化或升级 TAPD 项目本地配置（.tapd/project_config.json）。用户只需给出需求标题、ID 或链接：
  脚本关联该需求并写入 link_story，再按子需求类型回填测试处理人（reporter）、前端/后端处理人。
  当用户首次接入 TAPD、初始化配置、根据需求生成配置、补齐模板字段，或显式更新 defaults/module_owner_map 时使用。
---

# TAPD Config Init Skill

用于初始化与升级 `.tapd/project_config.json`。**推荐入口：用户只提供一条需求。**

## 何时使用

- 用户说：初始化 TAPD 配置、根据需求初始化、关联需求到配置、配置这个需求。
- 首次接入 TAPD Skill，需要生成本地配置。
- 模板新增字段后，补齐本地缺失项。
- 显式更新 `defaults.name`、`module_owner_map` 等已有字段。

## 默认路径

- 项目模板：`.tapd/project_config.template.json` → 本地 `.tapd/project_config.json`
- 认证模板：`.tapd/tapd_env.template.json` → 本地 `.tapd/tapd_env.json`（**勿提交**）

| 文件 | 内容 |
|------|------|
| `project_config.json` | `workspace_id`、`defaults`、`module_owner_map`、`link_story` |
| `tapd_env.json` | `access_token`（或 API 账号密码）、可选 `current_user_nick` |

`workspace_id` 必须已在 `project_config.json` 中（或由需求链接解析）。认证从 `tapd_env.json` 加载。

## 标准流程（用户只输入需求）

用户给出需求标题 / ID / TAPD 链接后：

1. **Agent 先语义简化标题**为短 `defaults.name`（提单【name】前缀），再调脚本。  
   - 保留业务含义，去掉「需求」等套话，可改写成领域惯用简称。  
   - 例：`客户端付费小说半自动投放需求` → `客户端付费小说半自动批创`。  
   - **禁止**用固定后缀表/死映射代替总结。
2. 执行脚本，并用 `--set` / payload 传入总结后的 name（PowerShell 中文请用 payload 文件）：

```powershell
$env:PYTHONIOENCODING = "utf-8"
@'
{
  "story": "客户端付费小说半自动投放需求",
  "defaults": { "name": "客户端付费小说半自动批创" }
}
'@ | Set-Content -Encoding UTF8 ".tapd/config_update.json"

python "{baseDir}/scripts/init_tapd_config.py" --payload-file ".tapd/config_update.json"
```

或：

```powershell
python "{baseDir}/scripts/init_tapd_config.py" `
  --story-id "1166882899001023559" `
  --set "defaults.name=客户端付费小说半自动批创"
```

脚本会：

1. 按模板创建或补齐 `project_config.json`（不覆盖未指定的已有值）。
2. 定位需求（精确标题优先；命中子需求时自动上溯到父需求）。
3. 写入 `link_story.story_id` / `link_story.story_name`，并开启 `link_story.enabled`。
4. 查询该需求的子需求，按工作项类型判断 **前端 / 后端 / 测试**：
   - 测试子需求处理人 → `defaults.reporter`
   - 前端子需求处理人 → `module_owner_map.前端`
   - 后端子需求处理人 → `module_owner_map.后端`
5. 应用 `--set` / payload（含 Agent 传入的 `defaults.name`）。
6. **没有对应子需求，或处理人为空：对应字段写成空字符串**（覆盖旧值）。

多条同名需求时返回 `status=need_confirm` 与候选列表，**不写需求相关字段**。请用户改用 `story_id` 后重跑。

## 子需求判定

优先用 TAPD 需求类别（`workitem_type`），其次看标题关键字：

| 角色 | 类别信号 | 写入字段 |
|------|----------|----------|
| 测试 | 名称含「测试」，或 english_name=`QA` | `defaults.reporter` |
| 前端 | 名称含「前端」，或 english_name=`FE` | `module_owner_map.前端` |
| 后端 | 名称含「后端」，或 english_name=`BE` | `module_owner_map.后端` |

同一角色多条子需求时，取第一个非空处理人（TAPD `owner` 按 `;` 拆分后取第一人）。

## 对话约定

1. 用户已给出需求：先语义简化出 `defaults.name`，再跑脚本并传入；把 `story_sync` 与写入的 name / reporter / 前端 / 后端展示给用户。
2. 用户只说「初始化」但没给需求：先模板补齐，再问需求标题或链接。
3. 不要让用户手填前端/后端/测试负责人；这些由子需求处理人决定。
4. 不要根据父需求处理人猜测前后端负责人。
5. `defaults.module` 不要写入配置。
6. `defaults.name` 必须由 Agent 按标题语义简化后传入；脚本不同步、不做后缀死映射。

## 其它更新方式

模板补齐（不覆盖已有值）：

```powershell
python "{baseDir}/scripts/init_tapd_config.py"
```

`--set` / payload 在需求同步**之后**应用，可覆盖脚本回填的字段：

```powershell
python "{baseDir}/scripts/init_tapd_config.py" `
  --story "需求标题" `
  --set "defaults.name=批创"
```

## 结果字段

| 字段 | 说明 |
|------|------|
| `status` | `created` / `merged` / `updated` / `merged_and_updated` / `created_and_updated` / `skipped` / `need_confirm` / `not_found` |
| `updated_keys` | 实际写入的配置路径 |
| `story_sync.story` | 命中的父需求 |
| `story_sync.children` | 子需求及识别角色 |
| `story_sync.owners` | 回填的 reporter / 前端 / 后端 |

## 与提单 Skill 的分工

| 场景 | 用 config-init | 用 submit-bug |
|------|----------------|---------------|
| 按需求初始化关联与处理人 | ✅ `--story` | ❌ |
| 标题前缀 `defaults.name` | ✅ Agent 语义简化后 `--set` / payload | 单次可传 `name` |
| 本次缺陷描述 | ❌ | ✅ `title` / `description` |

## 需求链接同步版本与迭代

当输入需求链接、需求 ID 或需求标题时，脚本会读取根需求的 version 与 iteration_id：

- defaults.version 自动写入需求的版本名称。
- 根据 iteration_id 调用 TAPD GET iterations 获取迭代名称，并写入 defaults.iteration。
- 若需求未设置版本或迭代，目标字段会写为空字符串，避免沿用上一次需求的配置。
