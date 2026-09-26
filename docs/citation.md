# 引用 cnequity

如果 cnequity 帮助了你的论文、研究报告或数据工程，请引用仓库。GitHub 会读取根目录的 [`CITATION.cff`](https://github.com/rootSunc/CNEquity/blob/main/CITATION.cff)，并在仓库首页提供 “Cite this repository” 入口。

## 软件引用

```text
CNEquity Contributors. (2026). CNEquity: A free, self-hosted historical
financial data infrastructure for China markets, starting with A-shares
(Version <your installed version or commit>). Apache-2.0.
https://github.com/rootSunc/CNEquity
```

版本化研究请同时记录：

- `cnequity` 版本或 Git commit
- 数据湖的 `coverage_start` / `coverage_end`、依赖 revision 映射与研究快照身份
- `adjust`、`strict_adj`、profile / `scope_hash`、`strict_universe`、`as_of` 与 `pit_mode` 口径
- 上游数据源及其许可限制

软件许可证是 Apache-2.0；落盘行情、公告和财报仍受上游条款约束，不能仅凭软件引用获得再分发权。详见[许可与数据合规](legal-and-data-sources.md)。
