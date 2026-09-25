"""Research-data evidence inventory commands."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import click

from cnequity.cli._root import cli
from cnequity.cli._shared import _cfg, config_option
from cnequity.quality.cash_rights import reviewed_rights_inventory, save_reviewed_rights
from cnequity.quality.decision_gaps import check_research_window, inventory, save_immutable


@cli.group("decision-data")
def decision_data() -> None:
    """Inspect historical decision-data evidence and unresolved gaps."""


@decision_data.command("payment-gaps")
@click.option("--start", type=click.DateTime(formats=["%Y-%m-%d"]), default="2016-01-01")
@click.option("--end", type=click.DateTime(formats=["%Y-%m-%d"]), default="2024-12-31")
@click.option("--output-dir", type=click.Path(path_type=Path), default=None)
@config_option
def payment_gaps(start, end, output_dir: Path | None, config_path: str) -> None:
    """Freeze missing cash-payment events for the 2016–2024 research scope."""
    start_day: date = start.date()
    end_day: date = end.date()
    try:
        check_research_window(start_day, end_day)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--start/--end") from exc
    cfg = _cfg(config_path)
    result = inventory(data_root=cfg.data_root, start=start_day, end=end_day)
    target = save_immutable(result, output_dir or cfg.meta_root / "decision_data_gaps")
    click.echo(
        json.dumps(
            {
                "path": str(target),
                "count": result["count"],
                "source_route_counts": result["source_route_counts"],
                "revision_id": result["revision_id"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


@decision_data.command("cash-rights")
@click.option("--start", type=click.DateTime(formats=["%Y-%m-%d"]), default="2016-01-01")
@click.option("--end", type=click.DateTime(formats=["%Y-%m-%d"]), default="2024-12-31")
@click.option("--output-dir", type=click.Path(path_type=Path), default=None)
@config_option
def cash_rights(start, end, output_dir: Path | None, config_path: str) -> None:
    """Export reviewed holder-specific rights tied to the current action revision."""
    start_day: date = start.date()
    end_day: date = end.date()
    try:
        check_research_window(start_day, end_day)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--start/--end") from exc
    cfg = _cfg(config_path)
    result = reviewed_rights_inventory(data_root=cfg.data_root, start=start_day, end=end_day)
    target = save_reviewed_rights(result, output_dir or cfg.meta_root / "decision_cash_rights")
    click.echo(
        json.dumps(
            {
                "path": str(target),
                "count": result["count"],
                "event_count": result["event_count"],
                "revision_id": result["corporate_actions_revision_id"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
