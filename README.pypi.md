# CNEquity · 本地、可日更、可溯源的中国市场数据湖

将 A 股行情、财报、公司事件与资金面等多源数据整理为本地 Parquet，供 Python、DuckDB、Polars 和 AI agent 反复查询。包含增量采集、失败续跑、质量审计与只读控制台。

**Python 3.10+ · macOS / Linux / Windows · CLI 命令：`cne`**

[GitHub / English](https://github.com/rootSunc/CNEquity/blob/main/README.en.md) · [完整文档](https://rootsunc.github.io/CNEquity/) · [更新日志](https://github.com/rootSunc/CNEquity/blob/main/CHANGELOG.md)

## 一条命令初始化

无需账号或 token，也不必克隆仓库。在准备长期存放数据的目录执行：

```bash
pip install cnequity
cne init
```

第一次运行会生成 `configs/cnequity.toml`（数据放在当前目录的 `data/cnequity/`），然后建沪深京全市场最近 3 年的主干：证券、日历、公司行为、个股与指数日线、复权因子、行业指数；自动恢复窗口内已知退市股票的日线，获取当前交易状态并派生历史停牌，审计后发布为本地 Parquet。全市场初始化可能需要数小时，终端实时显示进度和 ETA。

**中断了，或结果里有 `warning`？重跑同一条 `cne init`**，它会保留已成功的批次，只补剩下的部分。`warning` / `degraded` 表示数据已发布、部分来源暂时覆盖不足，命令返回 0 并记下缺口；只有程序、存储或完整性错误才失败。

```bash
cne status --datasets
```

```python
from cnequity.query import load

bars = load("daily_bars", symbols=["600519.SH"])
print(bars.select("symbol", "trade_date", "close", "source").tail(10))
```

更深历史用 `cne init --profile full`（日线从 2016-01-01 起）；全市场历史 ST 证据是可选的 `cne backfill trading_status`。`cne serve` 打开本地只读控制台（http://127.0.0.1:8787）。

## 数据与研究口径

当前开发树所有 52 个数据集的字段与历史能力见[数据集目录](https://rootsunc.github.io/CNEquity/datasets/catalog/)；注册数包含可选、兼容和停用源占位，已安装版本以本机契约为准。

- 行情、复权、证券身份与交易状态；公司行为、财报、估值与股东；资金面、行业成分、宏观、新闻与监管事件。
- 可选分钟线、分笔及期货/期权数据，默认关闭；实际来源能力见[数据集目录](https://rootsunc.github.io/CNEquity/datasets/catalog/)。
- 原始价格与 hfq 因子分开保存，查询时复权；PIT 研究显式使用 `as_of` 与 `pit_mode="strict"`，当前回填不冒充过去已经观察到的版本。
- 行级来源和不可变数据版本支持复查；严格股票池与复权查询会暴露证据缺口。

## 初始化之后：每日更新

```bash
cne run daily
```

每天（含周末）运行一次：交易日跑全部日更组，然后更新公告和资讯；非交易日只更新事件流。升级版本后运行 `cne config upgrade`，把新版本的调度 step 补进配置。初始化并不填满所有数据集，范围、成本和续跑见[初始化指南](https://rootsunc.github.io/CNEquity/getting-started/initialization/)。

## 继续使用

[Python API](https://rootsunc.github.io/CNEquity/reference/python-api/) · [研究示例](https://rootsunc.github.io/CNEquity/recipes/) · [MCP 接入](https://rootsunc.github.io/CNEquity/reference/mcp/) · [运维手册](https://rootsunc.github.io/CNEquity/operations/runbook/)

项目处于 0.x 迭代阶段。仓库主分支文档可能领先于已安装版本，请用 `cne --version` 核对发布说明。运行时依赖一次安装，无需 extras；部分补充来源需要自备凭证，来源可达性与历史深度不作保证。

代码 [Apache-2.0](https://github.com/rootSunc/CNEquity/blob/main/LICENSE)；数据另受[上游许可](https://rootsunc.github.io/CNEquity/legal-and-data-sources/)约束，本包不附带数据湖。

如果它帮你省下重复搭建数据底座的时间，欢迎在 [GitHub 点一个 ⭐ Star](https://github.com/rootSunc/CNEquity)。

命令执行与数据覆盖分别报告：有效部分结果可以发布，缺口继续保留。调度验收使用 `cne status --datasets --gate`；程序、存储和完整性错误仍会失败。
