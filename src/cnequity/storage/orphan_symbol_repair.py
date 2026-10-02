"""Drop factor and action rows for securities the lake holds no prices for.

Open-end fund codes (519xxx) once reached ``daily_bars`` as NAV series. The
bars were purged, but their ``adj_factors`` and ``corporate_actions`` rows
stayed: a factor with no price to adjust, and dividends no factor can step
on. Every such row reads as a factor/action contradiction and adjusts
nothing. Listed funds whose prices the lake never collected land here too.

The repair reads the committed generations, rewrites only partitions that
hold such rows, and publishes one revision per dataset through the repair
gate. Previous revisions stay readable.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from cnequity.config import Config
from cnequity.domain.datasets import DATASETS
from cnequity.file_lock import lake_mutation_lock
from cnequity.query.parquet_scan import scan_parquet_root
from cnequity.storage.parquet import CuratedWriter
from cnequity.storage.revisions import RevisionStore

logger = logging.getLogger(__name__)

_DATASETS = {"adj_factors": "trade_date", "corporate_actions": "ex_date"}


def _symbols(root: Path, partition_col: str) -> set[str]:
    if not root.is_dir():
        return set()
    return set(
        scan_parquet_root(root, partition_col=partition_col)
        .select("symbol")
        .unique()
        .collect()
        .get_column("symbol")
        .to_list()
    )


def repair_orphan_symbols(config: Config, *, apply: bool = False) -> dict:
    """Plan (default) or publish removal of rows for securities with no bars."""
    with lake_mutation_lock(config.meta_root, blocking=True):
        return _repair_locked(config, apply=apply)


def _repair_locked(config: Config, *, apply: bool) -> dict:
    store = RevisionStore(config.meta_root, config.curated_root, config.derived_root)
    bars = store.current_root("daily_bars") or config.curated_root / "daily_bars"
    priced = _symbols(bars, "trade_date")
    report: dict = {"applied": False, "datasets": {}}
    if not priced:
        report["note"] = "no daily_bars; nothing can be reconciled"
        return report
    for dataset, partition_col in _DATASETS.items():
        store.ensure_current(dataset)
        base = store.current_root(dataset)
        if base is None:
            continue
        orphans = sorted(_symbols(base, partition_col) - priced)
        entry: dict = {"orphan_symbols": len(orphans), "sample": orphans[:20], "rows": 0}
        report["datasets"][dataset] = entry
        if not orphans:
            continue
        hit = (
            scan_parquet_root(base, partition_col=partition_col)
            .filter(pl.col("symbol").is_in(orphans))
            .group_by(partition_col)
            .len()
            .collect()
        )
        entry.update(rows=int(hit.get_column("len").sum()), partitions=hit.height)
        if apply:
            entry.update(_publish(config, store, dataset, partition_col, base, set(orphans)))
    report["applied"] = apply
    return report


def _publish(
    config: Config,
    store: RevisionStore,
    dataset: str,
    partition_col: str,
    base: Path,
    orphans: set[str],
) -> dict:
    from cnequity.domain.contracts import contract_fingerprint, dataset_contract
    from cnequity.quality.publication import check_repair_publication

    store.materialize_current(dataset)
    layer = config.derived_root if DATASETS[dataset].layer == "derived" else config.curated_root
    writer = CuratedWriter(layer)
    changed: list[Path] = []
    emptied = 0
    for directory in sorted(p for p in base.iterdir() if p.is_dir()):
        files = sorted(directory.rglob("*.parquet"))
        if not files:
            continue
        frame = pl.concat([pl.read_parquet(p) for p in files], how="diagonal_relaxed")
        keep = frame.filter(~pl.col("symbol").is_in(list(orphans)))
        if keep.height == frame.height:
            continue
        value = directory.name.partition("=")[2]
        if keep.is_empty():
            # The commit carries an undeclared partition only while it is
            # still present, so removing it here removes it from the revision.
            target = layer / dataset / directory.name
            for path in sorted(target.rglob("*"), reverse=True):
                path.unlink() if path.is_file() else path.rmdir()
            target.rmdir()
            emptied += 1
            continue
        changed.append(
            writer.write_partition(dataset, partition_col, value, keep, "part-merged.parquet")
        )
    if not changed and emptied:
        # commit() reads "no changed files" as "unchanged", so a change made
        # only of removed partitions would never publish. Re-declare one
        # untouched partition, content as committed, to carry the removals.
        survivor = next(
            (
                p
                for p in sorted((layer / dataset).iterdir())
                if p.is_dir() and any(p.rglob("*.parquet"))
            ),
            None,
        )
        if survivor is not None:
            frame = pl.concat(
                [pl.read_parquet(p) for p in sorted(survivor.rglob("*.parquet"))],
                how="diagonal_relaxed",
            )
            changed.append(
                writer.write_partition(
                    dataset,
                    partition_col,
                    survivor.name.partition("=")[2],
                    frame,
                    "part-merged.parquet",
                )
            )
    run_id = f"orphan-symbols-{dataset}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    publication = check_repair_publication(config, dataset, run_id, changed)
    contract = dataset_contract(dataset)
    revision = store.commit(
        dataset,
        run_id=run_id,
        changed_files=changed,
        schema_version=int(contract["schema_version"]),
        contract_fingerprint=contract_fingerprint(contract),
        metadata={
            "reason": "orphan_symbol_repair",
            "orphan_symbols": len(orphans),
            "partitions_emptied": emptied,
            "publication_audit": publication["report_path"],
        },
    )
    if revision is None:
        raise RuntimeError(f"{dataset}: orphan removal produced no revision; nothing published")
    logger.info("%s: removed rows for %d security(ies) without bars", dataset, len(orphans))
    return {
        "revision": revision.revision,
        "partitions_rewritten": len(changed),
        "partitions_emptied": emptied,
        "publication_audit": publication["report_path"],
    }
