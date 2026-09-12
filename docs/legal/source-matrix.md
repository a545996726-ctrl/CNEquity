# 来源合规矩阵

`sources/SOURCES.yml` 是数据集注册表之外的来源合规登记。它只登记来源标签、访问方式、条款审阅状态和保守的使用结论，不替代任何上游服务协议，也不向下游授予数据使用或再分发许可。

## 覆盖范围

矩阵的来源集合来自 `src/cnequity/domain/datasets.py` 中每个 `DatasetSpec` 的 `primary_source`、`backup_source` 和 `backfill_source`。因此备源和只用于历史回填的来源也必须登记。`derived` 是一个特殊的显式来源标签：它表示本地派生结果，不能把它当作独立的数据许可来源。

当前登记覆盖 14 个来源标签。可以用下面的只读检查确认注册表与矩阵仍然一致：

```python
from cnequity.compliance.source_policy import load_source_policies, required_sources

policies = load_source_policies()
assert required_sources() <= policies.keys()
```

截至 2026-08-29，矩阵已经记录东方财富和同花顺的官方用户许可页面及限制性结论；其余来源仍保持待核实状态。

2026-09-13 新增 `ths_official`（同花顺官方 API，fuyao.aicubes.cn）。它与 `ths` 是两个不同的来源标签：`ths` 抓取 10jqka 公开页面且并非已登记客户端，`ths_official` 是账号签发 API Key 的已登记客户端，因此 `authentication` 记为 `api_key`。但站内文档（含 llms-full.txt 全文聚合）没有任何关于数据商用、再分发、缓存或留存的条款，只有一句「数据权限以官网与账号授权为准」，所以 `commercial_use`、`redistribution`、`cache_allowed` 全部保持 `unknown`，`cne sources policy ths_official` 因此返回 `review_required`。上游仓库的 MIT 许可只覆盖代码，不涉及数据。

同日补登 `bse`（北京证券交易所）。它此前从未登记，却已向 `daily_bars` 写入 654 行、向
`trading_status` 写入两个子标签，使得 `cne sources policy bse` 查不到任何条款。
北交所站点在境外出口返回 403，条款页不可达，因此权限字段全部保持 `unknown`。
子标签 `bse_*` 按前缀继承本条政策，无需改动适配器。这里的“已审阅”只表示维护者把页面中的明确限制转换为保守的机器状态，不等于律师出具的完整法律意见。

## 字段与保守语义

每个来源至少包含 `owner`、`access_type`、`tos_url`、`tos_reviewed_at`、`authentication`、`personal_use`、`commercial_use`、`cache_allowed`、`redistribution`、`rate_limit`、`retained_payloads`、`legal_status` 和 `notes`。未核实的事实必须填写精确的 `unknown`，不能用空值、猜测的日期或含糊的“公开所以允许”替代。

`personal_use`、`commercial_use`、`cache_allowed` 和 `redistribution` 只有明确的 `allowed`（或布尔 `true`）才会被使用策略视为允许；`unknown` 一律产生待审阅/阻断结果。`tos_reviewed_at` 只有在对应条款确实被人工审阅后才可写日期；代码仓库的 Apache-2.0 许可证不改变上游数据的限制。

`policies_for_dataset("daily_bars")` 可按主源、备源和回填源汇总政策；`usage_profile(...)` 可对个人使用、商业使用、缓存或再分发意图作保守风险判断。该 API 只提供机器可读的风险门槛，不构成法律意见。

## 生成与维护流程

矩阵不是运行时从网络抓取的清单。新增或修改 `DatasetSpec` 时，维护者应：

1. 重新计算注册表中的唯一来源标签，并为每个新标签补齐矩阵必填字段；`derived` 只能在确实为本地派生时标记为 `true`。
2. 针对来源方的当前官方条款、接口说明、许可文本和限速/缓存规则逐项核实。无法核实的项保持 `unknown`；不要根据接口无需登录、网页可访问或其他来源的许可推断允许商业使用或再分发。
3. 在同一变更中更新 `tos_url`、`tos_reviewed_at` 和 `notes`，说明审阅范围与日期；复合标签（如 `eastmoney_kline+sina_global`）必须分别审阅所有组成来源。
4. 运行来源政策单元测试与 `ruff check`。若政策文件改用外部路径，调用方应在使用前显式运行 `validate_source_policies`，不要绕过校验。
5. 发生条款变更、来源迁移、服务停用或权限撤回时，回退为 `unknown` 或写入明确限制，并保留变更说明；不要把历史上的“曾经可访问”当作当前授权。

本矩阵只描述仓库实现所观察到的访问方式和待确认事项。使用者仍须自行阅读上游条款、评估所在司法辖区要求，并对采集、保存、商业使用及再分发承担责任。
