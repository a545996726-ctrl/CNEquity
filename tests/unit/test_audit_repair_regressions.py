from datetime import date

import polars as pl
import pytest

from cnequity.config import Config
from cnequity.quality.dataset_checks import _partitioned_pk_duplicate_count


@pytest.mark.parametrize("layout", ["root_overlap", "misplaced", "non_key_partition"])
def test_pk_audit_checks_cross_partition_duplicates(tmp_path, layout):
    dataset = "daily_bars"
    pcol = "trade_date"
    frame = pl.DataFrame({"symbol": ["600000.SH"], "trade_date": [date(2026, 1, 1)]})
    if layout == "non_key_partition":
        dataset = "announcement_index"
        pcol = "announce_date"
        frame = pl.DataFrame({"announcement_id": ["same"], "announce_date": [date(2026, 1, 1)]})
    first = tmp_path / f"{pcol}=2026-01-01" / "part.parquet"
    second = tmp_path / (
        "legacy.parquet" if layout == "root_overlap" else f"{pcol}=2026-01-02/part.parquet"
    )
    for path in (first, second):
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.write_parquet(path)
    assert _partitioned_pk_duplicate_count([first, second], dataset, pcol, tmp_path) == 1


def test_offline_audit_never_calls_live_close_check(tmp_path, monkeypatch):
    import cnequity.quality.audit as audit

    def forbidden(*args, **kwargs):
        pytest.fail("offline audit attempted a live close crosscheck")

    monkeypatch.setattr(audit, "daily_bars_close_crosscheck_findings", forbidden)
    audit._collect_lake_findings(Config(data_root=tmp_path), date(2026, 1, 1), offline=True)
