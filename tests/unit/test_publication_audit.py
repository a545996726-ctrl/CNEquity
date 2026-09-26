from datetime import date
from pathlib import Path

import polars as pl
import pytest

from cnequity.config import Config
from cnequity.quality.publication import evaluate_publication
from cnequity.query.reader import load
from cnequity.steps.finalize import step_compact
from cnequity.storage.parquet import StagingWriter
from cnequity.storage.revisions import RevisionStore


def _bar(close: float, stamp: str) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": ["600000.SH"],
            "trade_date": [date(2024, 6, 28)],
            "open": [close],
            "high": [close],
            "low": [close],
            "close": [close],
            "volume": [1000],
            "amount": [close * 1000],
            "source": ["tdx_protocol"],
            "data_version": ["v2"],
            "fetched_at": [stamp],
        }
    )


@pytest.mark.parametrize("mode,expected", [("block", 10.0), ("shadow", 20.0)])
def test_candidate_audit_precedes_pointer_switch(tmp_path, monkeypatch, mode, expected):
    cfg = Config(data_root=tmp_path, publication_gate=mode)
    path = cfg.curated_root / "daily_bars/trade_date=2024-06-28/part.parquet"
    path.parent.mkdir(parents=True)
    _bar(10.0, "2024-06-28T00:00:00Z").write_parquet(path)
    revisions = RevisionStore(cfg.meta_root, cfg.curated_root)
    revisions.commit(
        "daily_bars",
        run_id="initial",
        changed_files=[path],
        schema_version=2,
        contract_fingerprint="test",
    )
    old_id = revisions.latest("daily_bars").revision_id
    calls = []

    def errors(view, day):
        # Both audits happen before the original lake has been published.
        assert revisions.latest("daily_bars").revision_id == old_id
        close = load("daily_bars", config=view)["close"][0]
        calls.append(close)
        common = [{"dataset": "legacy", "severity": "error", "check": "known_gap"}]
        return common + (
            [{"dataset": "daily_bars", "severity": "error", "check": "bad_correction"}]
            if close == 20
            else []
        )

    monkeypatch.setattr("cnequity.quality.publication._errors", errors)
    StagingWriter(cfg.staging_root).write_batch(
        "daily_bars", "candidate", "one", _bar(20.0, "2024-06-28T01:00:00Z")
    )
    result = step_compact(cfg, date(2024, 6, 28), "candidate", {})
    assert calls == [10.0, 20.0]
    assert load("daily_bars", config=cfg)["close"].to_list() == [expected]
    assert Path(result["publication_audit"]).exists()
    if mode == "block":
        assert revisions.latest("daily_bars").revision_id == old_id
        assert (
            result["context_updates"]["compact_skipped_datasets"][0]["reason"] == "publication_gate"
        )
        assert pl.read_parquet(path)["close"].to_list() == [10.0]


def test_full_audit_detects_corrupt_candidate_offline(tmp_path):
    cfg = Config(data_root=tmp_path / "lake", publication_gate="block", lake_profile="sample")
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "part.parquet").write_bytes(b"not parquet")
    report = evaluate_publication(cfg, "corrupt", date(2024, 6, 28), {"daily_bars": candidate})
    assert report["blocked"]
    assert any(item["check"] == "schema_contract" for item in report["new_errors"])


def test_audit_failure_cannot_publish_in_block_mode(tmp_path, monkeypatch):
    cfg = Config(data_root=tmp_path, publication_gate="block")

    def fail(*args):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr("cnequity.quality.publication._errors", fail)
    report = evaluate_publication(
        cfg, "failure", date(2024, 6, 28), {"daily_bars": tmp_path / "missing"}
    )
    assert report["blocked"]
    assert report["new_errors"][0]["check"] == "candidate_audit_failed"
