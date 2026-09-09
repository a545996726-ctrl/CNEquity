# 集成同花顺官方 API（`ths_official`）

目标是**提高数据湖质量**，不是增加数据源数量。计划中的每一项都对应一处实测的缺陷或空洞，
不做"多一个源总是好的"这类假设。

上游为 [HiThink-Tech/Financial-API](https://github.com/HiThink-Tech/Financial-API)（代码 MIT），
服务端 `https://fuyao.aicubes.cn`，鉴权为账号签发的 `X-api-key`。

---

## 1. 实测证据

全部数据测于 2026-09-08 / 09，对照本地 54GB 生产湖。抽样处注明样本量；
复权因子一项为**全量**比对（56,808 个可比事件），非抽样。

### 一致性

| 项目 | 结果 |
|---|---|
| 日线 OHLC（12 只 × 3 年，8,600 行） | 四个字段**全部** <0.5bps，p99 = max = 0.00 |
| `volume` / `turnover` 单位 | 与湖内比值 median 1.0000（单位为股，无需换算） |
| 现金分红（全量 52,583 条） | **52,509 一致（99.86%）** |
| 配股比例（959 条交集） | 901 一致；dump 含 `allotment_ratio` / `allotment_price` |
| 营业收入（240 期） | 239 一致；唯一分歧为 600734.SH 2025Q2 真实重述 |
| 归母净利润（240 期） | 236 一致（须映射到 `parent_holder_net_profit`） |
| `operating_profit` / `profit_total` / `operating_costs` / `income_tax_expense` | **各 100%** |

### 边界

| 项目 | 结果 |
|---|---|
| 个股日线历史下界 | 约 **2005-01-04**（两只 1996 年前上市的股票同日截止，为全局下界） |
| 财报历史 | 2006–2015 返回 40 期；**2016–2024 返回 36 期（9 年 × 4 季，一期不缺）** |
| 复权事件流 | 1991-02-26 → 2026-09-16（含未来除权日） |
| K 线周期 | `1d` ✓、`1w` ✓、`1M` ✗（`code=1002`）。文档参数表与概览两处说法均不准确 |
| 指数/板块历史 | 近 3 年正常；**2008–2017 返回 0 根**，下界远高于个股 |
| 退市证券 | **`code=1002 Unknown thscode`** —— 按代码直接取也不认 |
| 财务指标接口 | `report=YYYY-Q`，每次一个（标的, 报告期）→ 全湖约 **57 万请求** |

### 口径陷阱

- **`per_share_bonus` 只含送股，不含转增**（全量 34 个可比转增事件中 33 个成立）。
  单靠事件流推导复权因子会漏掉转增；配股只能走 dump。
- **`report_date_ms` 不是首次披露日**，而是该期之后一年那份报告的披露日
  （该期在其中作为上年同期对比列出现）。真实首披日 = **期数 P−4 个季度那一行的
  `report_date`**，位移后 184/192（95.8%）精确命中。残差集中在延迟/重述披露、
  北交所、当年边界三类。
- 湖内 `net_profit` 是**归母**口径，对应 `parent_holder_net_profit`，
  不是 THS 的 `net_profit`（总净利润，含少数股东）。按名直连会得到 39.2% 的假分歧。

### 协议缺陷

- **`2001` 线上从未出现。** 缺 Key 与错 Key 都回 `2003`，而文档把 `2003` 保留给
  "capability 未授予"。一码三义，只能靠 `message` 区分。
- **`3002` 是空结果不是错误**（`No adjustment events for thscode=...`）。
- **dump 有两套路径**：`/dump/**` 是浏览器 Cookie 入口且由文档站 SPA 兜底
  （错用会静默拿到 HTML）；API 客户端必须走 `/api/dump/**`。
- 预签名链接字段名为 **`presigned_url`**，`expires_in_seconds: 300`。**不可缓存**。

---

## 2. 设计约束

API 是**可选依赖**。没有 Key 时湖保持原有获取方式，有 Key 时按最佳实践优化。
这不是新设计，是仓库既有约定的再次应用（`minute_bars` 默认关闭、
`exchange_audit` gate 在 `[sources.*]`、`_corporate_actions_*_repair` 显式开关）。

三条硬约束：

**C1 · `ths_official` 永远不做 `primary_source`。**
一旦某数据集的 primary 依赖 Key，没 Key 的用户日更即坏。它只能出现在
`backup_source`、`backfill_source` 以及 failover / audit 配置中。

**C2 · 内容差异必须在数据层可见。**
补入的行 `source='ths_official'`，provenance 三列一眼可辨；
`docs/datasets/catalog.md` 注明哪些覆盖区间需要 Key。

**C3 · 区分 routing 与 switching（[ADR-0005](../adr/0005-source-routing-vs-switching.md)）。**

| 收益 | 归类 | 是否需显式命令 |
|---|---|---|
| 补 2016–2024 balance/cashflow | **routing**（这些主键当前无行） | 否，普通 backfill |
| 2005–2015 换持牌来源 | **switching**（4,403,582 行已有 canonical） | **是** |
| 复权因子 / 日线仲裁 | 只写 source snapshot | 否 |

补九年空洞不是制造分叉，是修复 [steps/fundamentals.py:395](../../src/cnequity/steps/fundamentals.py)
的 `backfill_missing_statement_types` 现在就在报的已知缺陷。

另外：**验证类**开关（有 Key 就查）与**内容类**开关（有 Key 也不动已有数据，除非显式要求）
必须分开，不能共用一个 `[sources.ths_official].enabled`。

---

## 3. 实施阶段

### Phase 1 · 源登记与配置

- `sources/SOURCES.yml` 新增 `ths_official` 条目。`authentication` 写 `api_key`；
  `commercial_use` / `redistribution` / `cache_allowed` **继续写 `unknown`** ——
  官方文档中没有任何数据使用条款，MIT 只覆盖代码。
- **不复用现有 `ths` 标签**：鉴权方式、access_type、端点族、条款都不同。
- Config 增加 `[sources.ths_official].api_key` 与 `HITHINK_FINANCE_API_KEY`，
  照搬 [config/loader.py:519](../../src/cnequity/config/loader.py) 的 `tushare_token` 模式
  （`repr=False`，raw_archive 脱敏）。
- `orchestrator.source_concurrency` 增加条目。

本阶段无行为变化。

### Phase 2 · 客户端与适配器骨架

`src/cnequity/adapters/ths_official/`：

- `client.py` —— 统一信封（HTTP 恒 200，业务结果看 `code`）；`2003` 按 `message` 分派；
  `3002` 视为空结果；`4001` 退避重试；传输层重试；**带 `download-url` 的路径禁止缓存**。
- 接入 `domain.rate_limit.source_request` 与 `storage.raw_archive`。
- 单元测试使用录制的响应 fixture，不打网络。

### Phase 3 · 补财报九年空洞（价值最高）

湖内每年有数据的标的数：

| 年份 | income | indicator | balance | cashflow |
|---|---|---|---|---|
| 2015 | 4,358 | 4,358 | 4,358 | 4,358 |
| **2016–2020** | 4,623→5,460 | 满 | **0** | **0** |
| **2021–2024** | 5,478→5,558 | 满 | **1→37** | **0→36** |
| 2025 | 5,572 | 5,525 | 5,492 | 5,492 |

字段映射（文档未记载这套命名，须以实测为准）：

| 湖内 `item_code` | THS 字段 |
|---|---|
| `net_cash_operate` | `act_cash_flow_net` |
| `net_cash_invest` | `invest_cash_flow_net` |
| `net_cash_finance` | `financing_cash_flow_net` |
| `capex` | `pay_fixed_assets_etc_cash` |
| `end_cash` | **无对应**（`cash_equivalents_net_addition` 是净增加额，不是期末余额） |
| — | `pay_dividends_profits_interest_cash`（湖内没有，可新增） |

- 成本：约 5,600 标的 × 2 张表 × 1 个 10 年窗口 ≈ **11,200 请求**。
- 属 routing，无需 repair flag。
- 验收：`backfill_missing_statement_types` 对 2016–2024 不再报告。

### Phase 4 · 复权因子与公司行动的对手源（价值第二）

`adj_factors` 现状：**19,088,826 行、100% sina、无 backup、无 backfill、
`[[failover.datasets]]` 中也没有它**。全湖 8,079 处内部分歧无人仲裁
（6,551 处有事件无因子跳变，1,528 处有跳变无事件），且逐年恶化。

- 走 **dump**（`/api/dump/market-dumps/adjustment-factors/download-url`），
  一次下载 57,139 行覆盖全历史，而非逐 thscode。
- 写入 source snapshot，不写 curated。
- 新增 `[[failover.datasets]] name="adj_factors" primary="sina" backup="ths_official"`。
- `corporate_actions` 的 failover 由二元扩为三方。
- 口径：`per_share_bonus` 只含送股；配股取 dump 的 `allotment_ratio` / `allotment_price`。

### Phase 5 · 日线第三方仲裁与 2005–2015 换源

- 2016+ 已是 tdx / eastmoney 双源，加入第三方后 revision gate 在分歧时可判方向。
- 2005–2015 的 **4,403,582 行**（占非官方 `ths` 抓取的 82.3%）换持牌来源。
  与退市标的**零重叠**，换源计划完整成立。属 **switching**，需显式 repair 命令。
- 2001–2004 的 **949,815 行**够不到官方历史下界，保持现状并在 SOURCES.yml 注明。

### Phase 6 · 财报披露日复核

- 位移 4 个季度后与湖内 `announce_date` 比对，95.8% 精确命中。
- 残差按三类分流：延迟/重述披露、北交所、当年边界。
- **只产出 advisory finding，永不覆写 `announce_date`。**
  湖内 4,012,672 行零空值，且 `pit_quality="reconstructed"` 的病根是数值为重述值 ——
  两边都给当前值，谁也治不了，`pit_grade` 上不去。

### Phase 7 · 文档与合规

- `sources/SOURCES.yml`、`docs/legal/source-matrix.md`、`docs/datasets/catalog.md`。
- 一份 ADR 记录**可选源契约**（C1–C3）。
- 一份 ADR 解决**送/转建模问题**：`action_type` 在主键里，两个来源对同一事件的不同分类
  会产生两条合法但矛盾的行。当前已在 derive 层按来源聚合规避双计，
  但是否应允许两行并存是建模决策。

---

## 4. 明确不做

| 数据集 | 原因 |
|---|---|
| `sector_bars` / `index_bars` 换源 | 指数历史 2008–2017 返回 0 根，只能换近段，收益不抵复杂度 |
| `valuation_metrics` | 只有最新快照，文档明写不提供历史 |
| `dragon_tiger` / `hot_rank` | 限一年内，只能日增量对账，不能回补 |
| 财务指标 | 每次一个(标的,报告期)，全湖约 57 万请求 |
| `trading_calendar` | 只有 `[今日-1年, 今日]`、无未来日，弱于现有 tdx 主源 |
| **整体切换主源** | 退市证券 `code=1002` 不可寻址。全湖 763,548 行（4.0%）属已退市标的，来源为 baostock/sina/tdx。切换即废掉幸存者偏差修正，也就是 README 首屏 5.9% vs 12.0% 那个结论 |
| 分钟 K / tick / 宏观 / 公告 / 新闻 / 研报 | 官方明确不提供 |

---

## 5. 风险登记

| 风险 | 缓解 |
|---|---|
| 无 Key 时全链路静默降级 | 验证类与内容类开关分离；catalog 注明覆盖差异 |
| `2003` 一码三义 | 按 `message` 分派；无法区分时按不可重试处理 |
| 预签名链接 300 秒过期 | 每次下载前重换，禁止缓存；大文件考虑分片与重试 |
| 限流阈值未知 | 迄今最多 55 请求/轮、0.4 秒间隔未触发。Phase 3 的 11,200 请求前需先做压测 |
| ETF 覆盖未验证 | 湖内 2,198 只 ETF 未纳入抽样，Phase 5 前补测 |
| 数据条款空白 | 全站文档无商用/再分发条款，SOURCES.yml 相关字段保持 `unknown` |

---

## 6. 前置条件

集成前应先修完由本次交叉验证发现的三个湖内缺陷（已在 `fix/lake-data-defects` 分支完成）：
证券名称 NUL 填充、假退市推断、转增比例双计与 10 倍量纲。
否则 Phase 4 的仲裁会把这些既有缺陷当作上游分歧报告出来。
