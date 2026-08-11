# TikTokShop 多账号自动化运营平台（Lite）

用 FastAPI + SQLite 串起来的 TikTok Shop 多账号运营工具：1688 采集 → 清洗定价 → 三语文案 → 审核 → CDP 真实上架 → 飞书机器人审批。数据全部真实，不含任何演示/模拟成分。

## 一键启动

双击 **`一键启动.bat`**，菜单默认回车即启动服务器：

- **1) 启动服务器**：起 uvicorn(:8000)，探活 `/dashboard/` 即就绪（可视化在飞书多维表格，不再自动开浏览器；看板仍可手动访问）
- **2) 启动飞书隧道**：用 cpolar 建立公网隧道（飞书回调需要）
- **3) 一键全部**：服务器 + 隧道
- **4) 安装依赖**：`pip install -r requirements.txt`
- **5) 健康检查**：探活 + 关键配置文件存在性

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

- 数据库 `data/platform.db` 是**真实数据**：真实 1688/拼多多采集的商品、真实定价。
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
  data/                 platform.db（真实数据）· rag_corpus.db（RAG 索引）· launcher.log
  docs/                 数据库 Schema · 新机器部署
```

## 数据库

默认 SQLite：`data/platform.db`。切 MySQL 一行环境变量：

```bash
set DATABASE_URL=mysql+pymysql://user:pass@localhost:3306/tiktok_platform
```

Schema 见 [`docs/02_database_schema.md`](docs/02_database_schema.md)，部署要点见 [`docs/新机器部署.md`](docs/新机器部署.md)。

## 相关外部资产

- 采集/定价/文案工作流：`../tk自动化工作流/`（LilyCoco 自动化工作流，smoke_01 真实 run）
- 本地化文案包：`../RoseSeek_TikTokShop_AI_Localized_20260809/`（1688 accessories 3site 各 run）
- 上架依赖机器环境：Edge 调试端口 9223（采集）、9344（上架）、cpolar（飞书隧道）
