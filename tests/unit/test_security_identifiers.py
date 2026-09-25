from datetime import date

import polars as pl

from cnequity.domain.security_identifiers import (
    canonical_symbol,
    canonicalize_dated_identifiers,
    identifier_aliases,
    identifier_changes,
)


def test_601313_chain_has_primary_source_evidence():
    event = identifier_changes()[0]
    assert event.predecessor == "601313.SH"
    assert event.successor == "601360.SH"
    assert event.effective_date == date(2018, 2, 28)
    assert event.share_ratio == 1.0
    assert event.announcement_id == "1204419027"
    assert len(event.document_sha256) == 64


def test_canonical_symbol_and_aliases_follow_effective_date():
    assert identifier_aliases("601313.SH") == {"601313.SH", "601360.SH"}
    assert canonical_symbol("601360.SH", date(2018, 2, 27)) == "601313.SH"
    assert canonical_symbol("601313.SH", date(2018, 2, 28)) == "601360.SH"


def test_000043_chain_has_primary_source_evidence_and_dated_handoff():
    event = next(item for item in identifier_changes() if item.predecessor == "000043.SZ")
    assert event.successor == "001914.SZ"
    assert event.effective_date == date(2019, 12, 16)
    assert event.share_ratio == 1.0
    assert event.announcement_id == "1207164397"
    assert event.source_published_at.isoformat() == "2019-12-15T16:00:00+00:00"
    assert len(event.document_sha256) == 64
    assert identifier_aliases("001914.SZ") == {"000043.SZ", "001914.SZ"}
    assert canonical_symbol("001914.SZ", date(2019, 12, 13)) == "000043.SZ"
    assert canonical_symbol("000043.SZ", date(2019, 12, 16)) == "001914.SZ"


def test_000043_native_row_wins_over_successor_restatement():
    frame = pl.DataFrame(
        {
            "symbol": ["000043.SZ", "001914.SZ", "000043.SZ"],
            "trade_date": [date(2018, 6, 1), date(2018, 6, 1), date(2019, 12, 16)],
            "source": ["native", "restated", "stale_old"],
        }
    )
    assert canonicalize_dated_identifiers(frame, date_col="trade_date").sort(
        "trade_date"
    ).to_dicts() == [
        {"symbol": "000043.SZ", "trade_date": date(2018, 6, 1), "source": "native"},
        {"symbol": "001914.SZ", "trade_date": date(2019, 12, 16), "source": "stale_old"},
    ]


def test_000022_chain_has_issuer_dated_one_for_one_handoff():
    event = next(item for item in identifier_changes() if item.predecessor == "000022.SZ")
    assert event.successor == "001872.SZ"
    assert event.effective_date == date(2018, 12, 26)
    assert event.share_ratio == 1.0
    assert event.announcement_id == "1205690369"
    assert event.source_published_at.isoformat() == "2018-12-25T16:00:00+00:00"
    assert len(event.document_sha256) == 64
    assert canonical_symbol("001872.SZ", date(2018, 12, 20)) == "000022.SZ"
    assert canonical_symbol("000022.SZ", date(2018, 12, 26)) == "001872.SZ"


def test_native_historical_row_wins_over_vendor_restatement():
    frame = pl.DataFrame(
        {
            "symbol": ["601313.SH", "601360.SH", "601313.SH"],
            "trade_date": [date(2018, 2, 14), date(2018, 2, 14), date(2018, 2, 28)],
            "close": [10.0, 10.0, 11.0],
            "volume": [100, 101, 200],
            "source": ["native", "restated", "stale_old"],
        }
    )
    out = canonicalize_dated_identifiers(frame, date_col="trade_date")
    assert out.sort("trade_date").to_dicts() == [
        {
            "symbol": "601313.SH",
            "trade_date": date(2018, 2, 14),
            "close": 10.0,
            "volume": 100,
            "source": "native",
        },
        {
            "symbol": "601360.SH",
            "trade_date": date(2018, 2, 28),
            "close": 11.0,
            "volume": 200,
            "source": "stale_old",
        },
    ]


def test_restated_successor_is_relabelled_when_native_row_is_absent():
    frame = pl.DataFrame(
        {
            "symbol": ["601360.SH"],
            "trade_date": [date(2017, 1, 3)],
            "status": ["normal"],
        }
    )
    out = canonicalize_dated_identifiers(frame, date_col="trade_date")
    assert out["symbol"].to_list() == ["601313.SH"]


def test_historical_vendor_label_collapses_to_native_issuer_only_in_evidenced_window():
    frame = pl.DataFrame(
        {
            "symbol": ["300114.SZ", "302132.SZ", "302132.SZ"],
            "trade_date": [date(2021, 5, 20), date(2021, 5, 20), date(2025, 1, 2)],
            "close": [12.0, 12.0, 20.0],
            "source": ["native", "restated", "later_unknown"],
        }
    )
    out = canonicalize_dated_identifiers(frame, date_col="trade_date")
    assert out.sort("trade_date").to_dicts() == [
        {
            "symbol": "300114.SZ",
            "trade_date": date(2021, 5, 20),
            "close": 12.0,
            "source": "native",
        },
        {
            "symbol": "302132.SZ",
            "trade_date": date(2025, 1, 2),
            "close": 20.0,
            "source": "later_unknown",
        },
    ]
