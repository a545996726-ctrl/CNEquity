"""Lake maintenance: `run compact`, `run clean`, `derive`, `stats`.

What you run against a lake that already exists, to keep its shape rather than
to change what it holds.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import date

import click
import polars as pl

from cnequity.cli._root import cli, run
from cnequity.cli._shared import (
    _cfg,
    _run_status_exit_code,
    attach_log_file,
    config_option,
    parse_date_option,
)
from cnequity.derive.adj_factors import compute_adj_factors
from cnequity.domain.datasets import RESAMPLED_MINUTE_DATASETS
from cnequity.domain.market_time import shanghai_today
from cnequity.orchestrator.engine import JobEngine
from cnequity.orchestrator.manifest import Manifest
from cnequity.query.parquet_scan import scan_parquet_files
from cnequity.storage.revisions import prune_revision_generations
from cnequity.storage.source_snapshots import (
    DEFAULT_SNAPSHOT_RETENTION_DAYS,
    clean_source_snapshots,
)
from cnequity.storage.staging_cleanup import (
    DEFAULT_LOG_RETENTION_DAYS,
    clean_run_logs,
    clean_staging,
)


@run.command("compact")
@config_option
@click.option("--run-id", default=None, help="只发布这一次 run；默认处理所有待发布的 run。")
def compact(config_path: str, run_id: str | None):
    """把 staging 里已经结束、但还没发布的 run compact 进 curated。

    \b
    不带 --run-id 时逐个处理所有这样的 run：进程已结束、有暂存文件、还没有成功的 compact。
    正在跑的 run 和 init run（由 `cne init` 自己续跑）不动；每个数据集仍受 compact
    门禁保护，有未完成批次的数据集留给 `cne run retry`。
    """
    cfg = _cfg(config_path)
    attach_log_file(cfg, "run-compact")
    engine = JobEngine(cfg)
    if run_id:
        out = engine.run_step("compact", shanghai_today(), run_id)
        click.echo(
            json.dumps(
                {"run_id": run_id, "rows_written": out.get("rows_written", 0), **out},
                indent=2,
                default=str,
            )
        )
        return

    pending = _unpublished_runs(cfg, engine)
    if not pending:
        click.echo("没有待发布的 staging：所有已结束的 run 都已经 compact。")
        return
    results = []
    worst = 0
    for rid in pending:
        out = engine.run_step("compact", shanghai_today(), rid)
        status = str(out.get("status", "success"))
        click.echo(f"compact {rid}：{status}", err=True)
        results.append({"run_id": rid, "rows_written": out.get("rows_written", 0), **out})
        exit_code = _run_status_exit_code(status)
        if exit_code == 1 or worst == 0:
            worst = exit_code
    click.echo(json.dumps({"runs": results}, indent=2, default=str))
    if worst:
        raise SystemExit(worst)


def _unpublished_runs(cfg, engine: JobEngine) -> list[str]:
    """Finished runs with staged files and no successful compact, oldest first.

    These used to be found by hand — the runbook said to look for stranded
    runs and compact them one `--run-id` at a time, while a bare
    `cne run compact` only ever touched the latest run.
    """
    from cnequity.storage.staging_cleanup import list_staging_run_ids

    # A killed process leaves its row `running`; close those first so their
    # staged facts are not mistaken for live work forever.
    engine.manifest.reconcile_orphaned_runs(
        stale_after_seconds=cfg.batch_stale_seconds, locks_root=cfg.meta_root
    )
    staged = list_staging_run_ids(cfg.staging_root)
    pending = []
    for record in reversed(engine.manifest.list_runs()):
        rid = str(record["run_id"])
        # Name the in-flight states, not the terminal ones, so a new terminal
        # status is never silently skipped. Init publishes through its phases.
        if record["status"] in ("running", "stale") or record["job_name"] == "init":
            continue
        if rid not in staged:
            continue
        batches = engine.manifest.get_batches_for_run(rid)
        if any(b["dataset"] == "compact" and b["status"] == "success" for b in batches):
            continue
        pending.append(rid)
    return pending


def _derive_trading_status(cfg, *, start: date | None, end: date | None) -> dict:
    """Run the derive step and publish it, as the daily job would.

    The rows have to reach a committed revision to be worth anything: a
    consumer reading the lake — including `daily_bars`'s own interior-gap
    check — reads the committed generation, not the mutable curated directory
    a direct write would land in. So this is a one-step run through the
    engine, followed by the same compact the daily job ends with.
    """
    engine = JobEngine(cfg)
    trade_date = shanghai_today()
    run_id = engine.manifest.start_run(
        "derive_trading_status",
        {
            "trade_date": trade_date.isoformat(),
            "derive_start": start.isoformat() if start else None,
            "derive_end": end.isoformat() if end else None,
        },
    )
    context = {"derive_start": start, "derive_end": end, "derive_full": True}
    summary: dict = {"run_id": run_id}
    try:
        derived = engine.run_step("trading_status_derive", trade_date, run_id, context)
        summary["rows_staged"] = derived.get("rows_written", 0)
        summary["compact"] = engine.run_step("compact", trade_date, run_id)
    except Exception as exc:
        engine.manifest.finish_run(run_id, "failed", error_message=str(exc))
        raise
    step_statuses = {
        str(derived.get("status", "success")),
        str(summary["compact"].get("status", "success")),
    }
    if step_statuses.intersection({"failed", "blocked"}):
        status = "failed"
    elif step_statuses.intersection({"warning", "degraded"}):
        status = "degraded"
    else:
        status = "success"
    engine.manifest.finish_run(
        run_id,
        status,
        rows_written=summary["rows_staged"],
    )
    persisted = engine.manifest.get_run(run_id)
    summary["status"] = str(persisted["status"]) if persisted is not None else status
    summary.update(engine._public_outcome(run_id))
    return summary


def _require_minute_input(cfg, dataset: str) -> None:
    """Either minute dataset will do; `_published_derive` can only demand all."""
    from cnequity.orchestrator.outcomes import InputUnavailableError
    from cnequity.query.parquet_scan import dataset_has_parquet
    from cnequity.storage.read_context import read_root

    if not any(
        dataset_has_parquet(read_root(cfg, name)) for name in ("minute_bars", "minute_bars_5m")
    ):
        raise InputUnavailableError(
            f"{dataset}: 湖里没有 minute_bars 或 minute_bars_5m，先开启 [minute_bars] 并回填分钟线"
        )


@contextmanager
def _published_derive(cfg, dataset: str):
    """Make CLI derives visible to revision-aware readers under the writer lock."""
    from cnequity.file_lock import lake_mutation_lock
    from cnequity.orchestrator.outcomes import (
        InputUnavailableError,
        step_outcome,
    )
    from cnequity.orchestrator.run_lock import run_lock
    from cnequity.query.parquet_scan import dataset_has_parquet
    from cnequity.steps.finalize import (
        _capture_derive_inputs,
        _layer_file_identity,
        _publish_derived_revision,
        _record_dataset_result,
    )
    from cnequity.storage.revisions import RevisionStore

    manifest = Manifest(cfg.manifest_path)
    run_id = manifest.start_run(f"derive:{dataset}", {"dataset": dataset})
    outcome = {"status": "success", "rows_written": 0}
    try:
        with run_lock(cfg.meta_root, run_id), lake_mutation_lock(cfg.meta_root):
            store = RevisionStore(cfg.meta_root, cfg.curated_root, cfg.derived_root)
            store.ensure_current(dataset)
            store.materialize_current(dataset)
            before = _layer_file_identity(cfg.derived_root / dataset)
            inputs = _capture_derive_inputs(cfg, dataset)
            required = {
                "adj_factors": ("daily_bars",),
                "industry_index": ("daily_bars", "industry_members"),
                "futures_continuous": ("futures_bars",),
                "option_greeks": ("option_bars", "futures_bars"),
            }
            missing = [
                name
                for name in required.get(dataset, ())
                if not dataset_has_parquet(store.current_root(name))
            ]
            if missing:
                raise InputUnavailableError(f"{dataset}: missing input {', '.join(missing)}")
            yield outcome
            revision = _publish_derived_revision(
                cfg,
                dataset,
                run_id,
                shanghai_today(),
                before,
                input_revisions=inputs,
                coverage_status="partial"
                if outcome["status"] in {"warning", "degraded"}
                else "unknown",
            )
            if revision:
                if revision["coverage_status"] == "partial":
                    outcome["status"] = "degraded"
                _record_dataset_result(
                    cfg,
                    run_id,
                    dataset,
                    "publish_revision",
                    "success",
                    criticality="research",
                    revision_id=revision["revision_id"],
                    rows_written=outcome["rows_written"],
                    coverage_status=revision["coverage_status"],
                    publication_status=revision["publication_status"],
                )
            _record_dataset_result(
                cfg,
                run_id,
                dataset,
                "derive",
                outcome["status"],
                criticality="research",
                revision_id=revision["revision_id"] if revision else None,
                rows_written=outcome["rows_written"],
            )
            manifest.finish_run(run_id, outcome["status"], rows_written=outcome["rows_written"])
            row = manifest.get_run(run_id)
            outcome.update(
                {
                    key: row[key]
                    for key in (
                        "status",
                        "execution_status",
                        "coverage_status",
                        "publication_status",
                        "result_schema_version",
                    )
                }
            )
            from cnequity.orchestrator.recovery import fallback_options

            outcome.update(run_id=run_id, fallback=fallback_options(cfg, manifest, run_id))
    except Exception as exc:
        axes = step_outcome("failed", error=exc)
        fields = axes.to_dict()
        fields.pop("result_schema_version")
        manifest.record_dataset_result(
            run_id,
            dataset,
            "derive",
            "skipped" if axes.execution_status == "skipped" else "failed",
            criticality="research",
            error_code=type(exc).__name__,
            error_message=str(exc),
            **fields,
        )
        manifest.finish_run(run_id, "failed", error_message=str(exc))
        raise


@cli.command()
@click.argument("name", default="adj_factors")
@config_option
@click.option(
    "--full",
    is_flag=True,
    default=False,
    help=(
        "重写 adj_factors / industry_index / option_greeks / minute_bars_15m|30m|60m 的全部分区"
        "（默认只补增量）。"
    ),
)
@click.option(
    "--start",
    "start_str",
    default=None,
    help=(
        "industry_index / trading_status / option_greeks / minute_bars_15m|30m|60m："
        "只派生这个日期（YYYY-MM-DD）及之后的。"
    ),
)
@click.option(
    "--end",
    "end_str",
    default=None,
    help=(
        "industry_index / trading_status / option_greeks / minute_bars_15m|30m|60m："
        "只派生这个日期（YYYY-MM-DD）及之前的。"
    ),
)
@click.option(
    "--apply",
    "apply_changes",
    is_flag=True,
    help=(
        "bse_code_migration：真正重写分区；adj_factor_source：写入逐证券来源覆盖并重算这些证券的因子"
        "（默认只报告）。"
    ),
)
def derive(
    name: str,
    config_path: str,
    full: bool,
    start_str: str | None,
    end_str: str | None,
    apply_changes: bool,
):
    """派生计算类数据集。

    \b
    `adj_factors` 和 `industry_index` 本来就是日更里的 step（`derive_adj_factors`、
    `derive_industry_index`）；`trading_status` 的停牌派生在 init 和
    `cne backfill daily_bars` 之后自动运行，`cne backfill corporate_actions` 之后也会自动重算受影响证券的因子。
    所以在这里跑它们属于修复或补更早的窗口，不是正常一天的一部分。
    `sector_routing`、`sector_code_map` 和 `valuation_orphans` 没有任何调度会跑，只能手动执行。
    `adj_factor_source` 用 Baostock 仲裁因子与公司行为的矛盾；证明新浪有误且 Baostock
    与其余事件一致的证券，`--apply` 后整条因子改用 Baostock。
    `futures_continuous` 和 `option_greeks` 在 `derivatives` 组里日更。
    `futures_continuous` 每次全量重算；`option_greeks` 自动检测行情、合约、利率和模型依赖变化。
    衍生品回填后自动更新合约及派生；`--full` 可显式全部重算。
    `minute_bars_15m`、`minute_bars_30m`、`minute_bars_60m` 默认不计算，只在这里手动派生：
    某只股票某天有 1m 就用 1m，否则用 5m。不给窗口时只重算还没算过、或分钟线输入已更新的交易日。
    """
    # Derive targets are lower case in the registry, and command names are
    # already case-insensitive; a target typed in caps should resolve the same.
    name = name.lower()
    cfg = _cfg(config_path)
    attach_log_file(cfg, "derive")
    start = parse_date_option(start_str, "--start")
    end = parse_date_option(end_str, "--end")
    if start and end and start > end:
        raise click.ClickException("--start 必须早于或等于 --end")
    if name == "adj_factors":
        with _published_derive(cfg, name) as outcome:
            result = compute_adj_factors(cfg, full=full)
            outcome["rows_written"] = result.rows
            if result.failed:
                outcome["status"] = "degraded"
        click.echo(f"已派生 {name}：{result.rows} 行")
        if result.failed:
            click.echo(
                f"警告：{len(result.failed)} 个 标的×类型 抓取失败（{result.fail_ratio:.1%}）",
                err=True,
            )
            click.echo(json.dumps(outcome, indent=2, default=str, ensure_ascii=False))
    elif name == "adj_factor_source":
        from cnequity.derive.factor_arbitration import (
            arbitrate_factor_sources,
            record_source_overrides,
        )

        report = arbitrate_factor_sources(cfg)
        if apply_changes and report.get("switch"):
            switched = record_source_overrides(cfg, report)
            with _published_derive(cfg, "adj_factors") as outcome:
                result = compute_adj_factors(cfg, refresh_symbols=switched)
                outcome["rows_written"] = result.rows
                if result.failed:
                    outcome["status"] = "degraded"
            report["applied"] = True
            report["rows_rewritten"] = result.rows
        else:
            report["applied"] = False
        report.pop("switch_detail", None)
        click.echo(json.dumps(report, indent=2, default=str, ensure_ascii=False))
    elif name == "industry_index":
        from cnequity.derive.industry_index import derive_industry_index

        with _published_derive(cfg, name) as outcome:
            summary = derive_industry_index(cfg, start=start, end=end, full=full)
            outcome["rows_written"] = summary.get("rows", 0)
        click.echo(json.dumps(summary, indent=2, default=str))
    elif name == "futures_continuous":
        if start or end:
            raise click.ClickException("futures_continuous 必须全量重算，不支持 --start / --end")
        from cnequity.derive.futures_continuous import derive_futures_continuous

        with _published_derive(cfg, name) as outcome:
            summary = derive_futures_continuous(cfg)
            outcome["rows_written"] = summary.get("rows", 0)
        click.echo(json.dumps(summary, indent=2, default=str))
    elif name == "option_greeks":
        from cnequity.derive.option_greeks import derive_option_greeks

        with _published_derive(cfg, name) as outcome:
            summary = derive_option_greeks(cfg, start=start, end=end, full=full)
            outcome["rows_written"] = summary.get("rows", 0)
        click.echo(json.dumps(summary, indent=2, default=str))
    elif name in RESAMPLED_MINUTE_DATASETS:
        from cnequity.derive.minute_resample import derive_minute_resample

        with _published_derive(cfg, name) as outcome:
            _require_minute_input(cfg, name)
            summary = derive_minute_resample(cfg, name, start=start, end=end, full=full)
            outcome["rows_written"] = summary.get("rows", 0)
            # Halts are expected gaps; a gap in a traded day, or input the
            # resampler rejects, is a data problem.
            if any(
                summary.get(key)
                for key in ("skipped_incomplete_1m", "skipped_incomplete_5m", "skipped_invalid")
            ):
                outcome["status"] = "degraded"
        click.echo(json.dumps(summary, indent=2, default=str, ensure_ascii=False))
    elif name == "trading_status":
        summary = _derive_trading_status(cfg, start=start, end=end)
        click.echo(json.dumps(summary, indent=2, default=str))
        exit_code = _run_status_exit_code(str(summary.get("status", "failed")))
        if exit_code:
            raise SystemExit(exit_code)
    elif name == "sector_routing":
        from cnequity.derive.sector_routing import derive_sector_routing

        summary = derive_sector_routing(cfg)
        click.echo(json.dumps(summary, indent=2, default=str))
    elif name == "sector_code_map":
        from cnequity.derive.sector_code_map import derive_sector_code_map

        summary = derive_sector_code_map(cfg)
        click.echo(json.dumps(summary, indent=2, default=str))
    elif name == "valuation_orphans":
        from cnequity.storage.repairs.valuation_orphans import purge_valuation_orphan_symbols

        summary = purge_valuation_orphan_symbols(cfg)
        click.echo(json.dumps(summary, indent=2, default=str))
    elif name == "bse_code_migration":
        from cnequity.storage.repairs.bse_code_migration import migrate_bse_legacy_codes

        summary = migrate_bse_legacy_codes(cfg, apply=apply_changes)
        if not apply_changes:
            summary["note"] = "report only; re-run with --apply to rewrite the partitions"
        click.echo(json.dumps(summary, indent=2, default=str))
    else:
        raise click.ClickException(f"未知的 derive 目标：{name}")


@run.command("clean")
@config_option
@click.option("--dry-run", is_flag=True, help="兼容选项；现在所有清理均只预览。")
@click.option(
    "--orphan-retention-days",
    default=7,
    show_default=True,
    help="预览超过这么多天、且 manifest 里没有记录的孤儿 staging。",
)
@click.option(
    "--snapshot-retention-days",
    default=DEFAULT_SNAPSHOT_RETENTION_DAYS,
    show_default=True,
    help=(
        "预览超过这么多天的 meta/source_snapshots run_id 目录（每个数据集 / 源的最新一份始终保留）"
        "。"
    ),
)
@click.option(
    "--force",
    is_flag=True,
    help="将未完成或未 compact 的 staging 也列入预览，不删除文件、不降级批次。",
)
@click.option(
    "--keep-revision-generations",
    default=5,
    show_default=True,
    type=int,
    help=(
        "每个数据集保留最近这么多代及 current/hold；其他版本只列出候选，不标记、不释放字节。"
        "网页标记前须导入引用清单。0 表示跳过版本处理。"
    ),
)
@click.option(
    "--log-retention-days",
    default=DEFAULT_LOG_RETENTION_DAYS,
    show_default=True,
    type=int,
    help="预览超过这么多天的 `logs/cne-*.log`，不删除；0 表示跳过。",
)
@click.option(
    "--reconcile-runs",
    is_flag=True,
    help="清理前，把卡在 'running'（worker 崩溃）的 run 标成 failed。",
)
@click.option(
    "--reconcile-after-seconds",
    default=None,
    type=float,
    help=("只对静默超过这么多秒的 run 做上面的对账（默认取 [orchestrator].batch_stale_seconds）。"),
)
def clean(
    config_path: str,
    dry_run: bool,
    orphan_retention_days: int,
    snapshot_retention_days: int,
    keep_revision_generations: int,
    log_retention_days: int,
    force: bool,
    reconcile_runs: bool,
    reconcile_after_seconds: float | None,
):
    """预览过期数据，不执行物理删除；删除须在 serve 运维网页确认。

    \b
    「可清理」是指：run 处于终态（success/warning/failed）、所有批次都已落定，
    并且记录过一次成功的 compact。没跑完或从没 compact 过的 staging 会留着等重试，
    --force 只扩大预览范围。来源快照和日志也仅预览，全部 bytes_freed 为 0。
    历史版本和登记试验可在 serve 存储运维页确认删除；其他资源暂仅报告。
    """
    if dry_run and reconcile_runs:
        raise click.UsageError(
            "--dry-run 不能与 --reconcile-runs 同用：对账会修改运行状态。"
            "请先去掉 --reconcile-runs 查看清理预演。"
        )
    # Scheduled invocations must never bypass the browser confirmation flow.
    dry_run = True
    cfg = _cfg(config_path)
    attach_log_file(cfg, "run-clean")
    # Preview the candidate inventory; no cleanup executor receives write authority.
    generations = (
        prune_revision_generations(cfg.meta_root, keep=keep_revision_generations, dry_run=dry_run)
        if keep_revision_generations > 0
        else []
    )
    reconciled: dict[str, int] | None = None
    if reconcile_runs:
        manifest = Manifest(cfg.manifest_path)
        stale_after = (
            float(reconcile_after_seconds)
            if reconcile_after_seconds is not None
            else cfg.batch_stale_seconds
        )
        reconciled = manifest.reconcile_orphaned_runs(
            stale_after_seconds=stale_after,
            locks_root=cfg.meta_root,
        )
    result = clean_staging(
        cfg,
        dry_run=dry_run,
        orphan_retention_days=orphan_retention_days,
        force=force,
    )
    snaps = clean_source_snapshots(
        cfg.meta_root,
        retention_days=snapshot_retention_days,
        dry_run=dry_run,
    )
    logs = clean_run_logs(cfg.data_root, retention_days=log_retention_days, dry_run=dry_run)
    click.echo(
        json.dumps(
            {
                "dry_run": dry_run,
                "confirmation_required": "serve_storage_page",
                "reconciled": reconciled,
                "removed_run_ids": [],
                "candidate_run_ids": result.removed_run_ids,
                "orphan_run_ids": result.orphan_run_ids,
                "force_removed_run_ids": [],
                "force_candidate_run_ids": result.force_removed_run_ids,
                "skipped_run_ids": result.skipped_run_ids,
                "bytes_freed": 0,
                "logical_bytes_selected": (
                    result.bytes_freed
                    + snaps.bytes_freed
                    + logs.bytes_freed
                    + sum(item.logical_bytes_selected for item in generations)
                ),
                "source_snapshots": {
                    "removed_run_dirs": [],
                    "candidate_run_dirs": snaps.removed_run_dirs,
                    "kept_run_dirs": snaps.kept_run_dirs,
                    "bytes_freed": 0,
                    "logical_bytes_selected": snaps.bytes_freed,
                },
                "run_logs": {
                    "removed": 0,
                    "candidates": len(logs.removed),
                    "kept": logs.kept,
                    "bytes_freed": 0,
                    "logical_bytes_selected": logs.bytes_freed,
                },
                "revision_generations": [
                    {
                        "dataset": item.dataset,
                        "removed": len(item.removed_revision_ids),
                        "kept": len(item.kept_revision_ids),
                        "bytes_freed": item.freed_bytes,
                        "candidates": len(item.candidate_revision_ids),
                        "marked": len(item.marked_revision_ids),
                        "logical_bytes_selected": item.logical_bytes_selected,
                    }
                    for item in generations
                ],
            },
            indent=2,
        )
    )


@cli.group()
def stats():
    """meta/stats 下的湖度量表（行数、字节数、来源构成）。"""


def _stats_rebuild_if_stale(cfg, *, as_json: bool) -> None:
    """The former `cne stats refresh`: rebuild only when the lake has moved on."""
    from cnequity.storage.stats import refresh_stats_if_stale, stats_freshness

    freshness = stats_freshness(cfg)
    result = refresh_stats_if_stale(cfg)
    if result is None:
        reason = (
            "stale, but another rebuild holds the lock — nothing to do"
            if freshness.stale
            else f"current as of run {freshness.latest_run_id} — nothing to do"
        )
        click.echo(json.dumps({"rebuilt": False, "reason": reason}) if as_json else reason)
        return
    if as_json:
        click.echo(json.dumps({"rebuilt": True, **result.as_dict()}, indent=2, default=str))
        return
    click.echo(
        f"已重算（{freshness.reason or 'stale'}）："
        f"{len(result.datasets)} 个数据集、{result.rows:,} 行，"
        f"耗时 {result.elapsed_seconds:.1f}s"
    )


@stats.command("rebuild")
@config_option
@click.option(
    "--dataset",
    "dataset_names",
    multiple=True,
    help="只重算这些数据集（可重复）。其它数据集的行保持不变。",
)
@click.option(
    "--if-stale",
    is_flag=True,
    help="除非统计建好之后又跑过采集，否则什么都不做。可以安全地挂定时器。",
)
@click.option("--json", "as_json", is_flag=True, help="以 JSON 打印结果。")
def stats_rebuild(config_path: str, dataset_names: tuple[str, ...], if_stale: bool, as_json: bool):
    """从 curated 和 derived 重算 partition_stats / provenance_stats。

    \b
    默认无条件重算。要挂定时器请用 `--if-stale`：没有任何变化时它直接返回，
    并且在已有并发重算持锁时选择让路而不是排队 ——
    一个卡在全量扫描后面的面板请求，比落后一次 run 的数字更糟。

    \b
    是否过期按 run id 判断，不看时钟。只有采集会改变这个湖，
    所以在最后一次 run 之后建的统计就是新的，无论看上去多旧。
    """
    # Registry names are lower case; `--dataset` should not care about case.
    dataset_names = tuple(n.lower() for n in dataset_names)
    from cnequity.storage.stats import rebuild_stats

    cfg = _cfg(config_path)
    attach_log_file(cfg, "stats-rebuild")

    if if_stale:
        if dataset_names:
            raise click.UsageError("--if-stale 会重算整个湖；请去掉 --dataset，或者去掉 --if-stale")
        _stats_rebuild_if_stale(cfg, as_json=as_json)
        return

    try:
        result = rebuild_stats(cfg, datasets=list(dataset_names) or None)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    if as_json:
        click.echo(json.dumps(result.as_dict(), indent=2, default=str))
        return
    click.echo(
        f"{len(result.datasets)} 个数据集、{result.partitions} 个分区、"
        f"{result.rows:,} 行、{result.files} 个文件、"
        f"{result.bytes / 1e6:.1f}MB，耗时 {result.elapsed_seconds:.1f}s"
    )
    if result.empty:
        click.echo(f"还没有 parquet：{', '.join(sorted(result.empty))}")


def _scan_curated_datasets(cfg) -> list[dict]:
    """Count curated Parquet on the spot — the former `cne catalog`.

    Every call walks the whole tree, which is why `cne stats rebuild` exists.
    It stays as the answer for a lake that has never built its stats tables:
    "what is in here" should not require a build step first.
    """
    entries = []
    curated = cfg.curated_root
    if not curated.exists():
        return entries
    for ds_dir in sorted(curated.iterdir()):
        if not ds_dir.is_dir():
            continue
        files = list(ds_dir.glob("**/*.parquet"))
        # lazy count(*) resolves from parquet metadata without decoding data
        # pages — cheap even on a 10-year lake.
        rows = int(scan_parquet_files(files).select(pl.len()).collect().item()) if files else 0
        entries.append({"dataset": ds_dir.name, "files": len(files), "rows": rows})
    return entries


@stats.command("show")
@config_option
@click.option("--dataset", default=None, help="某一个数据集的逐分区明细。")
@click.option("--by-source", is_flag=True, help="改为按 source / data_version 分组。")
@click.option("--json", "as_json", is_flag=True, help="输出机器可读的 JSON。")
def stats_show(config_path: str, dataset: str | None, by_source: bool, as_json: bool):
    """汇总统计表；统计表过期时先自动重算。

    \b
    统计表落后于最新一次采集时，先按 `cne stats rebuild --if-stale` 的规则重算再显示；
    `--dataset` / `--by-source` 需要的统计表还没生成时，也会先生成。
    其余情况下，从没建过统计表的湖退回到现场数 curated 的 Parquet：更薄 ——
    没有字节总量、没有来源构成、没有逐分区明细 —— 但不用先建任何东西就能回答
    「这个湖里有什么」。这个退路就是从前的 `cne catalog`，`--json` 是它的输出。
    """
    dataset = dataset.lower() if dataset else None
    from cnequity.storage.stats import (
        load_partition_stats,
        load_provenance_stats,
        load_summary,
        refresh_stats_if_stale,
        stats_freshness,
    )

    cfg = _cfg(config_path)
    summary = load_summary(cfg)
    # Refresh instead of telling the reader to: a stale table, or a missing one
    # a detail view needs. A lake with no tables and a plain view keeps the
    # cheap scan below, whose --json shape scripts already read.
    if (summary is not None and stats_freshness(cfg).stale) or (
        summary is None and (dataset or by_source)
    ):
        click.echo(
            f"统计表{'过期' if summary is not None else '尚未生成'}，正在重算（耗时取决于湖的大小）…",
            err=True,
        )
        refresh_stats_if_stale(cfg)
        summary = load_summary(cfg)
    if summary is None:
        if dataset or by_source:
            raise click.ClickException(
                "另一个统计重算正在进行 —— `--dataset` / `--by-source` 需要的统计表还没生成，稍后再试"
            )
        entries = _scan_curated_datasets(cfg)
        if as_json:
            click.echo(json.dumps(entries, indent=2))
            return
        click.echo("没有统计表 —— 已直接扫 curated；加 --dataset 或 --by-source 会自动生成统计表")
        click.echo(
            pl.DataFrame(
                entries, schema={"dataset": pl.String, "files": pl.Int64, "rows": pl.Int64}
            )
        )
        return
    freshness = stats_freshness(cfg)

    df = load_provenance_stats(cfg) if by_source else load_partition_stats(cfg)
    if dataset:
        df = df.filter(pl.col("dataset") == dataset)
        if df.is_empty():
            raise click.ClickException(f"数据集 {dataset!r} 没有统计行")
    elif by_source:
        df = df.group_by(["dataset", "source", "data_version"]).agg(
            pl.col("row_count").sum(),
            pl.col("fetched_at_min").min(),
            pl.col("fetched_at_max").max(),
        )
    else:
        df = df.group_by("dataset").agg(
            pl.len().alias("partitions"),
            pl.col("row_count").sum(),
            pl.col("file_count").sum().alias("files"),
            pl.col("bytes").sum(),
            pl.col("period_start").min(),
            pl.col("period_end").max(),
        )

    # Still stale only when another rebuild held the lock just now.
    stale_note = (
        f"  STALE — {freshness.reason}; another rebuild is in progress" if freshness.stale else ""
    )
    click.echo(
        f"生成于：{summary.get('generated_at')}  run：{summary.get('latest_run_id')}{stale_note}"
    )
    if as_json:
        click.echo(json.dumps(df.sort(df.columns[:2]).to_dicts(), indent=2, default=str))
        return
    with pl.Config(tbl_rows=-1, tbl_cols=-1, fmt_str_lengths=32):
        click.echo(df.sort(df.columns[:2]))
