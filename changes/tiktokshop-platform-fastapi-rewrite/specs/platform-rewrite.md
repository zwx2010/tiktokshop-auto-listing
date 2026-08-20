# 平台重写规格

## ADDED Requirements

### Requirement: MySQL 业务持久化

系统 MUST 使用 MySQL 8.0 或兼容版本作为业务数据的唯一运行时读写数据库，连接、事务、连接池、字符集和时区必须由统一配置管理；金额字段 MUST 使用定点数语义。

#### Scenario: 应用使用 MySQL 启动

- WHEN 提供有效的 MySQL `DATABASE_URL` 并启动应用
- THEN 应用完成连接检查、schema 版本检查和连接池初始化，且业务读写不访问 `platform.db`

#### Scenario: MySQL 不可用

- WHEN 数据库连接失败或迁移版本不匹配
- THEN 健康检查返回不可用原因，应用不启动会写业务数据的 worker，且日志包含可定位的错误上下文

### Requirement: 分层 FastAPI 应用

系统 MUST 将 HTTP 路由、Pydantic schema、应用服务、领域状态机、Repository 和外部集成适配器分层；路由 MUST 不直接编排 SQL、线程或第三方 HTTP 调用。

#### Scenario: API 调用商品查询

- WHEN 客户端调用商品查询接口
- THEN 路由只负责参数校验和响应转换，查询由应用服务通过 Repository 完成，并返回稳定的分页结构

#### Scenario: 业务服务测试

- WHEN 使用内存替身或测试 Repository 调用商品/Listing 服务
- THEN 不需要启动 FastAPI、MySQL、飞书或 CDP 即可验证状态转换和幂等规则

### Requirement: 完整业务能力

系统 MUST 保留并通过新应用服务暴露商品、账号、SKU、Listing、采集入库、清洗定价、三语文案/RAG、图片质检、审核、飞书回调、多维表轮询、CDP 上架、导出和看板能力。

#### Scenario: 采集到上架闭环

- WHEN 采集数据入库并通过清洗、定价、文案、图片质检和审批
- THEN 系统生成目标市场 Listing/上架文件，并由 CDP 适配器提交，结果回写 Listing、任务日志和学习数据

#### Scenario: 审批拒绝

- WHEN 飞书审批拒绝一个批次
- THEN 对应任务进入人工处理或失败状态，禁止触发 CDP 上架，并保留拒绝原因和审计记录

### Requirement: 任务与并发控制

系统 MUST 将长流程从 HTTP 请求中解耦为持久化任务，任务必须具备状态、重试次数、步骤日志、幂等键和人工等待状态；同一资源批次 MUST 不能被重复领取。

#### Scenario: 任务失败重试

- WHEN 某个步骤发生可重试错误且未超过最大重试次数
- THEN 任务记录错误上下文并回到可领取状态；超过次数后进入 `waiting_human` 或 `failed`

#### Scenario: 重复触发

- WHEN 同一个多维表批次或 Listing 请求被重复提交
- THEN 系统返回已有任务或幂等结果，不重复创建 Listing、不重复发送审批卡、不重复提交上架

### Requirement: SQLite 到 MySQL 全量迁移

系统 MUST 提供只读 SQLite 输入、可重复执行、失败可恢复的迁移命令；迁移 MUST 覆盖现有业务表和可识别历史资产，并输出行数、主键关联、唯一键冲突和跳过记录报告。

#### Scenario: 首次迁移

- WHEN 对备份的 `platform.db` 执行迁移命令
- THEN MySQL schema 初始化后导入全部可迁移数据，外键关系和唯一键校验通过，并生成机器可读报告

#### Scenario: 重复迁移

- WHEN 对同一个 SQLite 输入再次执行迁移
- THEN 迁移不会产生重复业务记录，报告显示已存在记录的幂等处理结果

#### Scenario: 迁移异常恢复

- WHEN 单表导入中断
- THEN 已提交批次保持一致，命令可从检查点继续，且不会将部分数据标记为已完成

### Requirement: API、可观测性与安全边界

系统 MUST 提供版本化 API、统一错误响应、健康检查、结构化任务日志和审计记录；飞书回调 MUST 继续校验签名/挑战请求，敏感配置 MUST 通过环境变量或本地密钥配置注入。

#### Scenario: 无效请求

- WHEN 客户端提交缺少字段或非法状态转换的请求
- THEN API 返回统一错误结构、明确字段/业务原因，并不产生部分业务写入

#### Scenario: 飞书回调验签

- WHEN 回调签名无效
- THEN 系统拒绝处理回调，不改变审批、任务或 Listing 状态，并记录安全审计事件

## MODIFIED Requirements

### Requirement: 启动与后台执行

现有 FastAPI 启动时直接创建表并启动无限后台线程的方式 MUST 改为显式生命周期管理；schema 变更 MUST 通过迁移工具执行，worker MUST 可独立启动、停止和健康检查。

#### Scenario: 仅启动 API

- WHEN 以 API 模式启动
- THEN 只启动 HTTP 服务和只读健康检查，不隐式创建不可控的后台轮询线程

#### Scenario: 启动 worker

- WHEN 以 worker 模式启动
- THEN worker 领取 MySQL 任务并执行轮询/编排，退出时释放租约并记录状态

## REMOVED Requirements

### Requirement: SQLite 运行时主库

系统 MUST 移除默认使用 `data/platform.db` 作为业务运行时主库的行为。

#### Scenario: 生产配置检查

- WHEN 生产模式配置指向 SQLite
- THEN 启动检查拒绝进入生产模式，并提示必须配置 MySQL
