# CNEquity · 本地、可日更、可溯源的中国市场数据湖

将 A 股行情、财报、公司事件与资金面等多源数据整理为本地 Parquet，供 Python、DuckDB、Polars 和 AI agent 反复查询。包含增量采集、失败续跑、质量审计与只读控制台。

**Python 3.10+ · macOS / Linux / Windows · CLI 命令：`cne`**

[GitHub / English](https://github.com/rootSunc/CNEquity/blob/main/README.en.md) · [完整文档](https://rootsunc.github.io/CNEquity/) · [更新日志](https://github.com/rootSunc/CNEquity/blob/main/CHANGELOG.md)

## 先跑通一个查询

基础体验无需账号或 token，也不必克隆仓库：

```bash
pip install cnequity
cne init --profile demo
```

默认抓取 5 只股票、最近约 30 个交易日的真实日线，写入独立的 `data/cnequity-demo/`，配置为 `configs/cnequity.demo.toml`。耗时依赖 TDX 可达性。

```python
from cnequity.query import load

bars = load("daily_bars", data_root="data/cnequity-demo")
print(bars.select("symbol", "trade_date", "close", "source").tail(10))
```

```bash
cne serve --config configs/cnequity.demo.toml
# http://127.0.0.1:8787
```

无法连接数据源时，可使用独立的离线样例：

```bash
cne init --profile sample --data-root data/cnequity-sample --config-out configs/cnequity.sample.toml
cne query --config configs/cnequity.sample.toml --sql "SELECT * FROM daily_bars LIMIT 5"
```

所有合成行标记为 `source=mock`，仅用于验证安装和读写链路，不能用于研究。

## 数据与研究口径

当前开发树所有 52 个数据集的字段与历史能力见[数据集目录](https://rootsunc.github.io/CNEquity/datasets/catalog/)；注册数包含可选、兼容和停用源占位，已安装版本以本机契约为准。

- 行情、复权、证券身份与交易状态；公司行为、财报、估值与股东；资金面、行业成分、宏观、新闻与监管事件。
- 可选分钟线、分笔及期货/期权数据，默认关闭；实际来源能力见[数据集目录](https://rootsunc.github.io/CNEquity/datasets/catalog/)。
- 原始价格与 hfq 因子分开保存，查询时复权；PIT 研究显式使用 `as_of` 与 `pit_mode="strict"`，当前回填不冒充过去已经观察到的版本。
- 行级来源和不可变数据版本支持复查；严格股票池与复权查询会暴露证据缺口。

## 建立长期数据湖

```bash
cne config create
cne config validate
cne init
cne run daily --all-groups
cne run events
cne status --datasets
```

默认 `init` 建沪深京全市场最近 3 年的主干；`--profile full` 加深历史，其中日线从 2016-01-01 起。初始化并不填满所有数据集。默认 400 只上限仅作用于 Baostock 历史 ST 扫描，后续用 `cne backfill trading_status` 继续；完整性仍需按市场与证据核验。

日更和事件流是两个入口：`--all-groups` 遍历日更组，`run events` 更新公告和资讯，周末也可运行。裸 `run daily` 只跑核心 waves。范围、成本和恢复见[初始化指南](https://rootsunc.github.io/CNEquity/getting-started/initialization/)。

## 继续使用

[Python API](https://rootsunc.github.io/CNEquity/reference/python-api/) · [研究示例](https://rootsunc.github.io/CNEquity/recipes/) · [MCP 接入](https://rootsunc.github.io/CNEquity/reference/mcp/) · [运维手册](https://rootsunc.github.io/CNEquity/operations/runbook/)

项目处于 0.x 迭代阶段。仓库主分支文档可能领先于已安装版本，请用 `cne --version` 核对发布说明。运行时依赖一次安装，无需 extras；部分补充来源需要自备凭证，来源可达性与历史深度不作保证。

代码 [Apache-2.0](https://github.com/rootSunc/CNEquity/blob/main/LICENSE)；数据另受[上游许可](https://rootsunc.github.io/CNEquity/legal-and-data-sources/)约束，本包不附带数据湖。

如果它帮你省下重复搭建数据底座的时间，欢迎在 [GitHub 点一个 ⭐ Star](https://github.com/rootSunc/CNEquity)。
