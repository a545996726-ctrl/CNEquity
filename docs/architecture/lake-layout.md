# 数据湖目录

湖根目录由配置中的 `[data].root` 决定。不同演示湖和正式湖应使用独立路径。

| 路径 | 用途 |
|---|---|
| `staging/` | 尚待合并的批次数据；失败恢复可能依赖它 |
| `curated/`、`derived/` | 合并工作副本及兼容布局；直接扫描不固定已发布版本 |
| `meta/revisions/` | 数据版本收据、当前版本指针与不可变数据文件 |
| `meta/state/` | 更新水位与覆盖状态 |
| `meta/quality/` | 质量检查与来源差异报告 |
| `meta/raw/` | 原始响应归档 |
| `meta/source_snapshots/` | 用于来源核验的快照 |
| `meta/locks/` | 运行协调所用的锁文件 |
| `duckdb/` | SQL 视图数据库 |
| `logs/` | 运行日志 |
| `backups/` | 元数据备份，不能替代完整数据快照 |

## 日常管理

通过 Python API 或 `cne query` 读取已发布数据。不要直接修改版本指针、generation 文件或把备份放进 `curated/`。长期保存使用[研究快照](../reference/python-api.md)，日志和报告留在自己的本地目录。

清理 staging 前先确认任务已完成并已合并。锁文件存在不等于仍有进程持锁，不要靠删除锁文件恢复运行。见[排障](../operations/troubleshooting.md)。

分区布局发生变化时，旧目录仍可能可读，但与新目录重叠会造成重复主键。按[迁移脚本说明](../operations/scripts.md)先预演、备份，再统一布局。
