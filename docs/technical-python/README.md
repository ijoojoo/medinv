# 药械进销存SaaS Python技术文档包

面向同时经营药品与医疗器械批发和零售的多企业SaaS。首期交付主体进销存及必要质量控制，AI、平台连接器、APP、小程序和简易HIS按后续阶段建设。

技术基线为Vue 3、TypeScript、CPython 3.14、FastAPI、SQLAlchemy 2.0、psycopg 3、Pydantic 2、Alembic及PostgreSQL 18。核心为模块化单体，显式事务，后台工作进程独立。文档版本Python 1.0，2026年10月9日。

## 文件索引

| 文件 | 用途 |
| --- | --- |
| 药械进销存SaaS Python技术设计文档.docx | 35页25章正式评审文档 含目录和部署图 |
| 药械进销存SaaS Python技术设计文档.md | 与Word同内容的维护源 |
| 数据字典.md | 24张核心SQL表 46组其余业务逻辑对象 8组Python运行时细化 |
| 接口登记表.md | 85项分期接口 权限和细化状态 |
| database/core-reference.sql | PostgreSQL核心切片 RLS 复合外键 追加事实保护 |
| contracts/core-openapi.json | 11项核心操作及33个Schema |
| contracts/domain-event.schema.json | 事件信封及库存事件载荷 |
| contracts/examples | 7份请求 响应和事件示例 |
| Python开发规范.md | 包目录 代码边界 DTO 精度 事务和日常检查 |
| examples/python | 事务单元及Decimal参考片段 不是完整应用 |
| 性能压测方案.md | 数据规模 连接预算 10类场景 测量与验收 |
| 测试矩阵.md | 原30项验收 24项技术测试及26项Python专项测试 |
| 部署与运行手册.md | 配置 发布 故障 灾备及首次营业 |
| 开发任务清单.md | 首六周PY工作包和后续业务里程碑 |
| 版本切换说明.md | 原方案与Python方案对应关系 |
| 验证范围.md | 本次交付实际检查和未执行边界 |
| manifest.json | 交付文件哈希与版本信息 |
| assets/technical-architecture.png | 主文档架构图 |

## 阅读及实施顺序

1. 阅读版本切换说明和主文档01至06章，确认范围、身份及数据隔离。
2. 阅读主文档08至16章和23章，评审库存、成本、付款恢复、幂等和Python事务边界。
3. 按开发规范创建项目，先锁定依赖与镜像，再构建真实数据库核心切片。
4. 接口登记表中的待细化接口在模块迭代前补齐OpenAPI，不把登记表当作已经存在的服务。
5. 按测试矩阵、性能压测方案和部署手册验证后再试点。

## 覆盖与限制

本包用于接下来的开发，不是已运行系统。SQL仅覆盖24表核心切片，没有完整采购、处方、财务、身份或控制面迁移；其余对象在数据字典中定义。参考SQL不包含自动完成所有业务校验的存储过程。

Python参考片段不包含完整鉴权、过账、幂等和重试实现；uv.lock、完整Alembic迁移、API和Locust脚本在对应研发任务交付。本包没有生产凭证、真实交易或第三方推测endpoint。

公开技术资料引用位于主文档相关段落及25章。容量、时延、恢复时间和AI准确率是设计与验收目标，不是实测能力。原Java文档完整保留，Python目录为新的实施基线。

