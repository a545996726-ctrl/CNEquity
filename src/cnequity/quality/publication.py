"""Offline candidate-lake audit before any compact pointer is published."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
from collections import Counter
from contextlib import closing
from dataclasses import replace
from datetime import date
from pathlib import Path

from cnequity.config import Config
from cnequity.domain.datasets import DATASETS
from cnequity.storage.atomic import write_json_atomic
from cnequity.storage.read_context import read_root


def _link_read_only(source: str, target: str) -> str:
    # The audit never writes Parquet. Metadata is copied separately; no link
    # escapes the temporary audit view and no linked file is ever published.
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)
    return target


def _errors(config: Config, day: date) -> list[dict]:
    from cnequity.quality.audit import _collect_lake_findings, _profile_adjusted_findings
    from cnequity.quality.source_diff import run_source_diffs

    findings = _profile_adjusted_findings(
        config, _collect_lake_findings(config, day, full=True, offline=True)
    )
    findings.extend(run_source_diffs(config, "publication-view", day))
    return [item for item in findings if item.get("severity") == "error"]


def _identity(finding: dict) -> str:
    return json.dumps(finding, sort_keys=True, default=str)


def evaluate_publication(
    config: Config, run_id: str, day: date, candidates: dict[str, Path]
) -> dict:
    mode = config.publication_gate
    if mode == "off" or not candidates:
        return {"mode": mode, "blocked": False, "new_errors": []}
    report = {
        "mode": mode,
        "datasets": sorted(candidates),
        "run_id": run_id,
        "trade_date": str(day),
    }
    try:
        with tempfile.TemporaryDirectory(prefix="cnequity-publication-") as directory:
            root = Path(directory)
            view = replace(config, data_root=root)
            for name, spec in DATASETS.items():
                source = read_root(config, name)
                if source.is_dir():
                    target = (
                        view.derived_root if spec.layer == "derived" else view.curated_root
                    ) / name
                    shutil.copytree(source, target, copy_function=_link_read_only)
            # Isolate every metadata write performed by audit helpers. No
            # pointers are copied: they would resolve back to published data.
            for relative in (
                "state",
                "quality/coverage",
                "quality/evidence",
                "source_snapshots",
                "research",
                "seeds",
            ):
                source = config.meta_root / relative
                if source.is_dir():
                    shutil.copytree(source, view.meta_root / relative)
            view.meta_root.mkdir(parents=True, exist_ok=True)
            if config.manifest_path.exists():
                # SQLite's context manager ends transactions but does not close
                # connections. Windows needs both handles closed before cleanup.
                with (
                    closing(
                        sqlite3.connect(f"{config.manifest_path.as_uri()}?mode=ro", uri=True)
                    ) as original,
                    closing(sqlite3.connect(view.manifest_path)) as copied,
                ):
                    original.backup(copied)
            baseline = _errors(view, day)
            for name, source in candidates.items():
                layer = (
                    view.derived_root if DATASETS[name].layer == "derived" else view.curated_root
                )
                target = layer / name
                if target.exists():
                    shutil.rmtree(target)
                shutil.copytree(source, target, copy_function=_link_read_only)
            candidate_errors = _errors(view, day)
            existing = Counter(_identity(item) for item in baseline)
            introduced = []
            for item in candidate_errors:
                identity = _identity(item)
                if existing[identity]:
                    existing[identity] -= 1
                else:
                    introduced.append(item)
            report.update(
                baseline_errors=baseline,
                candidate_errors=candidate_errors,
                new_errors=introduced,
            )
            blocked_datasets: set[str] = set()
            if mode == "block" and introduced:
                # Attribute a new finding by removing each candidate in turn.
                # This also catches cross-dataset checks: if either side's
                # removal resolves the finding, both sides are held together.
                full_counts = Counter(_identity(item) for item in candidate_errors)
                for name, source in candidates.items():
                    layer = (
                        view.derived_root
                        if DATASETS[name].layer == "derived"
                        else view.curated_root
                    )
                    target = layer / name
                    shutil.rmtree(target)
                    original = read_root(config, name)
                    if original.is_dir():
                        shutil.copytree(original, target, copy_function=_link_read_only)
                    try:
                        without = Counter(_identity(item) for item in _errors(view, day))
                    finally:
                        if target.exists():
                            shutil.rmtree(target)
                        shutil.copytree(source, target, copy_function=_link_read_only)
                    if any(
                        without[_identity(item)] < full_counts[_identity(item)]
                        for item in introduced
                    ):
                        blocked_datasets.add(name)
                if not blocked_datasets:
                    # A check with no attributable input is unsafe to publish.
                    blocked_datasets.update(candidates)
            report["blocked_datasets"] = sorted(blocked_datasets)
    except Exception as exc:
        # In block mode an audit that could not inspect the candidate is not a
        # successful publication check. Preserve the reason for a safe retry.
        report["new_errors"] = [
            {
                "dataset": "publication",
                "check": "candidate_audit_failed",
                "severity": "error",
                "message": str(exc),
            }
        ]
        report["blocked_datasets"] = sorted(candidates) if mode == "block" else []
    report["blocked"] = bool(report.get("blocked_datasets"))
    path = config.meta_root / "quality" / "publication" / f"{run_id}.json"
    write_json_atomic(path, report, indent=2, default=str)
    report["report_path"] = str(path)
    return report
