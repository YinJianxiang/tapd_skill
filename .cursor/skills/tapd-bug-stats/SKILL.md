---
name: tapd-bug-stats
description: 按时间、创建人、软件平台、简单问题标签统计 TAPD 缺陷数量与明细，导出 Excel 并在对话中展示 bug 列表。当用户要求 bug 统计、本周 bug、未解决缺陷、按负责人/平台分布或查看明细时使用。
---

# TAPD Bug Stats Skill

按条件筛选 TAPD 缺陷，输出 **Excel 报告** + **对话明细列表**。

## 何时使用

- 按时间段统计 bug（本周、本月、自定义日期）
- 按**创建人**筛选
- 按**软件平台**（前端/后端等）汇总
- 按**简单问题**标签统计/筛选
- 查看未解决 bug 及明细
- 导出 Excel

## 环境与配置

| 文件 | 用途 |
|------|------|
| `.tapd/tapd_env.json` | 认证 |
| `.tapd/project_config.json` | `workspace_id` |

依赖 `tapd-plus/scripts/tapd_client_stdlib.py`。Agent **直接执行**脚本，用户无需开 CMD。

## 执行方式（必做）

```powershell
python ".cursor/skills/tapd-bug-stats/scripts/bug_stats_report.py" `
  --start-date 2026-06-08 `
  --end-date 2026-06-11 `
  --open-only `
  --reporter 殷健翔
```

### 筛选参数

| 用户意图 | 参数 |
|---------|------|
| 时间段（创建时间） | `--start-date` + `--end-date` |
| 未解决 / 遗留 | `--open-only` |
| **已解决未关闭** | `--include-status resolved` |
| 仅排除已关闭 | `--exclude-status closed`（与 `--open-only` 互斥） |
| **创建人** | `--reporter` 或 `--creator`（昵称模糊匹配，逗号分隔多人） |
| **软件平台** | `--platform`（如 `前端`、`后端`，逗号分隔） |
| **仅简单问题** | `--simple-label only` |
| 排除简单问题 | `--simple-label exclude` |
| 无标签 | `--simple-label no_label` |
| 主维度聚合 | `--group-by module_owner`（默认） |

`--group-by` 可选：`module` / `owner` / `module_owner` / `platform` / `simple_label`

### 简单问题判定

- 读取 TAPD 字段 `label`
- 标签含 **`简单问题`**（`|` 分隔多标签）即视为简单问题
- 无 `label` → 归类为「无标签」；有标签但无「简单问题」→「非简单问题」

### 软件平台

- 读取 TAPD 字段 `platform`；为空时回退 `module`

## 输出

JSON stdout 含：`brief`、`summary`（含 `by_platform`、`by_simple_label`、`simple_count`）、`bugs`、`platform_stats`、`simple_label_stats`、`xlsx_path`

### Excel 工作表

| 工作表 | 内容 |
|--------|------|
| `stats` | 主 `--group-by` 聚合 |
| `软件平台` | 按 platform 汇总 |
| `简单问题` | 简单问题 / 非简单问题 / 无标签 |
| `raw` | 明细（含 reporter、platform、label、is_simple） |

### 对话回执

1. 贴 `brief`（含平台分布、标签分布、简单问题数）
2. 用户要看明细时，用表格列出 `bugs`（≤30 条全列，超出指 Excel raw）

明细表列：标题、状态、创建人、软件平台、标签、是否简单问题、负责人、创建时间、链接

## 命令示例

```powershell
# 殷健翔本周创建的未解决 bug
python ".cursor/skills/tapd-bug-stats/scripts/bug_stats_report.py" `
  --start-date 2026-06-08 --end-date 2026-06-11 `
  --open-only --reporter 殷健翔

# 后端平台 + 仅简单问题
python ".cursor/skills/tapd-bug-stats/scripts/bug_stats_report.py" `
  --platform 后端 --simple-label only

# 按软件平台作为主 stats 维度
python ".cursor/skills/tapd-bug-stats/scripts/bug_stats_report.py" `
  --group-by platform --open-only
```

## 统计口径

- **未解决**：排除 `closed`、`resolved`
- **时间**：bug 字段 `created` 落在日期范围内
