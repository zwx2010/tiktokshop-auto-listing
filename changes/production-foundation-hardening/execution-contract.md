# 执行合同

## Intent Lock

- **变更名称**：production-foundation-hardening。
- **要解决的问题**：消除内存任务/API、MySQL worker 与内存审批之间的状态断裂；将飞书多维表格建设为以数据库为事实源的运营控制面；在严格批准门下完成真实集成验证和受限测试发布。
- **范围内**：任务和审批持久化、并发领取/租约/幂等、飞书表字段与状态投影、四项非发布探针、50 件上限批准门、只读诊断页、日志脱敏、未提交兼容修复的回归与文档。
- **范围外**：自建运营前端、前端原型、既有采集/定价/本地化规则重写、未获批准的发布、超过约 50 件的测试发布。

## Approved Behavior

- **持久化任务事实源**：飞书触发、批准、重试和发布动作 MUST 对应 MySQL 任务与审计；API 创建的任务必须由持久化 worker 消费，重启后可恢复。
- **受控飞书运营面**：上架表 MUST 展示受控状态、任务 ID、批次、原因、锁、尝试、批准及发布结果；人工重试保留历史，不覆盖失败记录。
- **并发安全**：同一任务同时只允许一个有效 worker；重复回调、重试和租约恢复不得造成重复外部动作。
- **发布批准门**：飞书、AI、CDP、TikTok 的非发布探针均在 24 小时有效期内 confirmed，且配置未变化，才能生成批准清单。批准清单必须包含账号、市场、候选、数量、门禁和下架方案，且最多 50 件。
- **安全诊断**：入站/出站日志不记录 token、secret、signature、原始回调或完整个人标识；本地看板只读。

### Requirement Coverage

| 已批准需求 | 执行批次 | 测试义务 |
|---|---|---|
| 数据库任务事实源 | W1、W2 | API→worker、重启恢复、审计测试 |
| 受控飞书状态机 | W3 | 字段、投影、重试、回写恢复测试 |
| 批次隔离与并发安全 | W2 | MySQL 双 worker、租约、重复回调测试 |
| 发布前批准门 | W4、W5 | 探针、24h 失效、上限、批准审计测试 |
| 日志与只读诊断 | W3、W4 | 脱敏和只读路由负向测试 |

没有未映射需求。

## Design Constraints

- **架构约束**：数据库先状态转换，飞书后投影；飞书 record_id/批次/版本保存在任务 state；SQLite 只可作为迁移输入或单元契约，运行时不承担业务状态。
- **接口约束**：保留现有飞书表和外部 API 的兼容字段；新增字段须由诊断/建表脚本校验，缺失时显式失败。
- **依赖约束**：真实飞书、AI、CDP、TikTok 调用只在专门探针或获批准的发布任务中发生；不可凭“已配置”宣称连通。
- **数据约束**：批准、发布和回写全部可按任务 ID、飞书 record_id、批次号和外部追踪 ID 关联；日志只保存脱敏摘要。

## Execution Waves

### Wave 1 — 基线与持久化任务入口

- **Wave ID**：W1
- **任务**：1、2
- **依赖 wave**：无
- **策略**：serial
- **目标**：固化未提交兼容修复，并以 Repository 统一 API 和 worker 的任务生命周期。
- **完成标准**：API 创建的 MySQL 任务可由独立 worker 消费；基线修复有回归测试；原有测试通过。
- **Review gate**：`changes/production-foundation-hardening/reviews/W1.md`，当前基线 SHA 到 W1 SHA，receipt 为 `pass`。

### Wave 2 — 并发、租约与审批审计

- **Wave ID**：W2
- **任务**：3
- **依赖 wave**：W1
- **策略**：serial
- **目标**：实现迁移、审批持久化、原子领取、租约恢复与重复回调幂等。
- **完成标准**：MySQL 并发测试只领取一次；过期租约可恢复；审批重启后可查询且不重复发布。
- **Review gate**：`changes/production-foundation-hardening/reviews/W2.md`，W1 review receipt 为 `pass` 后方可开始。

### Wave 3 — 飞书运营投影与安全诊断

- **Wave ID**：W3
- **任务**：4、6
- **依赖 wave**：W2
- **策略**：serial
- **目标**：完成字段契约、状态投影、人工重试与只读诊断/日志脱敏。
- **完成标准**：飞书状态可追溯到数据库任务；回写失败可恢复；诊断页无控制入口；敏感字段不写日志。
- **Review gate**：`changes/production-foundation-hardening/reviews/W3.md`，W2 review receipt 为 `pass` 后方可开始。

### Wave 4 — 非发布真实联调与批准门

- **Wave ID**：W4
- **任务**：5
- **依赖 wave**：W3
- **策略**：serial
- **目标**：提供四项真实探针、有效期/失效逻辑和最多 50 件的批准清单。
- **完成标准**：配置缺失、网络失败、confirmed、过期和超限均有测试；真实探针只进行无副作用连通验证。
- **Review gate**：`changes/production-foundation-hardening/reviews/W4.md`，W3 review receipt 为 `pass` 后方可开始。

### Wave 5 — 受限真实发布与收尾

- **Wave ID**：W5
- **任务**：7、8
- **依赖 wave**：W4
- **策略**：serial
- **目标**：在独立人工批准后执行并回写最多 50 件测试商品，完成回归、迁移和运行手册。
- **完成标准**：批准记录、逐件平台追踪 ID/结果、异常处置和下架方案齐全；自动化与迁移验证通过。
- **Review gate**：`changes/production-foundation-hardening/reviews/W5.md`，W4 review receipt 为 `pass` 且人工批准存在后方可开始。

## Test Obligations

- **必须先从失败测试开始的行为**：API→MySQL worker 持久化；并发领取；租约恢复；重复回调；飞书状态回写；探针失效；50 件上限；日志脱敏。
- **必需的边界情况**：服务重启、飞书网络失败、外部超时、过期 confirmed、凭据/端点变更、重复 callback、发布部分成功、发布回写失败。
- **回归敏感区域**：现有图片字符串解析、历史图片回填、Decimal 定价、导出、RAG 门禁、飞书签名、Alembic 迁移。

## Execution Mode

- **可用方式与推荐**：批准后运行 `ssf execution recommend`，以 W1–W5 的串行依赖生成建议。
- **用户确认的模式**：待 DP-3 批准后选择；推荐 `sdd`，因为包含数据库迁移、跨模块状态机和真实外部副作用。
- **推荐理由 / 项目事实**：五个依赖 wave 均需独立审查；W5 是不可自动执行的高影响外部动作。
- **执行计划命令**：在 DP-3 后运行 `ssf execution plan`，将 W1–W5 保存为持久化控制面。

## Verification Dimensions

| 维度 | 状态 | 发现 |
|---|---|---|
| Completeness | Ready | 五项规格需求均映射到 wave、测试和交付物。 |
| Correctness | Pending | 需通过迁移、并发、回调、探针和真实环境验证。 |
| Coherence | Ready | 数据库优先、飞书投影、独立批准门与只读诊断无冲突。 |

**总体结论**：等待 DP-3 批准。

## Review Gates

- 每个 wave 结束后必须生成独立代码审查报告并记录 `ssf execution review` receipt；下游 wave 仅在上游 receipt 为 `pass` 后启动。
- W5 除 W4 的 `pass` 外，还必须有用户在飞书看到候选清单后的独立人工批准。当前用户确认的范围不等于该次发布批准。
- 任意测试失败、迁移不可回滚、重复外部动作、回写不可恢复或脱敏泄漏均阻止进入下一 wave。

## Escalation Rules

- **何时回退到 `specifying`**：新增运营状态/表字段语义、发布数量/审批边界、外部集成范围或自建前端范围发生变化。
- **何时回退到 `bridging`**：实现中需要改变任务模型、迁移策略、wave 依赖或测试责任但不改变已批准用户行为。
- **何时不得继续实现**：无 MySQL 迁移验证、无当前上游 review receipt、无测试证据，或 W5 缺少单独人工发布批准。
