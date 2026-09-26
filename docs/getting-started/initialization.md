# 初始化、范围与续跑

适用：已经跑通小样本，需要建立正式湖或扩大历史窗口。第一次使用请先读[快速开始](quickstart.md)。

## 初始化前

```bash
cne config create
cne config validate
cne init --config configs/cnequity.toml
```

已有配置时跳过 `config create`，先用 `cne config diff` 审阅新增默认值。生成配置会写入绝对 `data.root`，macOS / Windows 默认采用 `workers=1`；直接复制模板不会执行这些处理。

`init` 按 `[job.init.phases]` 建目录、manifest、视图并回填证券、日历、公司行为、日线、指数、交易状态及派生数据。只建布局可用 `cne init --layout-only`；它不产生研究数据。

<a id="init-scope"></a>

## Init：范围、磁盘与续跑

默认 `cne init`（`--profile quick`）是沪深京全市场、近 3 年的主干，不是只拉 400 只。`--profile full` 仍是全市场，只是把主干加深到各数据集自己的历史起点，其中日线从 2016-01-01 起。

| 命令 | 实际范围 | 请求成本特征 |
|---|---|---|
| `cne init --profile demo` | 5 只 × 最近约 30 个交易日，独立 demo 湖 | 小范围，先验证 TDX 可达性 |
| `cne init`（`--profile quick`） | 沪深京全市场 × 最近 3 年；证券、日历、日线、交易状态等主干 | 全市场，按证券与缺口分页 |
| `cne init --profile full` | 还是全市场；主干按各数据集默认起点拉取，日线从 2016-01-01 起 | 深历史，页数和落盘量更多 |
| `cne backfill trading_status` | 补齐全市场历史 ST 证据 | 逐证券、按源节奏执行，宜安排长窗口 |

TDX / Baostock 的可达性、出口、上游限流、重试和机器配置都会改变耗时；`init` 启动时会打印范围；单数据集回填另有 `cne backfill DATASET --plan` 可离线审阅。`init` 没有 `--plan` 参数。

磁盘量随证券范围、历史起点、频率与修订增长，staging 还需工作空间。分钟线、5 分钟线和分笔默认关闭；全市场分钟数据通常远大于日频主干。扩范围前先小样测量，见[运行手册 · 日内数据](../operations/runbook.md#日内数据minute_bars--minute_bars_5m)。

> **进度里出现 400 只，并不表示 init 只拉了 400 只。** 日线、证券列表等主干仍然扫描全市场。`400` 只限制较慢的 **Baostock 历史 ST 状态**：首次 `init` 每轮先扫 400 **只证券**（不是 400 条数据）就暂停，避免无界等待。进度写入 checkpoint；显式运行 `cne backfill trading_status` 会自动取消这 400 只上限。同一历史起止日和 universe 会从 checkpoint 继续；改变范围会按新范围重新开一轮。

新湖如果希望主干数据和历史 ST 都达到项目约定的完整范围，按顺序跑（历史 ST 是否完整仍取决于所选市场、来源权限与证据收据）：

```bash
cne init --profile full --config configs/cnequity.toml
cne backfill trading_status --config configs/cnequity.toml

# 再抓其余日更组，并从此每个交易日执行
cne run daily --all-groups --config configs/cnequity.toml
cne run events --config configs/cnequity.toml
```

前两条最好同一天接着跑。如果要跨天续跑，给 `init --trade-date` 和 `backfill --end` 传同一个截止日，并保持 2016-01-01 起点不变，才能接着同一份 checkpoint。

这里说的「完整」不是「全部数据集都有无限历史」。项目没有一条命令能把所有数据集的全部历史一次拉完。`init` 负责证券、日历、公司行为、个股/指数日线、交易状态和派生因子这些主干；分钟线、5 分钟线和分笔默认关闭，快照型数据也无法回补源端没有提供的历史。某个可回补的数据集需要更早的历史时，使用 `cne backfill <dataset> --start ... --end ...`；各数据集能拉到哪一年，见[数据集目录](../datasets/catalog.md)。

如果中途按了 Ctrl-C，不要删除 `data/`，也不需要从头开始。原命令再跑一次会自动找到未完成的 run，并从失败批次继续；也可以显式指定：

```bash
cne init --profile full --config configs/cnequity.toml
# 或：
cne init --resume --config configs/cnequity.toml
cne run retry --run-id RUN_ID --config configs/cnequity.toml
cne status --datasets --config configs/cnequity.toml
```

Ctrl-C 之后，命令会先停住正在跑的 worker，再把没跑完的批次记成可以马上重试的 `failed`；已经成功的批次不会重拉。进程被直接杀掉也没关系：下一次命令会根据运行锁认出这个孤儿 run。某个阶段没跑完，后面的阶段和 compact 都不会开始，所以一次小范围的成功回填，不会把只覆盖部分证券的数据说成「初始化完整」。

`status --datasets` 里的 `fresh` 只表示**已经落盘的那些日期**够新。正式数据湖还会用最新日线截面，对照配置的 `[universe].ingest`、当天 active 证券，以及明确的停牌证据，做一次轻量核对：范围内每只 active 证券都得有日线，或者有明确停牌证据。如果缺了整个配置市场（例如 `all_a` 没有 BJ），初始化会在 instruments 阶段提前失败。init 没跑完、任何一只证券缺证据、或者截面核对不上，都会另外报警并返回非零。

「全市场」含北交所：上市状态、停复牌与 ST 取自北交所自己的板块页，日线历史优先走 TDX 的 BJ 专用路径，Sina 补未覆盖标的；历史 Sina 行的缺失成交额可经 TDX 逐行比对补齐。默认 universe `all_a` 覆盖沪、深、京三市的 A 股。每个数据集真正拉到哪一天，会记在 `coverage_start`。

需要更长历史时，可以一次拉满，也可以以后再补深：

```bash
cne init --profile full

# 或对单个数据集补历史
cne backfill daily_bars --start 2016-01-01 --end COVERAGE_START
```

**它跑到哪了？** 运行中会打这几类行，正常情况下不会连续静默超过 60 秒：

| 行 | 含义 |
|------|------|
| `Step <名字> starting` / `Step <名字> success in Ns` | 步骤进出 |
| `daily_bars: 5,283 symbol(s) over … → 53 batch(es) … on 4 lane(s)` | 这一趟扫描的规模，开跑前就打出来 |
| `daily_bars 8/53 batches · 12,400 rows · 1m24s elapsed · ~7m55s left` | 滚动进度；凑满一轮 lane 后才给剩余时间 |
| `still working: daily_bars 4m12s (no output for 1m02s)` | 心跳，静默满 60 秒时点名当前步骤 |

启动时打印的 `Logging to …/logs/cne-init-<时间戳>.log` 是本次运行的日志文件（目录可用 `CNE_LOG_DIR` 覆盖）。另开一个终端也可以查：

```bash
cne status --run latest --config configs/cnequity.toml
```

**成本按标的数算，不按天数算。** `daily_bars` 的历史主路径逐标的抓取，所以 `--start D --end D` 只拉一天，默认仍会扫描整个配置范围；多年窗口的差别主要在每个标的返回多少根 K 线。想快速验证请用 `--symbols` 缩小范围，并先跑 `--plan`：

```bash
cne backfill daily_bars --symbols 600519.SH,000001.SZ --start 2026-09-15 --end 2026-09-15 --plan
```


## 验收，而不只看完成提示

```bash
cne status --datasets --config configs/cnequity.toml
cne stats show --config configs/cnequity.toml
cne audit --full --config configs/cnequity.toml
```

`fresh` 是新鲜度；审计健康也不等于任意历史窗口都可研究。全量审计会写报告，且按源开关执行外部核验，详见[命令副作用](../reference/cli-surface.md)。需要历史股票池时，再按真实研究窗口执行 `audit --full --research-start ... --research-end ...`，或使用严格 profile 查询。

有源码 checkout 时可用 `scripts/accept_backfill.py` 验收幂等性、覆盖起点和消费层，见[回填完成验收](../operations/runbook.md#回填完成验收)。这些脚本不随 PyPI 包安装。

## 接下来

- 持续运行：[运行手册](../operations/runbook.md)；日更与事件流分别调度。
- 扩范围前：[请求成本与源保护](../operations/fetch-policy.md)。
- 查询研究数据：[查询指南](../datasets/query-guide.md)与[研究示例](../recipes/README.md)。
