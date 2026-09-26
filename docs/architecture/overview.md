# 产品方向与边界

CNEquity 面向希望自行管理中国市场研究数据的用户：在本地持续采集，使用统一的数据口径查询，并能发现缺口、追溯来源和恢复失败任务。项目方向与功能范围由维护者统一规划；用户可以通过 Issue 反馈场景、问题与建议。

## 核心能力

- 本地数据湖：用 Parquet 保存数据，通过 Python、DuckDB、Polars 或 MCP 读取。
- 持续更新：初始化、日更、事件采集和历史回填使用明确的配置与范围。
- 质量可见：来源、单位、覆盖范围与失败状态可检查；新鲜度不等于历史完整。
- 版本可追溯：修正通过新数据版本发布；需要长期复现时保存研究快照、查询参数与软件版本。

## 使用前应了解的边界

安装不附带市场数据，采集依赖上游可用性和授权。可选凭证来源需要自行配置。当前支持范围见[数据集目录](../datasets/catalog.md)与[数据源限制](../datasets/sources.md)。

历史回填不能证明数据在过去已被观察到。严格研究应显式选择 PIT、股票池与复权校验；证据不足可能导致查询拒绝返回。见[查询指南](../datasets/query-guide.md)。

数据集分别发布，不提供整个湖的跨表原子事务。固定版本读取、版本保留与快照的边界见[Python API](../reference/python-api.md)。

## 从哪里开始

[快速开始](../getting-started/quickstart.md) → [初始化与续跑](../getting-started/initialization.md) → [运行手册](../operations/runbook.md)。遇到问题先按[故障排查](../operations/troubleshooting.md)定位失败范围。

产品数据流见[采集到查询](data-flow.md)，本地文件管理见[数据湖目录](lake-layout.md)。
