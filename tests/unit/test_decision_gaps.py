from __future__ import annotations

import json
from datetime import date

import polars as pl
import pytest
from click.testing import CliRunner

from cnequity.cli.main import cli
from cnequity.quality import decision_gaps


def test_protected_window_rejected_before_lake_read(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("protected data was read")

    monkeypatch.setattr(decision_gaps, "load", forbidden)
    monkeypatch.setattr(decision_gaps, "committed_revision", forbidden)
    with pytest.raises(ValueError, match="2016-01-01..2024-12-31"):
        decision_gaps.inventory(data_root=tmp_path, start=date(2024, 1, 1), end=date(2025, 1, 1))


def test_inventory_is_pinned_deterministic_and_preserves_unknown(monkeypatch, tmp_path):
    rows = pl.DataFrame(
        {
            "symbol": ["600025.SH", "519001.SH", "920001.BJ", "600519.SH"],
            "ex_date": [date(2020, 1, 2)] * 4,
            "action_type": ["cash_dividend"] * 4,
            "cash_dividend": [0.1, 0.2, 0.3, 0.4],
            "payment_date": [None, None, None, date(2020, 1, 3)],
            "source": ["vendor"] * 4,
            "data_version": ["v1"] * 4,
        }
    )

    def fake_load(dataset, **kwargs):
        assert dataset == "corporate_actions"
        assert kwargs["revision"] == "revision-id"
        assert kwargs["end"] == date(2024, 12, 31)
        return rows

    monkeypatch.setattr(decision_gaps, "load", fake_load)
    kwargs = {
        "data_root": tmp_path,
        "start": date(2016, 1, 1),
        "end": date(2024, 12, 31),
        "revision": (214, "revision-id"),
    }
    first = decision_gaps.inventory(**kwargs)
    second = decision_gaps.inventory(**kwargs)
    assert first == second
    assert first["count"] == 3
    assert first["source_route_counts"] == {
        "identity_unresolved": 1,
        "sh_sz_equity_candidate": 1,
        "bse_equity_candidate": 1,
    }
    assert all(event["evidence_status"] == "unreviewed" for event in first["events"])
    saved = decision_gaps.save_immutable(first, tmp_path)
    assert decision_gaps.save_immutable(second, tmp_path) == saved
    assert json.loads(saved.read_text()) == first
    saved.write_text("corrupt")
    with pytest.raises(ValueError, match="different content"):
        decision_gaps.save_immutable(first, tmp_path)


def test_cli_rejects_protected_window_before_config_access():
    result = CliRunner().invoke(
        cli, ["decision-data", "payment-gaps", "--end", "2025-01-01", "--config", "missing.toml"]
    )
    assert result.exit_code != 0
    assert "2016-01-01..2024-12-31" in result.output
    assert "missing.toml" not in result.output
