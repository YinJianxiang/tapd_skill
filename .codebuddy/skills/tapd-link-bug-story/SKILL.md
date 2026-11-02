---
name: tapd-link-bug-story
description: >-
  将 TAPD 缺陷与需求（story）建立关联：支持显式指定 story_id、按需求标题匹配，或根据缺陷标题/描述自动匹配当前迭代需求并调用 POST relations。
  当用户要求关联需求、绑定 story、补关联需求、bug 关联需求、自动匹配需求，或在提 bug 后补充「关联需求」时使用。
  为 tapd-submit-bug 的配套子 Skill。需配置 .tapd/tapd_env.json 与 project_config.json。
---

读取并遵循公共 Skill 文档：

[SKILL.md](../../../skills/tapd-link-bug-story/SKILL.md)。

公共文档中的 `{baseDir}` 指向项目根目录下的 `skills/tapd-link-bug-story/`。执行其中的脚本和读取参考资料时，以该公共目录为基准。
