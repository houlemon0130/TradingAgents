# TradingAgents 数据源诊断与修复手册

本机网络环境特殊：公司 LightProxy MITM 代理 + Cloudflare Worker 反代绕出口 IP 封锁。
数据源故障多数可快速修复，按本文档排查。诊断日期：2026-08-13 / 2026-08-19。

## 关键事实

- `.env` 由 `tradingagents/__init__.py` 导入时加载（`load_dotenv`）。任何脚本必须在
  `import tradingagents` **之后**、读取 `TA_EGRESS_PROXY_KEY` 等环境变量**之前**完成导入顺序。
- 正确顺序（参考 `scripts/batch_analyze.py`）：
  1. `net_bootstrap.apply()`（补丁 SSL + 设置代理 env）
  2. `import tradingagents`（触发 `.env` 加载）
  3. `data_source_guards.install()`（打数据源守卫补丁）
- 先跑 `check_data_sources.py` 确认现状，再决定修什么。

## 各数据源

### StockTwits — Cloudflare Worker 直连
- 故障：本机出口 IP 被 StockTwits 的 Cloudflare 403（直连、本地代理、真实浏览器都挡）。
- 修复：走 Worker 反代 `https://ta-egress-proxy.quasar-cymbal.workers.dev/p/api.stocktwits.com/...`。
- 常见失败：
  - 403 forbidden → Worker 没收到 `X-Proxy-Key`。原因通常是请求走了 LightProxy 被剥 header。
    确认 `net_bootstrap.DIRECT_HOSTS` 含 `ta-egress-proxy.quasar-cymbal.workers.dev`（2026-08-19 已加）。
  - `_worker_alive()` False 但 curl 直连 200 → 进程里 `.env` 未加载，`TA_EGRESS_PROXY_KEY` 为 None。
    检查 import 顺序是否在 `data_source_guards` 之前导入了 tradingagents。
- 验证：`curl -s -H "X-Proxy-Key: $TA_EGRESS_PROXY_KEY" https://ta-egress-proxy.quasar-cymbal.workers.dev/p/api.stocktwits.com/api/2/streams/symbol/AAPL.json`

### Reddit — Worker RSS 优先，ego-browser 兜底
- 故障：匿名访问按 IP 限流（2/6 成功），429 与"无讨论"不可区分。
- 修复：Worker 站内 `search.rss`（6/6 稳定，无评分/评论数）；Worker 403 时回退
  ego-browser（`ego-browser nodejs` 脚本，真实页面上下文可达 `search.json`）。
- 注意：`<no Reddit posts mentioning ...>` 是**合法空结果**（搜索成功），不是故障。
  故障特征是 `(WARNING: Reddit rate-limited this fetch (HTTP 429)` 或 `<fetch failed`。

### Polymarket — Worker 直连，Manifold 兜底
- 故障：`gamma-api.polymarket.com` 本机任何路径都 connect timeout。
- 修复：Worker 反代 `gamma-api.polymarket.com`。
- 注意：Manifold Markets（仿真盘）是 Worker 不可用时的替代品，返回块会显式标注 play-money。

### FRED — 有界重试
- 故障：可达但负载下丢连接（26 次调用约 2 次失败）。
- 修复：`data_source_guards._harden_fred` 包装 3 次指数退避重试。

### Yahoo（行情/新闻/基本面/技术指标）— 直连
- 直连正常；`no_proxy` 含 `.yahoo.com` 保证不走代理。

### AlphaVantage — 直连
- 备用 vendor；`.env` 配了 `ALPHA_VANTAGE_API_KEY`。

### DashScope/百炼（LLM 端点）— 直连
- `no_proxy` 含 `dashscope.aliyuncs.com`；分析本身依赖它，失败则整个管线不可用。

## Worker 反代本身

- 代码：`scripts/cf-proxy/worker.js` + `wrangler.toml`；部署名 `ta-egress-proxy`。
- `PROXY_KEY` 是 secret（`npx wrangler secret put PROXY_KEY`），`.env` 的
  `TA_EGRESS_PROXY_KEY` 必须与其一致。
- 若 worker.dev 返回 404/失效：`--temporary` 部署会过期，需重新 `npx wrangler deploy`。
- Worker 的 `ALLOWED_HOSTS` 只放行白名单主机，别指望它当通用代理。

## 运行日志中的失败信号（分析后验证用）

出现以下任意模式 = 该轮分析数据源不干净，**必须修复后重跑**，不能交付结果：

```
StockTwits fetch failed
<stocktwits unavailable
Reddit RSS .*429
rate-limited this fetch
Polymarket search failed
ConnectTimeoutError|timed out
HTTP Error 4\d\d
unreachable from this network
Prediction markets .* unavailable
```

（`no Reddit posts mentioning` / `no posts found mentioning` 是合法空结果，不在其列。）
