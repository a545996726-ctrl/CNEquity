"""The official monthly file is evidence, not a complete daily fee schedule."""

import json
from datetime import date

import pytest

from cnequity.adapters.futures_exchange import shfe_parameters
from cnequity.adapters.futures_exchange.common import FuturesPayloadError


class _Sheet:
    def __init__(self, rows):
        self.rows = [row + [""] * (17 - len(row)) for row in rows]
        self.nrows = len(rows)
        self.ncols = 17

    def row_values(self, index):
        return self.rows[index]


class _Workbook:
    nsheets = 1

    def __init__(self, rows):
        self.sheet = _Sheet(rows)

    def sheet_by_index(self, index):
        assert index == 0
        return self.sheet


@pytest.fixture
def parameter_rows(monkeypatch):
    rows = [
        ["铸造铝合金合约"],
        ["调整日期\n合约", "12月31日(周三)", "", "", "", "1月12日(周一)"],
        ["", "交易保证金", "", "交易手续费", "", "交易保证金"],
        ["", "一般%", "套保%", "一般‰", "套保‰", "一般%", "套保%", "一般‰", "套保‰"],
        ["ad2601", "15", "15", "0.05", "0.025", "20", "20", "0.05", "0.025"],
        ["备注", "1月13日调整"],
        ["铝合约"],
        ["调整日期\n合约", "1月5日(周一)"],
        ["", "交易保证金", "", "交易手续费"],
        ["", "一般%", "套保%", "一般（元/手）", "套保（元/手）"],
        ["al2601", "15", "15", "3", "1.5"],
        ["备注", "另有节假日公告"],
    ]
    workbook = _Workbook(rows)
    monkeypatch.setattr(shfe_parameters.xlrd, "open_workbook", lambda **_: workbook)
    return workbook


def _parse():
    return shfe_parameters.parse_monthly_settlement_parameters(
        b"official-xls",
        report_month=date(2026, 1, 1),
        published_on=date(2025, 12, 30),
        source_url="https://www.shfe.com.cn/reports/businessdata/example.xls",
    )


def test_monthly_observations_keep_dates_and_fee_units_separate(parameter_rows):
    records = _parse()
    assert [(row["contract_code"], row["settlement_date"], row["fee_unit"]) for row in records] == [
        ("AD2601", date(2025, 12, 31), "permille"),
        ("AD2601", date(2026, 1, 12), "permille"),
        ("AL2601", date(2026, 1, 5), "cny_per_contract"),
    ]
    assert records[0]["published_on"] == date(2025, 12, 30)
    assert records[0]["fee_general_value"] == 0.05
    assert records[2]["fee_general_value"] == 3.0
    assert all(len(row["raw_sha256"]) == 64 for row in records)


def test_incomplete_or_changed_fee_cells_fail_closed(parameter_rows):
    parameter_rows.sheet.rows[4][3] = ""
    with pytest.raises(FuturesPayloadError, match="partial values"):
        _parse()
    parameter_rows.sheet.rows[4][3] = "0.05"
    parameter_rows.sheet.rows[9][3] = "一般手数"
    with pytest.raises(FuturesPayloadError, match="units changed"):
        _parse()


def test_unrecognized_contract_rows_fail_closed(parameter_rows):
    parameter_rows.sheet.rows[4][0] = "AD-2601"
    with pytest.raises(FuturesPayloadError, match="unknown parameter contract"):
        _parse()


def _daily_payload():
    return {
        "o_code": "0000",
        "report_date": "20260910",
        "update_date": "20260910 15:30:09",
        "o_cursor": [
            {
                "INSTRUMENTID": "al2609",
                "PRODUCTID": "al_f",
                "SPECLONGMARGINRATIO": 0.2,
                "SPECSHORTMARGINRATIO": 0.2,
                "HEDGLONGMARGINRATIO": 0.2,
                "HEDGSHORTMARGINRATIO": 0.2,
                "TRADEFEERATIO": 0,
                "TTRADEFEERATIO": 0,
                "TRADEFEEUNIT": 3,
                "TTRADEFEEUNIT": 1.5,
            }
        ],
    }


def _parse_daily(payload):
    return shfe_parameters.parse_daily_settlement_parameters(
        json.dumps(payload).encode(),
        report_date=date(2026, 9, 10),
        source_url="https://www.shfe.com.cn/data/tradedata/future/dailydata/js20260910.dat",
    )


def test_daily_report_keeps_post_settlement_timestamp_and_fee_unit():
    row = _parse_daily(_daily_payload())[0]
    assert row["settlement_date"] == date(2026, 9, 10)
    assert row["source_updated_at"] == "20260910 15:30:09"
    assert row["fee_unit"] == "cny_per_contract"
    assert row["fee_general_cny_per_contract"] == 3.0
    assert row["margin_general_long"] == 0.2


def test_daily_report_rejects_wrong_date_and_mixed_fee_units():
    payload = _daily_payload()
    payload["report_date"] = "20260911"
    with pytest.raises(FuturesPayloadError, match="report date changed"):
        _parse_daily(payload)
    payload["report_date"] = "20260910"
    payload["o_cursor"][0]["TRADEFEERATIO"] = 0.05
    with pytest.raises(FuturesPayloadError, match="ambiguous fee units"):
        _parse_daily(payload)
