# 贡献指南

**只用数据的用户**：`pip install cnequity` → 见 [快速开始](docs/getting-started/quickstart.md)。  
本文面向向仓库提交代码的贡献者。

安全问题请走 [SECURITY.md](SECURITY.md)，不要开公开 issue。

提较大功能前，请先看 [定位与差异](docs/comparison.md)（本仓库只做数据层）和
[许可与数据合规](docs/legal-and-data-sources.md)。

## 适合第一次贡献的方向

不需要先理解整个编排引擎。以下工作可以独立完成，而且对用户可见：

- 为现有数据集补一条离线 recipe、查询示例或字段说明
- 给已有 adapter 增加 schema 边界测试和清晰的源限制说明
- 修复中英文文档、CLI help 与实际默认值之间的漂移
- 改进 `cne demo`、`cne status` 或 dashboard 的错误提示

数据源接入需要同时提交来源、保留期、限流和合规说明；请不要把真实数据文件提交到仓库。
较大的方向可以先在 issue / discussion 中确认范围。

版本、数据契约、迁移与发布门禁见
[`docs/development/release-governance.md`](docs/development/release-governance.md)。

## 环境

```bash
python -m venv .venv && source .venv/bin/activate
python -m pip install --upgrade pip   # PEP 735 --group 需要 pip >= 25.1
pip install -e . --group dev
pre-commit install                    # 提交前自动跑 ruff check / ruff format
# 无 extras：运行时依赖全部随包安装
# 见 docs/getting-started/installation.md
```

`pre-commit install` 只需每个 clone 执行一次。它跑的就是 CI `quality` job 的那两项检查，
把格式问题挡在提交前，而不是推送后才变红。

请勿提交 `configs/cnequity.toml`、`data/`、`logs/`。

```bash
ruff format .
ruff check .
pytest                 # 全部
pytest tests/unit      # 快速
pytest tests/integration
```

本地预览文档站（CI 会用 `mkdocs build --strict` 校验，坏链会直接失败）：

```bash
mkdocs serve      # http://127.0.0.1:8000
```

## 提交与 PR

提交信息用 [Conventional Commits](https://www.conventionalcommits.org/)，仓库最近 200 个提交中
198 个遵循此格式：

```
<type>(<scope>): <简短描述>
```

常用 type：`feat` `fix` `docs` `refactor` `perf` `test` `chore` `ci`。
scope 用模块名（`ths_official`、`quality`、`symbols`、`cli`…）。描述用祈使句、不加句号。

正文说明**为什么**这样改，而不是复述 diff。破坏性变更加 `!`（如 `feat(query)!:`）并在正文写明迁移方式。

PR 合入前 CI 必须全绿，本地可提前跑掉大部分：

| CI job | 本地等价命令 |
| --- | --- |
| `quality` — lint | `ruff check . && ruff format --check . && shellcheck scripts/*.sh` |
| `quality` — 离线基准 | `python scripts/benchmark_offline.py --check --max-elapsed-seconds 10 --max-concurrency 2` |
| `quality` — 前端产物 | `cd frontend && npm ci && npm run check` |
| `test` | `pytest` |

`test` 在 Linux（Python 3.10/3.12/3.13/3.14）、Windows 与 macOS 上各跑一遍。**Windows 是一等目标**：
路径、文件锁和换行差异都算回归。往 TOML 里写路径时务必用 `path_for_toml()`，裸 `Path` 在
`C:\Users\…` 下会变成非法转义。

## 约定

- 代码在 `src/cnequity/`，按职责拆分（`domain`、`adapters`、`orchestrator`、
  `steps`、`storage`、`derive`、`quality`、`query`、`config`、`cli`）。
- Step 按 L0–L8 分层放在 `steps/`；新模块需在 `steps/__init__.py` 中 import 以注册。
- 新数据集：在 `domain/schemas.py` 声明 schema + 主键、分区键，以及溯源列
  （`source`、`data_version`、`fetched_at`）。
- Adapter 保持薄（I/O 与源侧 quirks）；归一化放在 `steps/` / `domain/`。
- 单测默认离线；需要联网的测试须明确标记。
- 非平凡架构取舍写入 `docs/adr/`（复制 `0000-template.md`；ADR 正文保持英文）。

## 新增数据集清单

1. Schema + 主键 + 分区键
2. `@register_step`，填好 `depends_on` / `group` / `requires_workers`
3. 写时 schema 校验通过
4. 归一化单测 + 至少一个边界用例
5. 更新 [`docs/datasets/catalog.md`](docs/datasets/catalog.md) 与
   [`docs/datasets/sources.md`](docs/datasets/sources.md)
