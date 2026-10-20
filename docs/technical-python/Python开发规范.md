# Python开发规范

本文件是开发执行清单，核心规则见主文档第23章；examples/python只提供可审查的类型和事务参考，不是完整应用。

## 仓库目录

    apps/
      admin-web/
      pos-web/
    packages/
      ui/
      api-client/
      domain-types/
    backend/
      pyproject.toml
      uv.lock
      .python-version
      src/medinv/
        entrypoints/
          core_api.py
          control_api.py
          business_worker.py
        platform/
          auth/
          tenancy/
          database/
          observability/
          storage/
        modules/
          catalog/
          quality/
          procurement/
          inventory/
          costing/
          wholesale/
          retail/
          prescription/
          settlement/
          traceability/
          reporting/
          jobs/
          audit/
      migrations/
        business/
        control/
      tests/
        unit/
        integration/
        contracts/
        architecture/
    workers/
      ai/
      connectors/
    contracts/
      openapi/
      events/
    deploy/
    docs/adr/

后续workers目录按阶段创建，不要求首期装入模型或渠道SDK。命名使用snake_case；类型、命令、事件有明确含义；业务方法不返回裸dict拼装含糊结构，外部DTO由类型定义控制。

## 应用调用约定

Router解析请求和协议头，认证依赖返回不可变身份上下文。应用服务在def调用内进入TenantUnitOfWork.open，执行授权、幂等、锁定、领域计算、事实写入、DTO物化；离开上下文成功提交后才返回HTTP结果。

多个模块共享一个应用服务持有的UnitOfWork。Repository只接受当前Session，不拥有Engine，不得调用commit、rollback或close。后台任务创建自己的UnitOfWork，不携带HTTP请求Session。返回DTO后不得再访问ORM懒加载属性。

领域对象使用dataclass和Enum，金额数量用Decimal。核心代码开启mypy strict；Any只允许经过校验的第三方边界且有注释。异常类型携带稳定错误码，HTTP映射在统一异常处理层完成。

## 参考文件

examples/python/tenant_uow.py展示同连接事务局部租户设置、只读事务及显式提交边界。它没有实现会话认证、对象授权、幂等、重试、库存锁算法或审计，不能单独用于生产过账。

examples/python/decimal_values.py展示拒绝JSON数字、超精度和非有限值的输入函数，及数量规范化。负数量流水、退款、补差分别使用允许有符号值的内部类型；不得直接放宽外部销售数量。money_for_posting只在确定的入账舍入边界调用，不应每一步中间计算都舍入。

## Pydantic与契约

请求模型使用ConfigDict(extra="forbid")。将严格字符串验证器接在Decimal解析之前；为入参明确声明type=string、pattern、maxLength及examples。DTO支持的格式必须与contracts/core-openapi.json一致，不要默认接受科学计数法、浮点数或未知字段。

decimal输出定义field_serializer返回固定小数位字符串，UUID及bigint版本输出字符串。错误响应禁止回显Pydantic原始input中的敏感数据。生成OpenAPI时显式补齐业务错误、安全Cookie和CSRF及幂等头。

设计契约是公开接口基线；FastAPI生成契约用于检测实现差异。CI比较operationId、请求和响应schema、必填性、格式、错误状态、安全定义及样例。不能仅设置openapi_version字符串便认为已符合3.1.1规范。

## 数据库访问

使用SQLAlchemy Core或text绑定参数，SQL文本常量放在本模块infrastructure。排序列用枚举映射；批量IN设大小限制，不能允许任意字段名和SQL片段。余额与预占写入检查RETURNING结果，锁取得顺序按主文档统一。

成本账、库存账、审计和Outbox追加事实禁止UPDATE或DELETE。实体档案软停用和事实更正分别建模。任何新租户表必须补tenant_id、RLS、复合外键和对应负向测试。

## 日常开发命令约定

首次由维护者锁定依赖，后续开发和CI使用uv sync --locked。基础检查为uv run ruff check .、uv run ruff format --check .、uv run mypy src、uv run pytest；依赖方向检查使用所锁import-linter版本命令。

数据库测试必须连接PostgreSQL 18，不能改用SQLite求快；迁移、RLS、并发与故障恢复的测试环境使用runtime和migration两个不同角色。pytest并行测试使用独立数据库或互不干扰的租户与数据，锁竞争用例显式协调执行次序。

Alembic的business与control环境分别运行upgrade head，命令参数与配置文件在建仓时冻结。autogenerate生成后必须审查，禁止CI发现差异便自动修改生产。应用启动只检查当前revision属于支持集合。

## 合并和完成标准

每个变更说明业务行为、事务边界、幂等键、权限范围、迁移兼容、故障恢复及相关验收证据。涉及核心交易时，补相应并发正反例；纯样式调整不要求制造业务测试。

必须拒绝的代码包括float金额、字符串拼接SQL、全局Session、async中阻塞数据库、事务内外部调用、API内不可恢复后台任务、未授权跨租户查询，以及一次异常捕获后继续使用失败事务。
