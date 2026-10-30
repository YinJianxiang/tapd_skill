---
name: tapd-create-sub-stories
description: >-
  自动创建子需求，自动关联需求。在 TAPD 父需求下补齐前端子需求、后端子需求、测试子需求，
  并用 parent_id 挂到父需求。已存在的角色跳过。当用户要求创建子需求、拆分子需求、
  补前端/后端/测试子需求、自动关联需求到父需求时使用。需 .tapd/tapd_env.json 与 project_config.json。
---

# TAPD 自动创建子需求，自动关联需求

定位一条父需求，按项目需求类别创建缺失的 **前端 / 后端 / 测试** 子需求，创建时写入 `parent_id` 完成父子关联。

父子关联走 TAPD 需求树（`parent_id`），**不是** `POST relations`。缺陷关联需求请用 `tapd-link-bug-story`。

## 何时使用

- 自动创建子需求，自动关联需求
- 给某条需求补前端子需求 / 后端子需求 / 测试子需求
- 拆分子需求、批量建子需求

## 必要输入

用户给出父需求标题、ID 或 TAPD 链接即可。

可选：

| 字段 | 说明 |
|------|------|
| `roles` | 要创建的角色，默认 `前端,后端,测试` |
| `owners` / `frontend_owner` / `backend_owner` / `qa_owner` | 各角色处理人 |
| `dry_run` | 只预览不创建 |

处理人优先级：payload 显式指定 > `project_config.json`（`module_owner_map.前端/后端`、`defaults.reporter` 作测试）> 空。处理人为空仍创建。

## 标准流程

1. 解析父需求（命中子需求时上溯到父需求）。
2. `GET workitem_types` 匹配 前端/后端/测试 类别。
3. `GET stories?parent_id=` 看已有子需求；已有角色 **跳过**。
4. 对缺失角色 `POST stories`：`name` 与父需求相同，`parent_id` = 父需求 ID，带上对应 `workitem_type_id`；父需求有迭代则带上 `iteration_id`。
5. 返回每条 `created` / `skipped_exists` / `failed` 及可点击链接。

对话约定：用户已明确父需求时，先 `--dry-run` 展示将创建/跳过的角色与处理人，用户确认后再去掉 `--dry-run`。用户说「直接创建」可跳过预览。

## 命令

```powershell
$env:PYTHONIOENCODING = "utf-8"
@'
{
  "story": "分销剧单接入分销漫剧打专辑链接环节",
  "roles": ["前端", "后端", "测试"]
}
'@ | Set-Content -Encoding UTF8 ".tapd/create_sub_stories.json"

python "{baseDir}/scripts/create_sub_stories.py" `
  --payload-file ".tapd/create_sub_stories.json" `
  --dry-run
```

指定处理人：

```powershell
python "{baseDir}/scripts/create_sub_stories.py" `
  --story-id "1166882899001023886" `
  --frontend-owner "赵学亮" `
  --backend-owner "马艳" `
  --qa-owner "齐佳乐"
```

## 结果

| `children[].action` | 含义 |
|---------------------|------|
| `would_create` | dry-run 将创建 |
| `created` | 已创建并挂到父需求 |
| `skipped_exists` | 该角色已有子需求 |
| `failed` | 该条创建失败 |

创建完成后如需把处理人写回本地配置，再跑 `tapd-config-init --story-id <父需求ID>`。
