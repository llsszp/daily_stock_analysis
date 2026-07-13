# -*- coding: utf-8 -*-
"""
===================================
LongbridgeFetcher - 长桥兜底数据源 (Priority 5)
===================================

数据来源：长桥 OpenAPI (https://open.longbridge.com)
特点：覆盖美股 + 港股，可计算量比/换手率/PE 等 yfinance 缺失字段
定位：美股/港股最后兜底数据源

关键策略：
1. 组合 quote + static_info 接口计算 turnover_rate / pe_ratio / total_mv
2. 通过 history_candlesticks 计算 volume_ratio（近5日均量比）
3. 懒加载 QuoteContext，首次调用时才建立连接
4. static_info 进程内短缓存，减少重复请求（默认 24h，可调；见 LONGBRIDGE_STATIC_INFO_TTL_SECONDS）

凭证：优先使用 `LONGBRIDGE_OAUTH_CLIENT_ID` + SDK token 缓存（OAuth 2.0）；
Legacy API Key 三件套（`LONGBRIDGE_APP_KEY` / `LONGBRIDGE_APP_SECRET` / `LONGBRIDGE_ACCESS_TOKEN`）仍兼容。
可选：`LONGBRIDGE_STATIC_INFO_TTL_SECONDS`；SDK `language` 取自 `REPORT_LANGUAGE`，`log_path` 为 `{LOG_DIR}/longbridge_sdk.log`；
`LONGBRIDGE_HTTP_URL` / `LONGBRIDGE_QUOTE_WS_URL` / `LONGBRIDGE_TRADE_WS_URL` / `LONGBRIDGE_REGION` （见官方文档默认值）。
"""

import base64
import binascii
import json
import logging
import math
import os
import time
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List

import pandas as pd

from .base import BaseFetcher, STANDARD_COLUMNS
from .realtime_types import UnifiedRealtimeQuote, RealtimeSource, safe_float
from .us_index_mapping import is_us_stock_code, is_us_index_code

logger = logging.getLogger(__name__)

_DEFAULT_STATIC_INFO_TTL = 86400  # 24h
_DEFAULT_QUOTE_CACHE_TTL_SECONDS = 15
_DEFAULT_CONNECTION_COOLDOWN_SECONDS = 15
_DEFAULT_RECENT_INTRADAY_HOURS = 24
_DEFAULT_RECENT_INTRADAY_INTERVAL_MINUTES = 5
_LONGBRIDGE_TIMESTAMP_TZ = timezone(timedelta(hours=8))


def _static_info_ttl_seconds() -> int:
    """TTL for static_info cache; 0 disables caching (always fetch)."""
    raw = os.getenv("LONGBRIDGE_STATIC_INFO_TTL_SECONDS", "").strip()
    if raw == "":
        return _DEFAULT_STATIC_INFO_TTL
    try:
        return max(0, int(raw))
    except ValueError:
        return _DEFAULT_STATIC_INFO_TTL


def _quote_cache_ttl_seconds() -> int:
    raw = os.getenv("LONGBRIDGE_QUOTE_CACHE_TTL_SECONDS", "").strip()
    if raw == "":
        return _DEFAULT_QUOTE_CACHE_TTL_SECONDS
    try:
        return max(0, int(raw))
    except ValueError:
        return _DEFAULT_QUOTE_CACHE_TTL_SECONDS


def _connection_cooldown_seconds() -> int:
    """Cooldown after connection-close errors to avoid reconnect thrashing."""
    raw = os.getenv("LONGBRIDGE_CONNECTION_COOLDOWN_SECONDS", "").strip()
    if raw == "":
        return _DEFAULT_CONNECTION_COOLDOWN_SECONDS
    try:
        return max(0, int(raw))
    except ValueError:
        return _DEFAULT_CONNECTION_COOLDOWN_SECONDS


_REGION_URL_MAP: Dict[str, Dict[str, str]] = {
    "cn": {
        "http_url": "https://openapi.longbridge.cn",
        "quote_ws_url": "wss://openapi-quote.longbridge.cn/v2",
        "trade_ws_url": "wss://openapi-trade.longbridge.cn/v2",
    },
    "hk": {
        "http_url": "https://openapi.longbridge.com",
        "quote_ws_url": "wss://openapi-quote.longbridge.com/v2",
        "trade_ws_url": "wss://openapi-trade.longbridge.com/v2",
    },
}


def _sanitize_longbridge_env() -> None:
    """Remove empty-string LONGBRIDGE_*_URL env vars.

    GitHub Actions sets ``LONGBRIDGE_HTTP_URL: ${{ vars.X || secrets.X }}``
    which resolves to an empty string ``""`` when neither var nor secret is
    configured.  The Rust SDK's ``Config.from_apikey()`` auto-reads these
    env vars, and an empty string is *not* the same as "unset" — it causes
    the SDK to use a blank URL, which breaks the WebSocket handshake and
    results in "context dropped" / "Client is closed" within milliseconds.

    Also mirrors ``LONGBRIDGE_REGION`` → ``LONGPORT_REGION`` because the
    Rust SDK's internal ``is_cn()`` function only checks ``LONGPORT_REGION``
    (not ``LONGBRIDGE_REGION``) when deciding which default endpoints to use.
    """
    for key in (
        "LONGBRIDGE_HTTP_URL",
        "LONGBRIDGE_QUOTE_WS_URL",
        "LONGBRIDGE_TRADE_WS_URL",
        "LONGBRIDGE_ENABLE_OVERNIGHT",
        "LONGBRIDGE_PUSH_CANDLESTICK_MODE",
        "LONGBRIDGE_PRINT_QUOTE_PACKAGES",
        "LONGBRIDGE_REGION",
        "LONGBRIDGE_STATIC_INFO_TTL_SECONDS",
        "LONGBRIDGE_LOG_PATH",
    ):
        val = os.environ.get(key)
        if val is not None and val.strip() == "":
            del os.environ[key]
            logger.debug("[Longbridge] 删除空环境变量 %s", key)

    # App default: quiet (false). Matches README / docs/full-guide / .env.example; SDK alone may default verbose.
    if "LONGBRIDGE_PRINT_QUOTE_PACKAGES" not in os.environ:
        os.environ["LONGBRIDGE_PRINT_QUOTE_PACKAGES"] = "false"

    if not os.environ.get("LONGBRIDGE_LOG_PATH"):
        try:
            log_dir = (os.getenv("LOG_DIR") or "./logs").strip() or "./logs"
            p = Path(log_dir).expanduser()
            p.mkdir(parents=True, exist_ok=True)
            os.environ["LONGBRIDGE_LOG_PATH"] = str(p / "longbridge_sdk.log")
            logger.debug("[Longbridge] 设置 LONGBRIDGE_LOG_PATH=%s",
                         os.environ["LONGBRIDGE_LOG_PATH"])
        except Exception:
            pass

    region = (os.getenv("LONGBRIDGE_REGION") or "").strip().lower()
    if region:
        if not os.environ.get("LONGPORT_REGION"):
            os.environ["LONGPORT_REGION"] = region
            logger.debug("[Longbridge] 同步 LONGPORT_REGION=%s", region)

        urls = _REGION_URL_MAP.get(region, {})
        for env_name, default_url in (
            ("LONGBRIDGE_HTTP_URL", urls.get("http_url")),
            ("LONGBRIDGE_QUOTE_WS_URL", urls.get("quote_ws_url")),
            ("LONGBRIDGE_TRADE_WS_URL", urls.get("trade_ws_url")),
        ):
            if default_url and not os.environ.get(env_name):
                os.environ[env_name] = default_url
                logger.debug("[Longbridge] 根据 REGION=%s 设置 %s=%s",
                             region, env_name, default_url)


def _longbridge_config_kwargs() -> Dict[str, Any]:
    """Optional kwargs for ``Config.from_apikey`` (Longbridge OpenAPI SDK)."""
    try:
        import inspect
        from longbridge.openapi import Config, Language, PushCandlestickMode
    except Exception:
        return {}

    try:
        params = inspect.signature(Config.from_apikey).parameters
    except Exception:
        return {}

    kw: Dict[str, Any] = {}

    if "enable_print_quote_packages" in params:
        # Unset / empty → False (quiet); SDK default would be verbose — we opt in explicitly.
        raw = os.getenv("LONGBRIDGE_PRINT_QUOTE_PACKAGES")
        if raw is None or not str(raw).strip():
            kw["enable_print_quote_packages"] = False
        else:
            raw_norm = str(raw).strip().lower()
            kw["enable_print_quote_packages"] = raw_norm not in ("0", "false", "no")

    for pname, envname in (
        ("http_url", "LONGBRIDGE_HTTP_URL"),
        ("quote_ws_url", "LONGBRIDGE_QUOTE_WS_URL"),
        ("trade_ws_url", "LONGBRIDGE_TRADE_WS_URL"),
    ):
        if pname in params:
            v = os.getenv(envname, "").strip()
            if v:
                kw[pname] = v

    if "language" in params:
        try:
            from src.report_language import normalize_report_language

            rl = normalize_report_language(os.getenv("REPORT_LANGUAGE"), default="zh")
            if rl == "zh":
                kw["language"] = Language.ZH_CN
            elif rl == "en":
                kw["language"] = Language.EN
        except Exception as e:
            logger.debug("Longbridge language from REPORT_LANGUAGE skipped: %s", e)

    if "enable_overnight" in params:
        o = os.getenv("LONGBRIDGE_ENABLE_OVERNIGHT", "").strip().lower()
        if o:
            kw["enable_overnight"] = o in ("1", "true", "yes")

    if "push_candlestick_mode" in params:
        cm = os.getenv("LONGBRIDGE_PUSH_CANDLESTICK_MODE", "").strip().lower()
        if cm == "realtime":
            kw["push_candlestick_mode"] = PushCandlestickMode.Realtime
        elif cm == "confirmed":
            kw["push_candlestick_mode"] = PushCandlestickMode.Confirmed
        elif cm:
            logger.warning(
                "Unknown LONGBRIDGE_PUSH_CANDLESTICK_MODE=%r; use realtime or confirmed", cm
            )

    if "log_path" in params:
        try:
            log_dir = (os.getenv("LOG_DIR") or "./logs").strip() or "./logs"
            p = Path(log_dir).expanduser()
            p.mkdir(parents=True, exist_ok=True)
            kw["log_path"] = str(p / "longbridge_sdk.log")
        except Exception as e:
            logger.debug("Longbridge log_path from LOG_DIR skipped: %s", e)

    return kw


def _clean_optional(value: Any) -> Optional[str]:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _oauth_token_cache_path(client_id: str) -> Path:
    return Path.home() / ".longbridge" / "openapi" / "tokens" / client_id


def _restore_oauth_token_cache_from_env(client_id: str) -> bool:
    """Restore the SDK OAuth token cache from a GitHub Actions/Docker secret.

    The Longbridge SDK expects its OAuth token cache at
    ``~/.longbridge/openapi/tokens/<client_id>``.  In headless environments the
    file can be provided as base64 through ``LONGBRIDGE_OAUTH_TOKEN_CACHE_B64``.
    If an env cache is supplied and differs from the existing file, treat the
    env value as the operator-provided recovery source for headless runs.
    """
    raw = os.getenv("LONGBRIDGE_OAUTH_TOKEN_CACHE_B64")
    if not raw:
        return False

    try:
        payload = base64.b64decode("".join(raw.split()), validate=True)
    except (binascii.Error, ValueError) as exc:
        logger.warning("[Longbridge] OAuth token cache base64 解码失败: %s", exc)
        return False

    if not payload:
        logger.warning("[Longbridge] OAuth token cache base64 为空，跳过恢复")
        return False

    token_cache = _oauth_token_cache_path(client_id)
    if token_cache.exists():
        try:
            if token_cache.read_bytes() == payload:
                logger.debug("[Longbridge] OAuth token 缓存已与 env secret 一致，跳过恢复: %s", token_cache)
                return False
        except OSError as exc:
            logger.warning("[Longbridge] 读取现有 OAuth token 缓存失败，将尝试用 env secret 覆盖: %s", exc)

    try:
        token_cache.parent.mkdir(parents=True, exist_ok=True)
        token_cache.write_bytes(payload)
        token_cache.chmod(0o600)
        logger.info("[Longbridge] 已从 LONGBRIDGE_OAUTH_TOKEN_CACHE_B64 恢复 OAuth token 缓存")
        return True
    except Exception as exc:
        logger.warning("[Longbridge] 写入 OAuth token 缓存失败: %s", exc)
        return False


def _is_valid_oauth_cache_file(token_cache: Path) -> bool:
    """Basic health check for SDK token cache content.

    We avoid attempting interactive OAuth flows when the cache is missing or
    malformed, so headless jobs fail explicitly instead of hanging for manual
    re-authorization.
    """
    if not token_cache.exists():
        return False

    try:
        raw = token_cache.read_bytes()
    except OSError as exc:
        logger.warning("[Longbridge] 读取 OAuth token 缓存失败: %s", exc)
        return False

    if not raw.strip():
        logger.warning("[Longbridge] OAuth token 缓存为空文件: %s", token_cache)
        return False

    try:
        payload = raw.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        logger.warning("[Longbridge] OAuth token 缓存不是 UTF-8 文本: %s", exc)
        return False

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        logger.warning("[Longbridge] OAuth token 缓存不是合法 JSON: %s", exc)
        return False

    if not isinstance(data, dict) or not data:
        logger.warning("[Longbridge] OAuth token 缓存内容为空或格式不符合预期: %s", token_cache)
        return False

    return True


def _oauth_reauth_not_supported(url: str) -> None:
    raise RuntimeError(
        f"OAuth token 缓存已失效或缺失，当前为无头运行不支持打开授权页面，请重建 LONGBRIDGE_OAUTH_TOKEN_CACHE_B64: {url}"
    )


def _oauth_sdk_unavailable_error() -> RuntimeError:
    return RuntimeError(
        "当前安装的 longbridge SDK 不支持 OAuth 2.0（缺少 OAuthBuilder/Config.from_oauth）。"
        "请在支持该 SDK 版本的平台安装 longbridge>=4.0.0，或继续使用 Legacy 三件套。"
    )


def _longbridge_credentials(config: Any = None) -> Dict[str, Optional[str]]:
    """Collect Longbridge auth inputs from Config/env without exposing secrets."""
    app_key = _clean_optional(getattr(config, "longbridge_app_key", None))
    app_secret = _clean_optional(getattr(config, "longbridge_app_secret", None))
    access_token = _clean_optional(getattr(config, "longbridge_access_token", None))
    oauth_client_id = _clean_optional(getattr(config, "longbridge_oauth_client_id", None))

    app_key = app_key or _clean_optional(os.getenv("LONGBRIDGE_APP_KEY"))
    app_secret = app_secret or _clean_optional(os.getenv("LONGBRIDGE_APP_SECRET"))
    access_token = access_token or _clean_optional(os.getenv("LONGBRIDGE_ACCESS_TOKEN"))
    oauth_client_id = oauth_client_id or _clean_optional(os.getenv("LONGBRIDGE_OAUTH_CLIENT_ID"))

    # Some Longbridge pages label the OAuth public client id as "App Key".
    # Use it as a compatibility alias only when no Legacy access token exists.
    if not oauth_client_id and app_key and not access_token:
        oauth_client_id = app_key

    return {
        "app_key": app_key,
        "app_secret": app_secret,
        "access_token": access_token,
        "oauth_client_id": oauth_client_id,
    }


def _has_legacy_credentials(creds: Dict[str, Optional[str]]) -> bool:
    return bool(creds.get("app_key") and creds.get("app_secret") and creds.get("access_token"))


def _has_oauth_credentials(creds: Dict[str, Optional[str]]) -> bool:
    return bool(creds.get("oauth_client_id"))


def _is_us_code(stock_code: str) -> bool:
    normalized = stock_code.strip().upper()
    return is_us_stock_code(normalized) or is_us_index_code(normalized)


def _is_hk_code(stock_code: str) -> bool:
    normalized = (stock_code or "").strip().upper()
    if normalized.startswith("HK"):
        digits = normalized[2:]
        return digits.isdigit() and 1 <= len(digits) <= 5
    if normalized.endswith(".HK"):
        return True
    if normalized.isdigit() and len(normalized) == 5:
        return True
    return False


def _to_longbridge_symbol(stock_code: str) -> Optional[str]:
    """Convert internal stock code to Longbridge symbol format.

    Examples:
        AAPL      -> AAPL.US
        HK00700   -> 0700.HK
        00700     -> 0700.HK (5-digit pure number treated as HK)
    """
    code = stock_code.strip()
    upper = code.upper()

    if upper.endswith(".US"):
        return upper
    if upper.endswith(".HK"):
        return upper

    if _is_us_code(code):
        return f"{upper}.US"

    if _is_hk_code(code):
        upper = code.upper()
        if upper.startswith("HK"):
            digits = upper[2:]
        else:
            digits = upper
        digits = digits.lstrip("0") or "0"
        return f"{digits.zfill(4)}.HK"

    return None


def _coerce_quote_timestamp(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_LONGBRIDGE_TIMESTAMP_TZ)
    return parsed.astimezone(timezone.utc)


def _select_latest_us_quote(q: Any) -> Dict[str, Any]:
    """Pick the latest available US regular/pre/post/overnight quote payload."""
    regular_price = safe_float(getattr(q, "last_done", None))
    candidates = []
    if regular_price is not None and regular_price > 0:
        candidates.append({
            "session": "regular",
            "payload": q,
            "price": regular_price,
            "timestamp": _coerce_quote_timestamp(getattr(q, "timestamp", None)),
        })

    for session, attr in (
        ("pre_market", "pre_market_quote"),
        ("post_market", "post_market_quote"),
        ("overnight", "overnight_quote"),
    ):
        payload = getattr(q, attr, None)
        if payload is None:
            continue
        price = safe_float(getattr(payload, "last_done", None))
        if price is None or price <= 0:
            continue
        candidates.append({
            "session": session,
            "payload": payload,
            "price": price,
            "timestamp": _coerce_quote_timestamp(getattr(payload, "timestamp", None)),
        })

    if not candidates:
        return {"session": "regular", "payload": q, "price": None, "timestamp": None}

    timestamped = [item for item in candidates if item["timestamp"] is not None]
    if timestamped:
        return max(timestamped, key=lambda item: item["timestamp"])
    return candidates[0]


def _safe_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _round_float(value: Any, digits: int = 2) -> Optional[float]:
    number = safe_float(value)
    if number is None:
        return None
    return round(number, digits)


def _pct_change(start: Optional[float], end: Optional[float]) -> Optional[float]:
    if start is None or end is None or start <= 0:
        return None
    return round((end - start) / start * 100, 2)


def _compact_intraday_points(
    bars: List[Dict[str, Any]],
    *,
    max_points: int = 18,
) -> List[Dict[str, Any]]:
    if len(bars) <= max_points:
        return list(bars)
    step = max(1, math.ceil(len(bars) / max_points))
    selected = bars[::step]
    if selected[-1].get("timestamp") != bars[-1].get("timestamp"):
        selected.append(bars[-1])
    return selected[: max_points - 1] + [bars[-1]] if len(selected) > max_points else selected


def _window_change_from_bars(
    bars: List[Dict[str, Any]],
    *,
    minutes: int,
) -> Optional[Dict[str, Any]]:
    if not bars:
        return None
    end_ts = _coerce_quote_timestamp(bars[-1].get("timestamp"))
    if end_ts is None:
        return None
    cutoff = end_ts - timedelta(minutes=minutes)
    window = [
        bar for bar in bars
        if (_coerce_quote_timestamp(bar.get("timestamp")) or end_ts) >= cutoff
    ]
    if len(window) < 2:
        return None
    start_price = safe_float(window[0].get("open")) or safe_float(window[0].get("close"))
    end_price = safe_float(window[-1].get("close"))
    return {
        "start_time": window[0].get("timestamp"),
        "end_time": window[-1].get("timestamp"),
        "start_price": _round_float(start_price, 4),
        "end_price": _round_float(end_price, 4),
        "change_pct": _pct_change(start_price, end_price),
        "bar_count": len(window),
    }


def _summarize_recent_intraday_bars(
    bars: List[Dict[str, Any]],
    *,
    hours: int,
    interval_minutes: int,
) -> Optional[Dict[str, Any]]:
    if not bars:
        return None

    first = bars[0]
    last = bars[-1]
    start_price = safe_float(first.get("open")) or safe_float(first.get("close"))
    last_price = safe_float(last.get("close"))
    highs = [
        (safe_float(bar.get("high")), bar.get("timestamp"))
        for bar in bars
        if safe_float(bar.get("high")) is not None
    ]
    lows = [
        (safe_float(bar.get("low")), bar.get("timestamp"))
        for bar in bars
        if safe_float(bar.get("low")) is not None
    ]
    high_price, high_time = max(highs, key=lambda item: item[0]) if highs else (None, None)
    low_price, low_time = min(lows, key=lambda item: item[0]) if lows else (None, None)
    volume_total = sum(_safe_int(bar.get("volume")) or 0 for bar in bars)
    turnover_values = [_round_float(bar.get("turnover"), 4) for bar in bars]
    turnover_total = sum(value for value in turnover_values if value is not None)
    vwap = (
        round(turnover_total / volume_total, 4)
        if volume_total > 0 and turnover_total > 0
        else None
    )
    change_pct = _pct_change(start_price, last_price)
    range_pct = (
        round((high_price - low_price) / start_price * 100, 2)
        if start_price and start_price > 0 and high_price is not None and low_price is not None
        else None
    )
    drawdown_from_high_pct = (
        round((last_price - high_price) / high_price * 100, 2)
        if last_price is not None and high_price and high_price > 0
        else None
    )
    rebound_from_low_pct = (
        round((last_price - low_price) / low_price * 100, 2)
        if last_price is not None and low_price and low_price > 0
        else None
    )

    if change_pct is None:
        trend_label = "unknown"
    elif change_pct >= 2:
        trend_label = "strong_up"
    elif change_pct >= 0.5:
        trend_label = "up"
    elif change_pct <= -2:
        trend_label = "strong_down"
    elif change_pct <= -0.5:
        trend_label = "down"
    elif range_pct is not None and range_pct >= 2:
        trend_label = "range_bound"
    else:
        trend_label = "flat"

    return {
        "hours": int(hours),
        "interval_minutes": int(interval_minutes),
        "bar_count": len(bars),
        "start_time": first.get("timestamp"),
        "end_time": last.get("timestamp"),
        "start_price": _round_float(start_price, 4),
        "last_price": _round_float(last_price, 4),
        "high_price": _round_float(high_price, 4),
        "high_time": high_time,
        "low_price": _round_float(low_price, 4),
        "low_time": low_time,
        "change_pct": change_pct,
        "range_pct": range_pct,
        "drawdown_from_high_pct": drawdown_from_high_pct,
        "rebound_from_low_pct": rebound_from_low_pct,
        "volume_total": volume_total if volume_total > 0 else None,
        "turnover_total": round(turnover_total, 2) if turnover_total > 0 else None,
        "vwap": vwap,
        "trend_label": trend_label,
        "window_changes": {
            key: value
            for key, value in {
                "1h": _window_change_from_bars(bars, minutes=60),
                "4h": _window_change_from_bars(bars, minutes=240),
                "12h": _window_change_from_bars(bars, minutes=720),
                "24h": _window_change_from_bars(bars, minutes=1440),
            }.items()
            if value is not None
        },
    }


class LongbridgeFetcher(BaseFetcher):
    """
    长桥 OpenAPI 数据源实现

    优先级: 5（最低，作为美股/港股最后兜底）
    数据来源: Longbridge OpenAPI

    通过组合多个 API 计算 yfinance 缺失的指标:
    - turnover_rate = volume / circulating_shares * 100
    - volume_ratio = today_volume / avg_5day_volume
    - pe_ratio = price / eps_ttm
    """

    name = "LongbridgeFetcher"
    priority = int(os.getenv("LONGBRIDGE_PRIORITY", "5"))

    _CONNECTION_ERRORS = ("client is closed", "context closed", "connection closed")

    def __init__(self):
        self._ctx = None
        self._config = None
        self._ctx_lock = threading.Lock()
        self._available = None
        self._cooldown_until = 0.0
        # {symbol: (StaticInfo, timestamp)}
        self._static_cache: Dict[str, Any] = {}
        self._static_cache_lock = threading.Lock()
        self._quote_cache: Dict[str, Any] = {}
        self._quote_cache_lock = threading.Lock()

    def _is_connection_error(self, exc: Exception) -> bool:
        msg = str(exc).lower()
        return any(s in msg for s in self._CONNECTION_ERRORS)

    def _invalidate_ctx(self):
        """Reset cached context so the next call rebuilds the connection."""
        with self._ctx_lock:
            self._ctx = None
            self._config = None

    def _mark_connection_cooldown(self, exc: Exception) -> None:
        cooldown_seconds = _connection_cooldown_seconds()
        self._invalidate_ctx()
        if cooldown_seconds <= 0:
            return
        self._cooldown_until = time.time() + cooldown_seconds
        logger.warning(
            "[Longbridge] 检测到连接异常，进入 %ss 冷却期以避免频繁重连: %s",
            cooldown_seconds,
            exc,
        )

    def is_available_for_request(self, capability: str = "") -> bool:
        """Report request-time availability including temporary cooldown."""
        if not self._is_available():
            return False
        if self._cooldown_until > time.time():
            logger.debug(
                "[Longbridge] %s 冷却中，暂时跳过请求，剩余 %.1fs",
                capability or "request",
                self._cooldown_until - time.time(),
            )
            return False
        if self._cooldown_until:
            self._cooldown_until = 0.0
        return True

    def _is_available(self) -> bool:
        """Check if Longbridge credentials are configured (OAuth or Legacy)."""
        if self._available is not None:
            return self._available
        try:
            from src.config import get_config
            creds = _longbridge_credentials(get_config())
        except Exception:
            creds = _longbridge_credentials()
        self._available = _has_legacy_credentials(creds) or _has_oauth_credentials(creds)
        return self._available

    @staticmethod
    def has_configured_credentials(config: Any = None) -> bool:
        """Return True when runtime config can attempt Longbridge auth."""
        creds = _longbridge_credentials(config)
        return _has_legacy_credentials(creds) or _has_oauth_credentials(creds)

    def _get_ctx(self):
        """Lazy-init the QuoteContext (thread-safe)."""
        if self._ctx is not None:
            return self._ctx
        with self._ctx_lock:
            if self._ctx is not None:
                return self._ctx
            if not self._is_available():
                return None
            try:
                from longbridge.openapi import QuoteContext, Config

                # ── 1. Clean up empty URL env vars & apply REGION mapping ──
                _sanitize_longbridge_env()

                # ── 2. Collect credentials and mirror Legacy values to env ──
                try:
                    from src.config import get_config
                    app_config = get_config()
                except Exception:
                    app_config = None

                creds = _longbridge_credentials(app_config)
                app_key = creds["app_key"]
                app_secret = creds["app_secret"]
                access_token = creds["access_token"]
                oauth_client_id = creds["oauth_client_id"]
                has_legacy = _has_legacy_credentials(creds)

                for k, v in {
                    "LONGBRIDGE_APP_KEY": app_key,
                    "LONGBRIDGE_APP_SECRET": app_secret,
                    "LONGBRIDGE_ACCESS_TOKEN": access_token,
                }.items():
                    if v and not os.environ.get(k):
                        os.environ[k] = v
                if oauth_client_id and not os.environ.get("LONGBRIDGE_OAUTH_CLIENT_ID"):
                    os.environ["LONGBRIDGE_OAUTH_CLIENT_ID"] = oauth_client_id

                # ── 3. Build Config ──
                extra_kw = _longbridge_config_kwargs()
                lb_config = None
                oauth_error: Optional[Exception] = None

                if oauth_client_id:
                    token_cache = _oauth_token_cache_path(oauth_client_id)
                    _restore_oauth_token_cache_from_env(oauth_client_id)

                    if _is_valid_oauth_cache_file(token_cache):
                        try:
                            from longbridge.openapi import OAuthBuilder

                            oauth = OAuthBuilder(oauth_client_id).build(
                                _oauth_reauth_not_supported,
                            )
                            from_oauth = getattr(Config, "from_oauth", None)
                            if from_oauth is None:
                                raise AttributeError("Config.from_oauth")
                            lb_config = from_oauth(oauth)
                            logger.info("[Longbridge] Config.from_oauth() 创建成功")
                        except (ImportError, AttributeError):
                            oauth_error = _oauth_sdk_unavailable_error()
                            logger.warning("[Longbridge] OAuth SDK 不可用: %s", oauth_error)
                        except Exception as exc:
                            oauth_error = exc
                            logger.warning("[Longbridge] OAuth 初始化失败: %s", exc)
                    elif token_cache.exists():
                        logger.warning(
                            "[Longbridge] OAuth token 缓存内容异常，已拒绝交互式续期: %s。"
                            "请先执行 scripts/generate_longbridge_oauth_token.py 重建缓存。",
                            token_cache,
                        )
                    else:
                        logger.warning(
                            "[Longbridge] OAuth client 已配置，但 token 缓存不存在: %s。"
                            "请先执行 scripts/generate_longbridge_oauth_token.py 生成缓存；"
                            "GitHub Actions/Docker 可提供 LONGBRIDGE_OAUTH_TOKEN_CACHE_B64。",
                            token_cache,
                        )

                if lb_config is None and has_legacy:
                    # Legacy fallback is allowed only when the full non-empty
                    # three-piece credential exists; this keeps OAuth-only
                    # failures from being rewritten as API-key failures.
                    for factory_name in ("from_apikey_env", "from_env"):
                        factory = getattr(Config, factory_name, None)
                        if factory is None:
                            continue
                        try:
                            lb_config = factory()
                            logger.info("[Longbridge] Config.%s() 成功", factory_name)
                            break
                        except Exception as e:
                            logger.debug(
                                "[Longbridge] Config.%s() 失败: %s", factory_name, e
                            )

                if lb_config is None and has_legacy:
                    lb_config = Config.from_apikey(
                        app_key,
                        app_secret,
                        access_token,
                        **extra_kw,
                    )
                    logger.info("[Longbridge] Config.from_apikey() 创建成功")
                elif lb_config is None:
                    reason = (
                        f"OAuth 初始化失败: {oauth_error}"
                        if oauth_error
                        else "未找到可用 OAuth token 缓存且未配置完整 Legacy 三件套"
                    )
                    logger.warning("[Longbridge] 未建立认证配置: %s", reason)
                    self._available = False
                    return None

                # Diagnostic logging
                region = os.getenv("LONGBRIDGE_REGION") or os.getenv("LONGPORT_REGION") or "(auto)"
                logger.info(
                    "[Longbridge] 配置: region=%s, http=%s, quote_ws=%s",
                    region,
                    os.getenv("LONGBRIDGE_HTTP_URL", "(default)"),
                    os.getenv("LONGBRIDGE_QUOTE_WS_URL", "(default)"),
                )

                self._config = lb_config
                self._ctx = QuoteContext(lb_config)
                logger.info("[Longbridge] QuoteContext 初始化成功")
                return self._ctx
            except Exception as e:
                logger.warning("[Longbridge] QuoteContext 初始化失败: %s", e)
                self._available = False
                return None

    # ------------------------------------------------------------------
    # static_info with cache
    # ------------------------------------------------------------------

    def _get_static_info(self, symbol: str, *, cache_only: bool = False) -> Optional[Any]:
        """Fetch static info (shares, EPS, BPS, name) with optional in-process TTL cache."""
        ttl = _static_info_ttl_seconds()
        now = time.time()
        if ttl > 0:
            with self._static_cache_lock:
                cached = self._static_cache.get(symbol)
                if cached and (now - cached[1]) < ttl:
                    return cached[0]

        if cache_only:
            return None

        ctx = self._get_ctx()
        if ctx is None:
            return None
        try:
            infos = ctx.static_info([symbol])
            if infos:
                info = infos[0]
                if ttl > 0:
                    with self._static_cache_lock:
                        self._static_cache[symbol] = (info, now)
                return info
        except Exception as e:
            logger.debug(f"[Longbridge] static_info({symbol}) 失败: {e}")
            if self._is_connection_error(e):
                self._mark_connection_cooldown(e)
        return None

    def _get_cached_quote(self, symbol: str) -> Optional[Any]:
        ttl = _quote_cache_ttl_seconds()
        if ttl <= 0:
            return None
        with self._quote_cache_lock:
            cached = self._quote_cache.get(symbol)
            if cached and time.time() - cached[1] <= ttl:
                return cached[0]
        return None

    def _cache_quotes(self, quotes: List[Any], symbols: List[str]) -> int:
        now = time.time()
        cached_count = 0
        with self._quote_cache_lock:
            for index, quote in enumerate(quotes):
                raw_symbol = getattr(quote, "symbol", None)
                symbol = str(raw_symbol).strip().upper() if isinstance(raw_symbol, str) else ""
                if symbol not in symbols and index < len(symbols):
                    symbol = symbols[index]
                if not symbol:
                    continue
                self._quote_cache[symbol] = (quote, now)
                cached_count += 1
        return cached_count

    def prefetch_realtime_quotes(self, stock_codes: List[str], *, batch_size: int = 50) -> int:
        """Batch-fetch raw quotes and static metadata for immediate per-symbol reuse."""
        if not self.is_available_for_request("realtime_quote_prefetch"):
            return 0
        symbols = []
        for code in stock_codes:
            symbol = _to_longbridge_symbol(code)
            if symbol and symbol not in symbols:
                symbols.append(symbol)
        if not symbols:
            return 0
        ctx = self._get_ctx()
        if ctx is None:
            return 0

        batch_size = max(1, min(int(batch_size or 50), 100))
        cached_count = 0
        for offset in range(0, len(symbols), batch_size):
            batch = symbols[offset:offset + batch_size]
            try:
                quotes = list(ctx.quote(batch) or [])
                cached_count += self._cache_quotes(quotes, batch)
            except Exception as exc:
                logger.info("[Longbridge] 批量 quote(%s只) 失败: %s", len(batch), exc)
                if self._is_connection_error(exc):
                    self._mark_connection_cooldown(exc)
                break
            try:
                infos = list(ctx.static_info(batch) or [])
                now = time.time()
                with self._static_cache_lock:
                    for index, info in enumerate(infos):
                        raw_symbol = getattr(info, "symbol", None)
                        symbol = str(raw_symbol).strip().upper() if isinstance(raw_symbol, str) else ""
                        if symbol not in batch and index < len(batch):
                            symbol = batch[index]
                        if symbol:
                            self._static_cache[symbol] = (info, now)
            except Exception as exc:
                logger.debug("[Longbridge] 批量 static_info(%s只) 失败: %s", len(batch), exc)
        return cached_count

    # ------------------------------------------------------------------
    # get_stock_name via static_info
    # ------------------------------------------------------------------

    def get_stock_name(self, stock_code: str) -> Optional[str]:
        """Return stock name from Longbridge static_info (name_cn or name_en)."""
        symbol = _to_longbridge_symbol(stock_code)
        if symbol is None:
            return None
        info = self._get_static_info(symbol)
        if info is None:
            return None
        name = getattr(info, "name_cn", "") or getattr(info, "name_en", "") or ""
        return name.strip() or None

    # ------------------------------------------------------------------
    # volume_ratio from history
    # ------------------------------------------------------------------

    def _ts_sort_key(self, candle: Any) -> float:
        """Monotonic sort key for a candle timestamp (UTC seconds or datetime)."""
        ts = getattr(candle, "timestamp", None)
        if ts is None:
            return 0.0
        if hasattr(ts, "timestamp"):
            return float(ts.timestamp())
        return float(int(ts))

    def _compute_volume_ratio(self, symbol: str, today_volume: int) -> Optional[float]:
        """Compute volume_ratio = today_volume / avg(recent completed daily volumes).

        Uses the most recent daily bar as \"today/incomplete\" reference window: average
        volume of the next 5 older daily bars. Avoids local `date.today()` matching, which
        breaks for US symbols when the shell runs in CN timezone.
        """
        if not today_volume or today_volume <= 0:
            return None
        ctx = self._get_ctx()
        if ctx is None:
            return None
        try:
            from longbridge.openapi import Period, AdjustType

            candles = ctx.history_candlesticks_by_offset(
                symbol,
                Period.Day,
                AdjustType.NoAdjust,
                False,
                6,
                datetime.now(),
            )
            if not candles or len(candles) < 2:
                return None

            ordered = sorted(candles, key=self._ts_sort_key, reverse=True)
            past_vols: list = []
            for c in ordered[1:6]:
                vol = int(getattr(c, "volume", 0) or 0)
                if vol > 0:
                    past_vols.append(vol)

            if not past_vols:
                return None

            avg_vol = sum(past_vols) / len(past_vols)
            if avg_vol <= 0:
                return None

            return round(today_volume / avg_vol, 2)
        except Exception as e:
            logger.debug(f"[Longbridge] 计算量比失败({symbol}): {e}")
            return None

    # ------------------------------------------------------------------
    # get_realtime_quote
    # ------------------------------------------------------------------

    def get_realtime_quote(
        self,
        stock_code: str,
        *,
        include_volume_ratio: bool = True,
        cache_only: bool = False,
    ) -> Optional[UnifiedRealtimeQuote]:
        """Fetch realtime quote from Longbridge, computing derived fields."""
        if not self.is_available_for_request("realtime_quote"):
            return None

        symbol = _to_longbridge_symbol(stock_code)
        if symbol is None:
            logger.debug(f"[Longbridge] 无法转换代码: {stock_code}")
            return None

        q = self._get_cached_quote(symbol)
        if q is None:
            if cache_only:
                return None
            ctx = self._get_ctx()
            if ctx is None:
                return None
            try:
                quotes = ctx.quote([symbol])
                if not quotes:
                    return None
                q = quotes[0]
                self._cache_quotes([q], [symbol])
            except Exception as e:
                logger.info(f"[Longbridge] quote({symbol}) 失败: {e}")
                if self._is_connection_error(e):
                    self._mark_connection_cooldown(e)
                return None

        selected_quote = _select_latest_us_quote(q) if symbol.endswith(".US") else {
            "session": "regular",
            "payload": q,
            "price": safe_float(getattr(q, "last_done", None)),
            "timestamp": _coerce_quote_timestamp(getattr(q, "timestamp", None)),
        }
        quote_payload = selected_quote["payload"]
        market_session = selected_quote["session"]
        price = selected_quote["price"]
        if price is None or price <= 0:
            return None

        prev_close = safe_float(getattr(quote_payload, "prev_close", None))
        open_price = safe_float(getattr(q, "open", None))
        high = safe_float(getattr(quote_payload, "high", None))
        low = safe_float(getattr(quote_payload, "low", None))
        volume = int(getattr(quote_payload, "volume", 0) or 0)
        turnover = safe_float(getattr(quote_payload, "turnover", None))

        change_amount = None
        change_pct = None
        amplitude = None
        if prev_close and prev_close > 0:
            change_amount = round(price - prev_close, 4)
            change_pct = round((price - prev_close) / prev_close * 100, 2)
            if high is not None and low is not None:
                amplitude = round((high - low) / prev_close * 100, 2)

        # Fetch static info for derived fields
        static = self._get_static_info(symbol, cache_only=cache_only)

        turnover_rate = None
        pe_ratio = None
        pb_ratio = None
        total_mv = None
        circ_mv = None
        name = ""

        if static is not None:
            name = getattr(static, "name_cn", "") or getattr(static, "name_en", "") or ""
            circulating = int(getattr(static, "circulating_shares", 0) or 0)
            total_shares = int(getattr(static, "total_shares", 0) or 0)
            eps_ttm = safe_float(getattr(static, "eps_ttm", None))
            eps_plain = safe_float(getattr(static, "eps", None))
            bps = safe_float(getattr(static, "bps", None))

            # US names often report circulating_shares=0 while total_shares is set — use total for turnover.
            shares_for_turnover = circulating if circulating > 0 else total_shares
            if shares_for_turnover > 0 and volume > 0:
                turnover_rate = round(volume / shares_for_turnover * 100, 4)
            elif volume > 0:
                logger.debug(
                    "[Longbridge] %s 无法计算换手率: volume=%s circulating=%s total_shares=%s",
                    symbol,
                    volume,
                    circulating,
                    total_shares,
                )

            eps_for_pe = None
            if eps_ttm is not None and eps_ttm > 0:
                eps_for_pe = eps_ttm
            elif eps_plain is not None and eps_plain > 0:
                eps_for_pe = eps_plain
            if eps_for_pe:
                pe_ratio = round(price / eps_for_pe, 2)

            if bps is not None and bps > 0:
                pb_ratio = round(price / bps, 2)
            if total_shares > 0:
                total_mv = round(price * total_shares, 2)
            if circulating > 0:
                circ_mv = round(price * circulating, 2)

        volume_ratio = self._compute_volume_ratio(symbol, volume) if include_volume_ratio else None

        quote = UnifiedRealtimeQuote(
            code=stock_code,
            name=name,
            source=RealtimeSource.LONGBRIDGE,
            provider_timestamp=(
                selected_quote["timestamp"].isoformat()
                if selected_quote.get("timestamp") is not None
                else None
            ),
            market="us" if symbol.endswith(".US") else "hk" if symbol.endswith(".HK") else None,
            market_session=market_session,
            price=price,
            change_pct=change_pct,
            change_amount=change_amount,
            volume=volume if volume > 0 else None,
            amount=turnover,
            volume_ratio=volume_ratio,
            turnover_rate=turnover_rate,
            amplitude=amplitude,
            open_price=open_price,
            high=high,
            low=low,
            pre_close=prev_close,
            pe_ratio=pe_ratio,
            pb_ratio=pb_ratio,
            total_mv=total_mv,
            circ_mv=circ_mv,
        )

        logger.info(
            f"[Longbridge] {symbol} 行情获取成功: "
            f"价格={price}, 时段={market_session}, 量比={volume_ratio}, 换手率={turnover_rate}"
        )
        return quote

    def get_recent_intraday_price_action(
        self,
        stock_code: str,
        *,
        hours: int = _DEFAULT_RECENT_INTRADAY_HOURS,
        interval_minutes: int = _DEFAULT_RECENT_INTRADAY_INTERVAL_MINUTES,
    ) -> Optional[Dict[str, Any]]:
        """Fetch recent extended-session intraday bars and return a compact summary.

        Longbridge's minute K-line endpoint has per-request count limits, so the
        default uses 5-minute bars and filters the latest response down to the
        requested 24-hour window.  For US symbols, ``TradeSessions.All`` includes
        regular, pre-market, post-market, and overnight sessions when the account
        has the corresponding data permission and ``LONGBRIDGE_ENABLE_OVERNIGHT``
        is enabled.
        """
        if not self.is_available_for_request("recent_intraday_price_action"):
            return None

        hours = max(1, min(int(hours or _DEFAULT_RECENT_INTRADAY_HOURS), 72))
        interval_minutes = int(interval_minutes or _DEFAULT_RECENT_INTRADAY_INTERVAL_MINUTES)
        if interval_minutes != 5:
            logger.debug(
                "[Longbridge] recent intraday only supports 5-minute bars, got %s",
                interval_minutes,
            )
            interval_minutes = 5

        symbol = _to_longbridge_symbol(stock_code)
        if symbol is None:
            logger.debug("[Longbridge] 无法转换代码用于24小时走势: %s", stock_code)
            return None

        ctx = self._get_ctx()
        if ctx is None:
            return None

        try:
            from longbridge.openapi import AdjustType, Period, TradeSessions
        except Exception as exc:
            logger.debug("[Longbridge] 当前 SDK 不支持 intraday candlesticks: %s", exc)
            return None

        count = min(1000, max(60, math.ceil(hours * 60 / interval_minutes) + 12))
        try:
            candles = ctx.candlesticks(
                symbol,
                Period.Min_5,
                count,
                AdjustType.NoAdjust,
                TradeSessions.All,
            )
        except Exception as exc:
            logger.info("[Longbridge] candlesticks(%s) 最近%s小时走势失败: %s", symbol, hours, exc)
            if self._is_connection_error(exc):
                self._mark_connection_cooldown(exc)
            return None

        if not candles:
            return None

        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        rows: List[Dict[str, Any]] = []
        for candle in candles:
            ts = _coerce_quote_timestamp(getattr(candle, "timestamp", None))
            if ts is None or ts < cutoff:
                continue
            close_price = safe_float(getattr(candle, "close", None))
            if close_price is None or close_price <= 0:
                continue
            rows.append({
                "timestamp": ts.isoformat(),
                "open": _round_float(getattr(candle, "open", None), 4),
                "high": _round_float(getattr(candle, "high", None), 4),
                "low": _round_float(getattr(candle, "low", None), 4),
                "close": _round_float(close_price, 4),
                "volume": _safe_int(getattr(candle, "volume", None)),
                "turnover": _round_float(getattr(candle, "turnover", None), 4),
            })

        rows = sorted(rows, key=lambda item: item["timestamp"])
        if not rows:
            return None

        summary = _summarize_recent_intraday_bars(
            rows,
            hours=hours,
            interval_minutes=interval_minutes,
        )
        if not summary:
            return None

        payload = {
            "source": self.name,
            "symbol": symbol,
            "hours": hours,
            "interval_minutes": interval_minutes,
            "trade_sessions": "all",
            "bar_count": len(rows),
            "start_time": rows[0]["timestamp"],
            "end_time": rows[-1]["timestamp"],
            "summary": summary,
            "sample_points": _compact_intraday_points(rows, max_points=18),
            "recent_bars": rows[-12:],
        }
        logger.info(
            "[Longbridge] %s 最近%s小时走势获取成功: %s根%s分钟K线, change=%s%%",
            symbol,
            hours,
            len(rows),
            interval_minutes,
            summary.get("change_pct"),
        )
        return payload

    # ------------------------------------------------------------------
    # BaseFetcher abstract methods (historical daily data)
    # ------------------------------------------------------------------

    def _fetch_raw_data(
        self, stock_code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """Fetch historical candlesticks from Longbridge."""
        if not self.is_available_for_request("daily_data"):
            raise RuntimeError("Longbridge temporarily unavailable for daily_data")

        symbol = _to_longbridge_symbol(stock_code)
        if symbol is None:
            raise ValueError(f"Cannot convert {stock_code} to Longbridge symbol")

        ctx = self._get_ctx()
        if ctx is None:
            raise RuntimeError("Longbridge QuoteContext not available")

        from longbridge.openapi import Period, AdjustType

        start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
        end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()

        try:
            candles = ctx.history_candlesticks_by_date(
                symbol,
                Period.Day,
                AdjustType.ForwardAdjust,
                start_dt,
                end_dt,
            )
        except Exception as e:
            if self._is_connection_error(e):
                self._mark_connection_cooldown(e)
            raise

        if not candles:
            return pd.DataFrame()

        rows = []
        for c in candles:
            ts = getattr(c, "timestamp", None)
            if ts is None:
                continue
            if hasattr(ts, "date"):
                dt = ts.date()
            else:
                dt = datetime.fromtimestamp(int(ts)).date()

            rows.append({
                "date": dt.strftime("%Y-%m-%d"),
                "open": safe_float(getattr(c, "open", None)),
                "high": safe_float(getattr(c, "high", None)),
                "low": safe_float(getattr(c, "low", None)),
                "close": safe_float(getattr(c, "close", None)),
                "volume": int(getattr(c, "volume", 0) or 0),
                "turnover": safe_float(getattr(c, "turnover", None)),
            })

        return pd.DataFrame(rows)

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        """Normalize column names to standard format."""
        if df.empty:
            return pd.DataFrame(columns=STANDARD_COLUMNS)

        rename_map = {"turnover": "amount"}
        df = df.rename(columns=rename_map)

        if "pct_chg" not in df.columns and "close" in df.columns:
            df["pct_chg"] = df["close"].pct_change() * 100

        for col in STANDARD_COLUMNS:
            if col not in df.columns:
                df[col] = None

        return df[STANDARD_COLUMNS]
