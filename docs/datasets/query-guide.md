# 查询指南

下游读数的推荐路径是 `cnequity.query.load()`。本文说明复权、Universe、PIT 与常见陷阱。

API 签名见 [Python API 参考](../reference/python-api.md)。

## 选择读取入口

| 入口 | 提供的能力 | 需要自己处理 |
|---|---|---|
| `load()` | 物化数据，按参数做复权、PIT、股票池与严格校验 | 显式选择模式和证据窗口 |
| `scan()` | 原始 LazyFrame，日期与标的过滤、版本选择 | 不含复权、PIT、股票池 |
| `cne query --sql` | 本地视图上的只读 SELECT | SQL 的口径与覆盖校验 |
| 直读 Parquet | 脱离运行时读取开放文件 | 版本、去重、PIT、复权和股票池 |

## 基本用法

```python
from cnequity.query import load, scan, list_datasets

# 物化 DataFrame
df = load("daily_bars", start="2024-01-01", end="2024-12-31")

# Lazy scan（大窗口）
lf = scan("daily_bars", start="2020-01-01", end="2024-12-31")

# 湖内数据集概览（含 history_mode / backfill_source / coverage_*）
meta = list_datasets()  # 或 list_datasets(config=cfg)
# snapshot_only → 无诚实历史；coverage_start 为盘上分区起点
```

配置解析顺序：`config=` → `data_root=` → `configs/cnequity.toml`

`list_datasets()` 是研究侧的**数据发现入口**，边界不证明中间无缺口：`history_mode` 区分 `by_date` / `snapshot_with_backfill` / `snapshot_only`；`coverage_start` 按已发布数据与分区边界解析（含 `report_period=YYYYQn`）。详见 [数据集目录 — 历史可用性](catalog.md#历史可用性history_mode)。

## 复权（adjust）

仅适用于含价量列的数据集（主要是 `daily_bars`）。

| 参数 | 行为 |
|------|------|
| `adjust=None` | 原始未复权 OHLC |
| `adjust="hfq"` | 用后复权因子乘价格；增列 `adj_open`…`adj_close`、`adj_is_exact` |
| `adjust="qfq"` | 查询窗口内按最新 bar anchor 将 hfq 因子归一化 |

```python
bars = load(
    "daily_bars",
    start="2020-01-01",
    end="2024-12-31",
    adjust="hfq",
)
```

### strict_adj

```python
bars = load("daily_bars", start="2024-01-01", adjust="hfq", strict_adj=True)
```

- `True`：缺因子行抛出 `ReaderError`，不填充 1.0
- `False`（默认）：缺因子时 `adj_is_exact=False`，价格按 factor=1.0 降级

**研究建议**：量化回测用 `hfq` + `strict_adj=True`；qfq 窗口 anchor 会漂移，不适合长期研究复现。

需要固定结果时，用 `revision_map` 同时固定价格、因子及股票池依赖，并留存所需质量证据、配置和软件版本。具体保证及快照边界见 [Python API](../reference/python-api.md#固定组合读取的数据版本)。

`index_bars` 是指数点位，不是个股价格，不支持 `adjust=`；请直接使用原始指数水平。

### 存储约定

- 湖内 `daily_bars` 存**未复权**价
- `adj_factors` 在 `derived/`，仅存 `hfq`（`adjust_type="hfq"`）
- DuckDB 视图 `daily_bars_adj` / `daily_bars_hfq` / `daily_bars_qfq` 与上述语义一致

## Universe 过滤

```python
bars = load(
    "daily_bars",
    start="2024-01-01",
    profile="cn_a_sh_sz_research_v1",
)
```

`cn_a_sh_sz_research_v1` 明确限定沪深两市，并在读取时严格核验证券身份、
逐日交易状态、历史 ST 和退市证据；缺少收据会拒绝返回。它不会把北交所缺口
藏在“全 A”标签下。需要包含北交所时可显式选
`cn_a_all_experimental_v1`，但该画像仍标为实验性，不应当作已验收的研究口径。
画像版本与哈希的用法见[股票池画像](../reference/universe-profiles.md)。

旧的 `universe="all_a"` / `"all_a_sh_sz"` 参数仍供兼容使用；前者会发出
`DeprecationWarning`，两者都不会自动成为严格画像。兼容过滤的基础规则：

1. **instruments**：`list_date <= trade_date`，且未退市或 `delist_date > trade_date`
2. 排除 CDR（689xxx.SH）
3. **trading_status**（仅有数据的日期）：剔除 ST/*ST 与停牌

### 历史 ST 限制

日更只抓当天 `trading_status`。停牌可由 `cne derive trading_status --start/--end` 按年重建，覆盖可与 `daily_bars` 同起点（约 2001）。**ST 标签**由 Baostock 的逐标的 `isST` 历史与可选 Tushare BJ 历史源共同提供；只有生成了完整、版本化的 `historical_st_evidence` 收据，才能把对应窗口用于研究。部分回补（例如仅从 2016 年开始）不能证明 2001 年起的历史 ST 已剔除。

收据可通过重叠的深历史范围与较新尾段范围合并，但新增标的必须有首个交易日证据。北交所（BJ）可通过显式配置的 Tushare Pro 回补：2016 年使用 `bak_basic` 历史简称，2017-01-01 起使用 `stock_st`；接口需要 token，2016 年以前仍会作为源端能力限制阻塞，不能把接口空结果当成 normal。未配置 Tushare 时，BJ 仍会显式阻塞。审计项 `trading_status_coverage_start` 区分总覆盖与 `st_coverage_start`，历史研究应使用 `cne audit --full --research-start ...` 复核。

显式研究画像会自动启用严格校验。若仍使用兼容参数，可加
`strict_universe=True` 校验逐日状态和请求范围的历史 ST 证据；缺收据或
包含无历史 ST 来源的 BJ 标的会抛出 `UniverseCoverageError`。
默认兼容读取适合探索，不应直接作为长历史回测输入。

### 交易所覆盖

`all_a` 包含沪深北。北交所证券名单与简称在日更时由 BSE 行情板补齐；TDX 名单枚举与 TDX 日线协议的市场能力不能混为一谈。

- BJ 日线历史优先走专用 TDX 路径（market id 2），当期 tip 可用 BSE 板快照，Sina 补未覆盖部分。显式小范围历史请求会跳过无关的全市场快照。
- Sina 历史行可能缺 `amount`。`--tdx-amount-repair` 只在 OHLC 一致且成交量差小于一手时补成交额；旧代码等未被 TDX 服务的行仍保留缺失和 finding。
- `--bse-tip-repair` 只对已存当期日线核对 BSE 快照并补量额，不重新请求 Sina 历史。它不能恢复过去的 BSE 快照。

这些路线补的是行情；BJ 历史 ST 仍需独立证据。完整来源与修复入口见[数据源说明](sources.md#daily_bars)。

## PIT（Point-in-Time）

PIT 数据集：`financial_statement_items`、`announcement_index`、
`share_structure`、`shareholder_counts`、`top_holders`。

```python
items = load(
    "financial_statement_items",
    items=["roe", "net_profit"],
    as_of="2024-04-30",
    pit_mode="strict",
)
```

- `pit_mode="strict"` 过滤 `announce_date`、已知 `available_at`/
  `source_published_at` 和 `fetched_at`/`observed_at` 均不晚于 `as_of`，并排除
  `reconstructed` 回填行；`pit_mode="best_effort"` 可保留它们，但返回
  `pit_is_exact=False`。
- 同一 `(symbol, report_period, item)` 取 `announce_date` 最新一行
- 禁止用 `end=` 代替 `as_of` 做财报对齐

PIT 仍可使用 `start` / `end` 限定数据自身的日期列：公告与股东数据按
`announce_date` / `change_date` / `count_date` / `record_date` 过滤；财报的
`report_period` 是季度字符串，会按与日期边界相交的季度过滤。`as_of` 仍然是
公告可见时间，不能被日期窗口替代。

`announce_date` 在主键里，所以财报修订是**新增一版**而不是覆盖原值：同一科目可以同时存在
首发值和修订值。默认只返回 `as_of` 当时生效的那一版；要看修订本身（修订幅度和方向本身
就是信号）加 `all_vintages=True`：

```python
# 000001.SZ 2024Q1 营收被改过几次、每次改了多少
load(
    "financial_statement_items",
    symbols=["000001.SZ"], items=["revenue"],
    as_of="2026-07-21", pit_mode="strict", all_vintages=True,
).select("report_period", "announce_date", "item_value")
```

注意：回填行以 `source=eastmoney_backfill` 标记，并视为
`pit_quality="reconstructed"`。默认省略 `pit_mode` 是 0.x 兼容模式：仍按
`fetched_at` 截止，但不提供严格 PIT 保证；研究代码请显式传
`pit_mode="strict"` 并记录该选择。日更逐日累积、保存真实双时态列的版本才满足
严格模式（见 [schema](schema.md#financial_statement_items)）。
回填默认自 2001 起（东财）；`list_datasets()` 的 `coverage_start` 为盘上实际起点。

## 符号与列过滤

```python
load("daily_bars", symbols=["600519.SH", "000001.SZ"], start="2024-01-01")
load("financial_statement_items", items=["roe"], as_of="2024-06-30", pit_mode="strict")
```

Symbol 格式：`{code}.{SH|SZ|BJ}`，与 `domain/symbols.py` 一致。

## DuckDB SQL

```bash
cne query --sql "SELECT * FROM instruments LIMIT 5"
```

常用视图：

| 视图 | 说明 |
|------|------|
| `daily_bars` | 未复权 |
| `daily_bars_hfq` | 后复权价列 |
| `daily_bars_qfq` | 前复权价列 |
| `daily_bars_adj` | 含 adj_* 与 adj_is_exact |
| `{dataset}` | 各 curated/derived 数据集 |

数据库：`{data.root}/duckdb/cnequity.duckdb`（只读连接）。

## 直读 Parquet

不依赖本项目运行时：

```python
import polars as pl
pl.scan_parquet("data/cnequity/curated/daily_bars/**/*.parquet")
```

需自行实现复权与 universe 逻辑；生产推荐 `load()`。

## 分区裁剪

`query/parquet_scan.py` 按 `partition_col` 与日期范围裁剪 Hive 分区目录。大窗口查询优先 `scan()` + lazy 算子链。

### 成交口径的分钟重采样

TDX 的零成交分钟可能沿用昨收或最近报价。需要成交口径时，从完整的 1m 数据重采样，避免把未成交报价混入 OHLC：

```python
from cnequity.query import load, resample_trade_bars

minute = load("minute_bars", start="2026-09-15", end="2026-09-15",
              symbols=["603869.SH"])
five_minute = resample_trade_bars(minute, "5m")
```

支持 5m、15m、30m、60m；分别按 09:30 和 13:00 对齐，不跨午休。OHLC 只计入 `volume > 0` 或 `amount > 0` 的分钟；后者可保留股数被供应商取整为零的小额成交。区间内无成交时保留末报价且量额为零，该价格仍不是成交价格。重复时间戳、午休记录、日期错配和缺组成分钟都会报错，完全没有记录的区间不会凭空补齐。结果不覆写原始湖，也不继承输入行的来源标签；研究应同时记录输入 revision，重采样无法恢复供应商丢失的成交细节。

## 错误处理

| 异常 | 常见原因 |
|------|----------|
| `ReaderError: unknown dataset` | 名称拼写或数据集未注册 |
| `ReaderError: no parquet data` | 未 init/compact 或路径错误 |
| `ReaderError` (strict_adj) | 缺 adj_factors 覆盖 |
| PIT 无 `as_of` | 可能包含未来公告（不推荐） |

## 相关文档

- [Python API 参考](../reference/python-api.md)
- [产品边界](../architecture/overview.md)
- [产品边界](../architecture/overview.md)

## 衍生品研究

逐合约期货、期权链、连续序列与 Greeks 不使用股票 universe/复权假设。完整查询与质量验收例子见 [商品期货与期权](../recipes/derivatives.md)。
