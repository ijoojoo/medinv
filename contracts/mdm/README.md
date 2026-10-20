# 商品与服务主数据设计目录

`product-master-catalog.v1.json` 是2026-10-18的设计资料，不是自动加载到运行程序的规则种子或数据库迁移。

- `fields`：用户参考的449项定义，`source_record`保留原文；`implementation`为本项目的对象、卡片、权限、阶段和旧代码映射。
- `additional_fields`：44项补充逻辑字段，包含保留的经营能力以及映射与治理细项。
- `templates`及`label_mappings`：21个基础模板、3个叠加属性和297条分类名称映射。
- `reference_rules`、`reference_entities`、`reference_enums`及`examples`：原参考内容，未直接作为代码执行。
- `implementation_rules`：22条项目实施约束；`rule_language.examples`仅示范受控规则表达方式，没有运行时解释器。
- `sources`：原依据与本轮核验范围；`review_status`不是所有法规全文有效性的保证。
- `runtime_baseline`：本轮未改变运行库；现有商品API仍接受DRUG/DEVICE。

采用某条规则开发前，应将自然语言条件解析为受控表达式或明确的领域代码，补足类型/关系/有效期约束，核验适用法源，并完成相应业务验收。禁止把449条逻辑字段逐条机械增加为product表列，也禁止把参考中的“P0”直接当成本项目近期全部开发任务。

完整方案见[MDM主数据与字段设计](../../docs/technical-python/MDM主数据与字段设计.md)。
