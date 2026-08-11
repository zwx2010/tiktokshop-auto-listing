# TikTok Shop 多账号自动化运营平台 — 数据库 Schema

| 项 | 值 |
|---|---|
| 版本 | v0.1 |
| 数据库 | MySQL 8.0（InnoDB / utf8mb4） |
| ORM | SQLAlchemy 2.x（逻辑设计，迁移工具 Alembic） |
| 关联文档 | `01_architecture_design.md` |

> 设计目标：**每张表回答一个业务问题；关键写入都有唯一约束保证幂等；状态用状态机而不是散落的布尔值；所有业务表带 `account_id` 支撑多账号隔离。**

---

## 1. 表清单总览（19 张）

| # | 表名 | 回答的业务问题 | 归属 |
|---|---|---|---|
| 1 | `accounts` | 有哪些店铺账号，处于什么状态 | 账号 |
| 2 | `account_api_credentials` | 账号的 API 凭据（加密） | 账号 |
| 3 | `keywords` | 每个站点/产品线跑什么关键词 | 选品 |
| 4 | `products` | 采集到的货源商品是什么 | 商品 |
| 5 | `product_skus` | 一个商品有哪些颜色/尺码，各多少成本 | 商品 |
| 6 | `listings` | 某商品在某账号某站点上架成了什么 | 上架 |
| 7 | `upload_results` | 上架结果表反馈了什么（学习数据） | 上架 |
| 8 | `orders` | 有哪些订单，状态如何 | 运营 |
| 9 | `order_items` | 订单里买了什么 | 运营 |
| 10 | `messages` | 客户和店铺的会话消息 | 消息 |
| 11 | `message_attachments` | 消息里的图片/附件 | 消息 |
| 12 | `image_qa_records` | 每张图质检结果 | 质检 |
| 13 | `tasks` | 有没有在跑/待跑的任务 | 调度 |
| 14 | `task_logs` | 任务每一步发生了什么 | 调度 |
| 15 | `sync_watermarks` | 每个账号同步到哪了（增量） | 同步 |
| 16 | `skipped_products` | 哪些商品被过滤、为什么 | 数据 |
| 17 | `dedup_registry` | 历史去重登记（替代现有 CSV） | 商品 |
| 18 | `settings` | 全局配置（店铺名/默认尺码表等） | 配置 |
| 19 | `audit_logs` | 谁在什么时候做了什么 | 审计 |

---

## 2. 实体关系（ER）

```
accounts 1───n account_api_credentials
accounts 1───n orders
accounts 1───n messages
accounts 1───n listings
accounts 1───n sync_watermarks
accounts 1───n tasks

products 1───n product_skus
products 1───n listings
products 1───n skipped_products
products 1───1 dedup_registry
products 1───n image_qa_records (ref_type='product')

listings 1───n upload_results
listings 1───n image_qa_records (ref_type='listing')

orders 1───n order_items
order_items n───1 listings (seller_sku)

messages 1───n message_attachments

tasks 1───n task_logs
```

---

## 3. 逐表设计

> 通用约定：所有表含 `id BIGINT UNSIGNED AUTO_INCREMENT` 主键、`created_at`、`updated_at`（`DATETIME(3)`）；金额用 `DECIMAL(12,2)`；JSON 用 `JSON` 类型；字符集 `utf8mb4`。

### 3.1 `accounts` — 账号

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| name | VARCHAR(100) | UNIQUE NOT NULL | 账号昵称（如 `LilyCoco_PH`） |
| platform | ENUM('tiktok_shop') | NOT NULL DEFAULT 'tiktok_shop' | 平台 |
| market_code | CHAR(2) | NOT NULL | 站点 PH/TH/VN |
| store_name | VARCHAR(100) | | 店铺名（写入标题前缀） |
| shop_id | VARCHAR(64) | | 店铺 ID |
| api_status | ENUM('none','applied','approved','rejected') | DEFAULT 'none' | 官方 API 申请状态 |
| rpa_profile_dir | VARCHAR(255) | | 独立浏览器配置相对路径（隔离登录态） |
| proxy_host / proxy_port / proxy_type | VARCHAR(255)/INT/VARCHAR(16) | | 独立代理 |
| proxy_username / proxy_password | VARCHAR(100) | | 代理认证（密码加密） |
| status | ENUM('inactive','logging_in','active','limited','disabled') | NOT NULL DEFAULT 'inactive' | 账号状态机 |
| health_score | DECIMAL(5,2) | DEFAULT 100.00 | 健康分 |
| rate_limits | JSON | | `{collect_wait_s, cooldown_every, cooldown_s, ...}` |
| last_active_at | DATETIME(3) | | 最近活跃 |

索引：`idx_status(status)`。
状态机：`inactive → logging_in → active → limited → disabled`；`limited`（风控）只允许人工恢复。

### 3.2 `account_api_credentials` — API 凭据

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| account_id | BIGINT | FK→accounts UNIQUE(account_id, platform_api) | |
| platform_api | ENUM('tiktok_open') | | |
| app_key / app_secret | VARCHAR(255) | | **加密存储** |
| access_token / refresh_token | TEXT | | 加密存储 |
| token_expires_at | DATETIME(3) | | 过期前自动刷新 |
| scope | JSON | | 授权范围 |

### 3.3 `keywords` — 选词

| 字段 | 类型 | 说明 |
|---|---|---|
| market_code | CHAR(2) | PH/TH/VN |
| product_line | ENUM('womens','accessories') | 产品线 |
| keyword | VARCHAR(200) | 关键词 |
| priority | TINYINT | 1 稳妥 / 2 扩展 / 3 验证 / 0 blocked |
| group_tag | VARCHAR(64) | 分组（如 tops/pants） |
| status | ENUM('active','paused') | 默认 active |

索引：`idx_market_line_priority(market_code, product_line, priority)`。

### 3.4 `products` — 货源商品

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| source_platform | ENUM('pdd','1688') | NOT NULL | 来源 |
| source_goods_id | VARCHAR(64) | NOT NULL | 平台商品 ID |
| market_code | CHAR(2) | | 目标站点 |
| product_line | VARCHAR(32) | | womens/accessories |
| title_cn | VARCHAR(500) | | 原始中文标题 |
| category | VARCHAR(64) | | 类目识别结果 |
| raw_attrs | JSON | | 采集原始属性 |
| main_image_url | VARCHAR(1000) | | 主图 |
| image_urls | JSON | | 图片列表 |
| cost_cny_used | DECIMAL(10,2) | | 使用的成本（人民币） |
| cost_source | ENUM('sku_matrix','detail_price','detail_text','search_buffered','fallback') | | 成本来源优先级 |
| weight_g | INT | | 重量（定价用） |
| size_chart_url | VARCHAR(1000) | | 尺码表 URL |
| dedup_key | VARCHAR(255) | **UNIQUE** | `{source_platform}:{source_goods_id}`，防重复采集/上架 |
| status | ENUM('discovered','filtered','priced','listed','failed') | | 商品状态机 |
| skip_reason | VARCHAR(255) | | 被过滤时原因 |

索引：`idx_status(status)`、`idx_dedup_key(dedup_key)`、`idx_market_line(market_code, product_line)`。

### 3.5 `product_skus` — SKU

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| product_id | BIGINT | FK→products | |
| color / size | VARCHAR(64) | UNIQUE(product_id, color, size) | 销售属性 |
| supplier_sku_id | VARCHAR(64) | | 平台原始 SKU ID |
| cost_cny | DECIMAL(10,2) | | 该 SKU 真实成本 |
| stock | INT | DEFAULT 500 | 库存 |

> 成本来源优先级决定 `products.cost_source`，但每个 SKU 的成本单独记在 `product_skus.cost_cny`（延续现有"逐 SKU 定价"逻辑）。

### 3.6 `listings` — 上架记录

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| product_id | BIGINT | FK→products | |
| account_id | BIGINT | FK→accounts UNIQUE(product_id, account_id, market_code) | 同一商品不重复上架 |
| market_code | CHAR(2) | | |
| title | VARCHAR(300) | | 最终标题 |
| description | TEXT | | 最终描述 |
| price / target_sale_price | DECIMAL(12,2) | | 展示价 / 目标成交价 |
| currency | CHAR(3) | | PHP/THB/VND |
| discount_rate | DECIMAL(4,2) | | 折扣力度 |
| seller_sku | VARCHAR(128) | **UNIQUE** | 商家 SKU 编码 |
| sku_snapshot | JSON | | SKU 快照（价格/库存/属性） |
| listing_status | ENUM('draft','ready','submitting','submitted','failed','live','error') | | 上架状态机 |
| submit_result | JSON | | 提交返回/错误 |
| upload_file_path | VARCHAR(255) | | 上传表路径 |
| warning_count | INT | DEFAULT 0 | 质检 Warnings 数（=0 才建议上传） |

索引：`idx_account_status(account_id, listing_status)`。

### 3.7 `upload_results` — 上架结果（反学数据）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| listing_id | BIGINT | FK→listings | |
| account_id / market_code | | | 冗余便于按账号查询 |
| row_status | ENUM('success','error') | | |
| error_code | VARCHAR(64) | | 平台错误码 |
| error_message | VARCHAR(500) | | |
| error_category | VARCHAR(32) | | 归因分类（size_chart/image/category/attribute/price/stock/sku/brand/policy_or_ip/logistics） |
| processed_by | VARCHAR(64) | UNIQUE(listing_id, processed_by) | 来源结果表 SHA256，防重复处理 |
| processed_at | DATETIME(3) | | |

### 3.8 `orders` — 订单

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| account_id | BIGINT | FK→accounts | |
| order_no | VARCHAR(64) | **UNIQUE(account_id, order_no)** | 幂等键 |
| market_code | CHAR(2) | | |
| status | ENUM('pending','paid','to_ship','shipped','completed','cancelled','refund') | | |
| buyer_note | VARCHAR(500) | | 买家留言 |
| total_amount / currency | DECIMAL(12,2)/CHAR(3) | | |
| ship_by_deadline | DATETIME(3) | | 出货截止（催发货/出货清单用） |
| sync_source | ENUM('api','rpa') | | 数据通道 |
| raw | JSON | | 原始报文 |

索引：`idx_account_status(account_id, status)`、`idx_ship_deadline(ship_by_deadline)`。

### 3.9 `order_items` — 订单明细

| 字段 | 类型 | 说明 |
|---|---|---|
| order_id | BIGINT FK→orders | |
| seller_sku | VARCHAR(128) | 关联 listings.seller_sku |
| product_title | VARCHAR(300) | 冗余标题 |
| qty | INT | |
| unit_price | DECIMAL(12,2) | |

### 3.10 `messages` — 客户消息

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| account_id | BIGINT | FK→accounts | |
| thread_id | VARCHAR(128) | | 会话 ID |
| external_message_id | VARCHAR(128) | **UNIQUE** | 幂等键 |
| direction | ENUM('in','out') | | 收/发 |
| from_user | VARCHAR(128) | | 发送人 |
| text | TEXT | | |
| attachments | JSON | | 附件摘要 |
| intent | VARCHAR(32) | NULL | 咨询尺码/催发货/售后/砍价/闲聊/其他 |
| ai_reply_draft | TEXT | NULL | AI 回复草稿 |
| reply_status | ENUM('none','drafted','approved','sent','failed') | DEFAULT 'none' | 回复状态机 |
| sync_source | ENUM('api','rpa') | | |
| raw | JSON | | |
| received_at | DATETIME(3) | | |

索引：`idx_account_thread(account_id, thread_id)`、`idx_reply_status(reply_status)`、`idx_unreplied(account_id, reply_status, received_at)`。

### 3.11 `message_attachments` — 消息附件

| 字段 | 类型 | 说明 |
|---|---|---|
| message_id | BIGINT FK→messages | |
| media_type | VARCHAR(32) | image/video/file |
| url / local_path | VARCHAR(1000) | |
| mime | VARCHAR(64) | |

### 3.12 `image_qa_records` — 图片质检记录

| 字段 | 类型 | 说明 |
|---|---|---|
| ref_type | ENUM('product','listing') | 质检对象类型 |
| ref_id | BIGINT | product_id / listing_id |
| image_url / image_path | VARCHAR(1000)/VARCHAR(500) | |
| rule_checks | JSON | 确定性层：`{resolution:ok, format:ok, blur:0.42, small:false}` |
| ai_checks | JSON | AI 层：`{is_promo:false, has_watermark:false, covered:false, irrelevant:false}` |
| overall | ENUM('pass','fail','review') | 合并判定 |
| fail_reasons | JSON | 具体原因列表 |
| qa_at | DATETIME(3) | |

索引：`idx_ref(ref_type, ref_id)`、`idx_overall(overall)`。

### 3.13 `tasks` — 任务

| 字段 | 类型 | 说明 |
|---|---|---|
| task_type | ENUM('collect','price','copywriting','image_qa','upload','sync_orders','sync_messages','sync_inventory') | |
| account_id | BIGINT FK 可空 | 账号级任务归属 |
| run_directory | VARCHAR(255) | 批次目录（断点续跑用） |
| status | ENUM('pending','running','success','failed','cancelled','waiting_human') | 状态机 |
| attempts / max_attempts | INT | 重试计数 |
| state | JSON | 断点位置/上下文 |
| started_at / finished_at | DATETIME(3) | |
| error_code / error_message | VARCHAR(64)/VARCHAR(500) | |

索引：`idx_status_type(status, task_type)`、`idx_account(account_id)`。

### 3.14 `task_logs` — 任务日志

| 字段 | 类型 | 说明 |
|---|---|---|
| task_id | BIGINT FK→tasks | |
| level | VARCHAR(16) | info/warn/error |
| message | VARCHAR(1000) | |
| context | JSON | 附加上下文 |
| ts | DATETIME(3) | |

### 3.15 `sync_watermarks` — 增量同步水位

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| account_id | BIGINT | PK 一部分 | |
| sync_type | ENUM('orders','messages','inventory') | PK 一部分 | |
| last_key | VARCHAR(128) | | 增量游标（订单号/时间戳/消息ID） |
| last_run_at | DATETIME(3) | | 最近同步时间 |

> 复合主键 `(account_id, sync_type)` 保证每账号每类同步只有一个水位。

### 3.16 `skipped_products` — 被过滤商品

| 字段 | 类型 | 说明 |
|---|---|---|
| product_id | BIGINT FK→products 可空 | |
| source_platform / source_goods_id | | |
| reason | VARCHAR(255) | 过滤原因（标题/图/成本/人群/售罄...） |
| detail | JSON | 细节 |
| skipped_at | DATETIME(3) | |

### 3.17 `dedup_registry` — 去重登记

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| dedup_key | VARCHAR(255) | UNIQUE | 与 products.dedup_key 一致 |
| product_id | BIGINT | FK 可空 | |
| status | ENUM('discovered','generated','uploaded','failed') | | 现状迁移自现有 `product_registry.csv` |

### 3.18 `settings` — 全局配置

| 字段 | 类型 | 说明 |
|---|---|---|
| key | VARCHAR(100) UNIQUE | 如 `default_store_name`、`default_size_chart_url` |
| value | TEXT | |
| updated_at | DATETIME(3) | |

### 3.19 `audit_logs` — 审计

| 字段 | 类型 | 说明 |
|---|---|---|
| actor | VARCHAR(100) | 人/AI/系统 |
| action | VARCHAR(64) | 上架/回复/改价/改配置 |
| object_type / object_id | VARCHAR(32)/BIGINT | 对象 |
| before / after | JSON | 变更前后（diff 可回溯） |
| ts | DATETIME(3) | |

---

## 4. 关键状态机汇总

```
账号:   inactive → logging_in → active → limited → disabled
                                    ↑        │
                                    └────────┘ (人工验证恢复)
商品:   discovered → priced → listed
          │            │
          └── filtered ┘(skip_reason 记录)
上架:   draft → ready → submitting → submitted → live
                          │            │
                          └──── error ─┘(submit_result 记录,可重试)
消息回复: none → drafted → approved → sent
                        └── failed
任务:   pending → running → success
            │        │
            └────────┴── failed → (attempts<max → pending 重试 / → waiting_human)
```

---

## 5. 与现有 CSV 资产的迁移映射

| 现有资产（LilyCoco 项目） | 迁移到 | 说明 |
|---|---|---|
| `data/product_registry*.csv`（4 个站点库） | `products` + `dedup_registry` | status/upload 结果并入 products 状态与 upload_results |
| `data/size_chart_url_map.csv` | `products.size_chart_url` | 商品级尺码表 URL |
| `data/category_size_charts.csv` | `settings`（类目默认尺码表） | |
| `config/ph_womens_keywords.csv` / `accessories_keywords.csv` | `keywords` | 带 priority/group_tag |
| `data/upload_learning/*` | `upload_results` + `audit_logs` | 反学数据入库 |
| `data/upload_results/processed_results.csv` | `upload_results.processed_by` | 防重复处理 |
| `config/market_pricing.json` 等 | `settings` / 配置文件保留 | 定价参数仍走配置文件，不进表（便于版本管理） |

---

## 6. 落地顺序建议（配合 Phase 1 MVP）

1. `accounts` → `settings`（先有"壳"）。
2. `keywords` → `products` → `product_skus`（采集落地）。
3. `listings` → `upload_results`（上架闭环）。
4. `tasks` → `task_logs`（调度与断点续跑）。
5. `image_qa_records`（质检）。
6. Phase 2 再落 `orders` / `order_items` / `messages` / `message_attachments` / `sync_watermarks` / `audit_logs`。

---

*Schema 以 SQLAlchemy 模型为准落地；本文档作为设计与评审依据。*
