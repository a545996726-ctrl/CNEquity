# 升级与兼容性

升级前记录 `cne --version`，阅读[更新日志](../changelog.md)，备份配置与需要长期保留的数据。安装方法见[安装与升级](installation.md)。

## 软件与数据版本

软件版本、数据集 schema 版本与数据 revision 含义不同。软件升级不代表历史数据已迁移；数据修正也不一定改变 schema。

0.x 的次版本可能包含有说明的兼容性变化。字段、单位、主键或历史语义变化应以对应版本的迁移说明为准，不能只依据列名相同继续拼接。

## 升级步骤

1. 核对新版本的范围、已知限制与迁移要求。
2. 更新软件后执行 `cne config diff`，审阅本地配置差异；保留凭证、路径与调度选择。
3. 执行版本说明要求的迁移，并核对状态与质量报告。
4. 使用原有查询验证关键数据口径，再恢复定时任务。

迁移说明见仓库的 [contracts/migrations](https://github.com/rootSunc/CNEquity/tree/main/contracts/migrations)，常用工具见[运行脚本](../operations/scripts.md)。回退软件不一定能回退已迁移的数据，恢复时同时核对配置、软件与数据版本。

## 反馈问题

在 [Issues](https://github.com/rootSunc/CNEquity/issues) 提供版本、脱敏配置片段、最小复现和错误信息。不要上传凭证、个人湖、完整运行日志或私人实验记录。安全问题见 [SECURITY.md](https://github.com/rootSunc/CNEquity/blob/main/SECURITY.md)。
