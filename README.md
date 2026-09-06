# TikTokShop 多账号自动化运营平台（Lite）

用 FastAPI + MySQL 串起来的 TikTok Shop 多账号运营工具：1688 采集 → 清洗定价 → 三语文案 → 审核 → CDP 真实上架 → 飞书机器人审批。外部服务只记录真实结果；缺少凭据或运行环境时明确返回未配置/不可用，不伪造成功。

## 72 秒项目演示

![TikTok Shop AI 自动化平台公开演示](docs/demo/tiktokshop-auto-listing-demo.gif)

> 演示画面全部使用脱敏样例数据，不包含商家信息、账号凭据或真实发布操作。它直观展示“飞书发指令 → 机器人执行与审批回卡 → 人工授权 → 发布结果回调 → 多维表格回写”的业务闭环；生成方式见 [`docs/demo/README.md`](docs/demo/README.md)。

## 架构流程

运营在飞书群里发一句自然语言指令，流水线自动跑完全程，直到商品真实发布到 TikTok Shop：

```mermaid
flowchart LR
    A["运营在飞书群发指令<br/>「上架一批 PH 站配饰」"] --> B{"Claude Agent<br/>意图解析"}
    B -->|采集指令| C[1688 CDP 采集<br/>Edge 调试端口抓取]
    B -->|上架指令| D[采集入库]
    C --> D
    D --> E[清洗 + 定价<br/>去重 / 变体值≤50字符 / 真实成本]
    E --> F[制表<br/>选品表 → 三站上架表]
    F --> G[qwen-vl 审图<br/>尺寸检查 / 汉字图过滤]
    G --> H[RAG 三语文案<br/>规则层 + 向量检索 + 真实范例 few-shot]
    H --> I[飞书审批卡<br/>通过全部 / 仅通过审图OK / 驳回]
    I -->|通过| J[CDP 实际上架<br/>真实发布 TikTok Shop]
    J --> K[结果回写<br/>素材库 / 状态机 / 人工兜底]
    I -->|驳回| L[人工补充修改]
    L --> H
```

> ⚠️ **免责声明**：本项目涉及对 1688 / TikTok Shop 等第三方平台页面的自动化操作，仅用于个人学习与自研工具，账号风控风险由使用者自行承担。

## 一键启动

双击 **`一键启动.bat`**，菜单默认回车即启动服务器：

- **1) 启动服务器**：起 uvicorn(:8000)，探活 `/dashboard/` 即就绪（可视化在飞书多维表格，不再自动开浏览器；看板仍可手动访问）
- **2) 启动飞书隧道**：用 cpolar 建立公网隧道（飞书回调需要）
- **3) 一键全部**：服务器 + 隧道
- **4) 安装依赖**：`pip install -r requirements.txt`
- **5) 健康检查**：探活 + 关键配置文件存在性
- **worker**：使用 `python scripts/worker.py --once` 显式执行一个 MySQL 任务，不在 API 进程中隐式轮询

也可以命令行直接用：

```bash
python scripts/launcher.py --server     # 只起服务器
python scripts/launcher.py --check      # 健康检查
python scripts/launcher.py --deps       # 装依赖
```

日志在 `data/launcher.log`。

## 入口

| 用途 | 入口 |
|---|---|
| 一键启动 | `一键启动.bat` |
| Web 看板 | 服务起后打开 `http://127.0.0.1:8000/dashboard/` |
| 1688 采集入库 | `tools/import_1688_captures_to_db.py --market th`（吃 CDP 采集 JSON） |
| 出选品表 | `导出选品表.bat` → `data/products_*.xlsx` |
| 按选品表出上架表 | `按选品表出上架表.bat` → `data/staging_*.csv`（真实 CDP 上架用） |
| 飞书机器人配置 | `scripts/feishu_setup.py` + `config/feishu.local.json` |

## 数据诚实说明（重要）

- 数据库迁移输入 `data/platform.db` 是**真实数据**：真实 1688/拼多多采集的商品、真实定价；运行时业务库为 MySQL。
- **文案来源只有两种**：`codex_listing_copy.csv`（真实三语文案表）或 claude 桥按商品真实属性生成（走 RAG 规则 + 真实范例把关，`scripts/regenerate_listing_copy.py`）。
- 没有任何文案源可用的 Listing 会被标 `copy_missing`，导出上架表时**直接跳过**，绝不带假/空文案上架。
- 旧版 Mock 模板文案（`Daily Outfit Styling / Elevate your daily look`）已全部清掉，库内不应存在。

## 能力一览（全部真实运行）

- **1688 CDP 采集**：Edge 调试端口抓取，防封控采集 → JSON
- **清洗 + 定价**：去重、SKU 变体值 ≤50 字符（规避 TikTok 校验）、真实成本定价
- **三语文案**：PH/EN、TH/Thai、VN/Vietnamese；生成时注入 RAG 要点规则（违禁词 / 品类白名单 / 上传规格上限）+ 同市场真实范例做 few-shot
- **审核门**：`app/agent/approval.py` 状态机；审批通过后自动触发**真实 CDP 上架**（端口 9344）
- **飞书机器人**：审批卡片/群消息回调 → 审批状态机；cpolar 隧道对外
- **Web 看板**：账号 / 商品 / SKU / 上架记录
- **集成状态**：`GET /api/integrations/status` 只读检查 TikTok、AI、飞书、CDP 的真实配置状态；`configured` 不代表已经连通，只有 provider 明确返回成功才是 `confirmed`

## 目录结构

```
TikTokShop_Platform_Lite/
  app/                  FastAPI + SQLAlchemy（main/routers/models/pipeline/…）
    agent/              审核门 + claude 桥（审批状态机、bridge.py）
    rag/                规则层 + 向量检索 + 语料（build/retrieve）
    feishu/             飞书机器人
  scripts/
    launcher.py         一键启动器
    regenerate_listing_copy.py   重生成真实文案（RAG 把关）
    build_rag_index.py           重建 RAG 索引
    feishu_setup.py              飞书配置向导
    seed_from_captures.py        灌真实采集数据
  tools/
    import_1688_captures_to_db.py   1688 采集入库
    export_platform_to_staging.py   出上架表（真实 CDP 用）
    export_products_excel.py        出选品表
  config/               market_pricing / listing_defaults / upload_learning / feishu.local.json
  data/                 platform.db（迁移输入）· rag_corpus.db（RAG 索引）· launcher.log
  docs/                 数据库 Schema · 新机器部署
```

## 数据库

运行时必须配置 MySQL：

```bash
set DATABASE_URL=mysql+pymysql://user:pass@localhost:3306/tiktok_platform
```

首次迁移 SQLite 业务数据：

```bash
python tools/migrate_sqlite_to_mysql.py --source data/platform.db --target "$DATABASE_URL" --report data/migration-report.json
alembic upgrade head
```

文案与外部服务配置：

- AI 文案：`COZE_API_TOKEN`、`COZE_BOT_ID`；未配置时 Listing 保持 `copy_missing`。
- TikTok：在 MySQL 的 `account_api_credentials` 中配置账号凭据；没有真实 token 时账号集成状态为 `not_configured`。
- 飞书：`config/feishu.local.json` 配置应用或机器人 webhook；配置存在只代表 `configured`，不代表消息已送达。
- CDP：设置 `CDP_ENDPOINT` 或 `CDP_URL`，并准备真实浏览器调试运行环境。

服务启动后建议先检查：

```bash
curl http://127.0.0.1:8000/api/v1/health/ready
curl http://127.0.0.1:8000/api/integrations/status
```

Schema 见 [`docs/02_database_schema.md`](docs/02_database_schema.md)，部署要点见 [`docs/新机器部署.md`](docs/新机器部署.md)。

## 相关说明

- 采集 / 定价 / 文案由自动化工作流包驱动（1688 accessories 多站点真实 run），配合本地化文案包产出三语文案。
- 上架依赖机器环境：Edge 调试端口 9223（采集）、9344（上架）、cpolar（飞书隧道）。
- 敏感数据不随仓库分发：真实商品库、飞书应用密钥见「数据诚实说明」与 `config/*.local.json`，需要自行准备。
