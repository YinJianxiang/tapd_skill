---
name: tapd-create-sub-stories
description: >-
  自动创建子需求，自动关联需求。在 TAPD 父需求下补齐前端子需求、后端子需求、测试子需求，
  并用 parent_id 挂到父需求。已存在的角色跳过。当用户要求创建子需求、拆分子需求、
  补前端/后端/测试子需求、自动关联需求到父需求时使用。需 .tapd/tapd_env.json 与 project_config.json。
---

读取并遵循公共 Skill 文档：

[SKILL.md](../../../skills/tapd-create-sub-stories/SKILL.md)。

公共文档中的 `{baseDir}` 指向项目根目录下的 `skills/tapd-create-sub-stories/`。执行其中的脚本和读取参考资料时，以该公共目录为基准。
