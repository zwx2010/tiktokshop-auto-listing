# 无副作用联调记录（2026-08-26）

| 集成 | 结果 | 说明 |
|---|---|---|
| MySQL | confirmed | `SELECT 1` 连通，Alembic 当前 head 为 `0005_approval_audits` |
| Feishu Bitable | blocked | 本地配置存在；访问 `open.feishu.cn` 被 Windows 网络策略拒绝（WinError 10013）|
| AI | not_configured | 缺少 `COZE_API_TOKEN`、`COZE_BOT_ID` |
| CDP | not_configured | 缺少 `CDP_ENDPOINT`/`CDP_URL` |
| TikTok | not_configured | MySQL 中尚无账号 API 凭据 |

本次未创建、修改或发布任何商品，也未触发 CDP/TikTok 上传。
