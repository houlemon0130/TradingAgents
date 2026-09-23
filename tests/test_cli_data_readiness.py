"""A missing completed-session price must prevent any model analysis."""

from __future__ import annotations

import pandas as pd
import pytest

from cli import data_readiness


def _prices(last_close: str) -> str:
    return (
        "# Stock data for NVDA\n\n"
        "Date,Open,High,Low,Close,Volume\n"
        "2026-09-21,222.94,228.5,221.56,227.38,109806100\n"
        f"2026-09-22,{'' if not last_close else '227'},"
        f"{'' if not last_close else '230'},"
        f"{'' if not last_close else '225'},"
        f"{last_close},93296546\n"
    )


@pytest.mark.unit
def test_missing_latest_close_blocks_before_indicator_fetch(monkeypatch):
    monkeypatch.setattr(data_readiness, "route_to_vendor", lambda *a: _prices(""))
    monkeypatch.setattr(data_readiness, "load_ohlcv", lambda *a, **kw: pytest.fail("indicator fetch must not run"))
    with pytest.raises(data_readiness.DataNotReadyError, match="2026-09-22 has no Open, High, Low, Close"):
        data_readiness._require_complete_price("NVDA", "2026-09-23")


@pytest.mark.unit
def test_missing_entire_completed_session_blocks(monkeypatch):
    only_september_21 = _prices("228.87").split("2026-09-22,")[0]
    monkeypatch.setattr(data_readiness, "route_to_vendor", lambda *a: only_september_21)
    monkeypatch.setattr(data_readiness, "_last_us_completed_weekday", lambda *a: pd.Timestamp("2026-09-22").date())
    monkeypatch.setattr(data_readiness, "load_ohlcv", lambda *a, **kw: pytest.fail("indicator fetch must not run"))
    with pytest.raises(data_readiness.DataNotReadyError, match="latest expected completed US session is 2026-09-22"):
        data_readiness._require_complete_price("NVDA", "2026-09-23")


@pytest.mark.unit
def test_intraday_us_bar_cannot_be_treated_as_a_close(monkeypatch):
    intraday = _prices("228.87") + "2026-09-23,230,231,229,230.5,1000000\n"
    monkeypatch.setattr(data_readiness, "route_to_vendor", lambda *a: intraday)
    monkeypatch.setattr(data_readiness, "_last_us_completed_weekday", lambda *a: pd.Timestamp("2026-09-22").date())
    with pytest.raises(data_readiness.DataNotReadyError, match="latest expected completed US session is 2026-09-22"):
        data_readiness._require_complete_price("NVDA", "2026-09-23")


@pytest.mark.unit
def test_complete_latest_bar_and_indicator_date_pass(monkeypatch):
    monkeypatch.setattr(data_readiness, "route_to_vendor", lambda *a: _prices("228.87"))
    # A real indicator frame has a normalized Date column.
    monkeypatch.setattr(data_readiness, "load_ohlcv", lambda *a, **kw: pd.DataFrame({"Date": ["2026-09-22"], "Close": [228.87]}))
    data_readiness._require_complete_price("NVDA", "2026-09-23")


@pytest.mark.unit
def test_source_failure_blocks(monkeypatch):
    monkeypatch.setattr(data_readiness, "route_to_vendor", lambda *a: "DATA_UNAVAILABLE: upstream failed")
    with pytest.raises(data_readiness.DataNotReadyError, match="OHLCV"):
        data_readiness._require_complete_price("NVDA", "2026-09-23")


@pytest.mark.unit
def test_cli_does_not_create_graph_or_run_directory_when_data_is_missing(tmp_path, monkeypatch):
    import cli.main as main

    monkeypatch.setattr(main, "get_user_selections", lambda: {
        "ticker": "NVDA", "analysis_date": "2026-09-23",
        "asset_type": "stock", "analysts": [],
    })
    monkeypatch.setattr(main, "_build_run_config", lambda selections, checkpoint: {
        "results_dir": str(tmp_path / "reports")
    })
    monkeypatch.setattr(main, "check_data_readiness", lambda *a: (_ for _ in ()).throw(
        data_readiness.DataNotReadyError("2026-09-22 close missing")
    ))
    monkeypatch.setattr(main, "TradingAgentsGraph", lambda *a, **k: pytest.fail("graph must not start"))

    with pytest.raises(data_readiness.DataNotReadyError, match="close missing"):
        main.run_analysis()
    assert not (tmp_path / "reports").exists()
