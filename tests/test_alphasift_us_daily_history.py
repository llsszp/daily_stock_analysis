# -*- coding: utf-8 -*-

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd

from src.services import alphasift_service


def _daily_frame(rows: int = 30) -> pd.DataFrame:
    dates = pd.date_range(end=pd.Timestamp.now().normalize(), periods=rows, freq="B")
    return pd.DataFrame(
        {
            "date": dates,
            "open": range(100, 100 + rows),
            "high": range(101, 101 + rows),
            "low": range(99, 99 + rows),
            "close": range(100, 100 + rows),
            "volume": [1_000_000] * rows,
        }
    )


def _clear_daily_cache() -> None:
    with alphasift_service._DSA_US_DAILY_HISTORY_CACHE_LOCK:
        alphasift_service._DSA_US_DAILY_HISTORY_CACHE.clear()


def test_short_swing_deep_score_limit_matches_displayed_plan() -> None:
    assert (
        alphasift_service._dsa_us_daily_calibration_limit(
            "dsa_us_short_swing_recovery",
            40,
        )
        == 32
    )
    assert (
        alphasift_service._dsa_us_daily_calibration_limit(
            "dsa_us_short_swing_recovery",
            20,
        )
        == 20
    )


def test_us_screen_daily_calibration_prefers_akshare_without_using_manager() -> None:
    _clear_daily_cache()
    stock_us_daily = MagicMock(return_value=_daily_frame())
    fake_akshare = SimpleNamespace(stock_us_daily=stock_us_daily)
    manager = MagicMock()

    with (
        patch.dict(sys.modules, {"akshare": fake_akshare}),
        patch.object(alphasift_service, "_get_dsa_fetcher_manager", return_value=manager),
    ):
        frame, source, cache_used, error = (
            alphasift_service._load_dsa_us_daily_history_for_calibration("CLSK")
        )

    assert len(frame) == 30
    assert source == "AkshareSina"
    assert cache_used is False
    assert error == ""
    stock_us_daily.assert_called_once_with(symbol="CLSK", adjust="")
    manager.get_daily_data.assert_not_called()
    _clear_daily_cache()


def test_us_screen_daily_calibration_falls_back_to_manager() -> None:
    _clear_daily_cache()
    fake_akshare = SimpleNamespace(
        stock_us_daily=MagicMock(side_effect=RuntimeError("sina unavailable"))
    )
    manager = MagicMock()
    manager.get_daily_data.return_value = (_daily_frame(), "YfinanceFetcher")

    with (
        patch.dict(sys.modules, {"akshare": fake_akshare}),
        patch.object(alphasift_service, "_get_dsa_fetcher_manager", return_value=manager),
        patch.object(alphasift_service.time, "sleep"),
    ):
        frame, source, cache_used, error = (
            alphasift_service._load_dsa_us_daily_history_for_calibration("AAPL")
        )

    assert len(frame) == 30
    assert source == "YfinanceFetcher"
    assert cache_used is False
    assert error == ""
    manager.get_daily_data.assert_called_once_with(
        "AAPL",
        days=alphasift_service.DSA_US_DAILY_CALIBRATION_LOOKBACK_DAYS,
    )
    _clear_daily_cache()
