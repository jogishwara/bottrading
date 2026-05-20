"""
Central configuration for the Binance Futures Testnet bot.

All values are loaded from environment variables so the code can move between
paper, backtest, and testnet execution without hardcoded secrets or settings.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _get_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return int(value)


def _get_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return float(value)


@dataclass(frozen=True)
class Settings:
    api_key: str
    api_secret: str
    telegram_token: str
    telegram_chat_id: str

    mode: str
    exchange_env: str
    symbol: str
    timeframe: str
    ohlcv_limit: int
    poll_seconds: int

    fast_ema: int
    slow_ema: int

    leverage: int
    risk_per_trade: float
    trade_margin_usdt: float
    max_daily_loss: float
    stop_loss_pct: float
    take_profit_pct: float
    take_profit_on_margin_pct: float
    cooldown_seconds: int
    max_notional_pct: float
    min_trade_usdt: float

    paper_initial_balance: float
    backtest_initial_balance: float
    backtest_fee_rate: float
    backtest_candle_limit: int

    enable_websocket: bool
    websocket_url: str

    log_level: str
    log_dir: Path
    retry_attempts: int
    retry_delay_seconds: float
    retry_backoff: float

    # This project is intentionally testnet-only. Keep this true.
    use_sandbox: bool

    @classmethod
    def from_env(cls) -> "Settings":
        settings = cls(
            api_key=os.getenv("API_KEY", "sU2CpvUDQExrxmAJpEsGxNLa5F3JIEngK217cFbUx6DZf6JTGr0vhUvmDRVD9tOb"),
            api_secret=os.getenv("API_SECRET", "kpEksy8eaWh8dbRqWffh65alPbYrMPhFCSBRgNYh1N31yBlbwnaEg7VHm8tNI5iv"),
            telegram_token=os.getenv("TELEGRAM_TOKEN", ""),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            mode=os.getenv("MODE", "paper").strip().lower(),
            exchange_env=os.getenv("EXCHANGE_ENV", "demo").strip().lower(),
            symbol=os.getenv("SYMBOL", "BTC/USDT:USDT").strip(),
            timeframe=os.getenv("TIMEFRAME", "1m").strip(),
            ohlcv_limit=_get_int("OHLCV_LIMIT", 150),
            poll_seconds=_get_int("POLL_SECONDS", 15),
            fast_ema=_get_int("FAST_EMA", 9),
            slow_ema=_get_int("SLOW_EMA", 21),
            leverage=_get_int("LEVERAGE", 2),
            risk_per_trade=_get_float("RISK_PER_TRADE", 0.01),
            trade_margin_usdt=_get_float("TRADE_MARGIN_USDT", 0.0),
            max_daily_loss=_get_float("MAX_DAILY_LOSS", 0.03),
            stop_loss_pct=_get_float("STOP_LOSS_PCT", 0.01),
            take_profit_pct=_get_float("TAKE_PROFIT_PCT", 0.02),
            take_profit_on_margin_pct=_get_float("TAKE_PROFIT_ON_MARGIN_PCT", 0.0),
            cooldown_seconds=_get_int("COOLDOWN_SECONDS", 300),
            max_notional_pct=_get_float("MAX_NOTIONAL_PCT", 0.95),
            min_trade_usdt=_get_float("MIN_TRADE_USDT", 10.0),
            paper_initial_balance=_get_float("PAPER_INITIAL_BALANCE", 1000.0),
            backtest_initial_balance=_get_float("BACKTEST_INITIAL_BALANCE", 1000.0),
            backtest_fee_rate=_get_float("BACKTEST_FEE_RATE", 0.0004),
            backtest_candle_limit=_get_int("BACKTEST_CANDLE_LIMIT", 500),
            enable_websocket=_get_bool("ENABLE_WEBSOCKET", False),
            websocket_url=os.getenv(
                "WEBSOCKET_URL",
                "wss://stream.binancefuture.com/ws",
            ).strip(),
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
            log_dir=BASE_DIR / os.getenv("LOG_DIR", "logs"),
            retry_attempts=_get_int("RETRY_ATTEMPTS", 3),
            retry_delay_seconds=_get_float("RETRY_DELAY_SECONDS", 2.0),
            retry_backoff=_get_float("RETRY_BACKOFF", 2.0),
            use_sandbox=_get_bool("USE_SANDBOX", True),
        )
        settings.validate()
        return settings

    @property
    def is_live(self) -> bool:
        return self.mode == "live"

    @property
    def is_paper(self) -> bool:
        return self.mode == "paper"

    @property
    def is_backtest(self) -> bool:
        return self.mode == "backtest"

    @property
    def stream_symbol(self) -> str:
        # CCXT futures symbols look like BTC/USDT:USDT. Binance streams use btcusdt.
        base_quote = self.symbol.split(":")[0].replace("/", "").lower()
        return base_quote

    @property
    def daily_loss_limit_amount(self) -> float:
        return self.paper_initial_balance * self.max_daily_loss

    @property
    def effective_take_profit_pct(self) -> float:
        if self.take_profit_on_margin_pct > 0:
            return self.take_profit_on_margin_pct / self.leverage
        return self.take_profit_pct

    def validate(self) -> None:
        if self.mode not in {"live", "paper", "backtest"}:
            raise ValueError("MODE must be one of: live, paper, backtest")

        if self.exchange_env not in {"demo", "testnet"}:
            raise ValueError("EXCHANGE_ENV must be one of: demo, testnet")

        if not self.use_sandbox:
            raise ValueError("USE_SANDBOX must stay true. This bot is testnet-only.")

        if self.is_live and self.exchange_env == "testnet":
            raise ValueError(
                "CCXT no longer supports authenticated Binance futures calls through "
                "sandbox/testnet mode. Set EXCHANGE_ENV=demo for Binance Demo Trading."
            )

        if self.fast_ema >= self.slow_ema:
            raise ValueError("FAST_EMA must be lower than SLOW_EMA for this strategy.")

        if not 0 < self.risk_per_trade <= 1:
            raise ValueError("RISK_PER_TRADE must be between 0 and 1.")

        if self.trade_margin_usdt < 0:
            raise ValueError("TRADE_MARGIN_USDT must be 0 or greater.")

        if not 0 < self.max_daily_loss <= 1:
            raise ValueError("MAX_DAILY_LOSS must be between 0 and 1.")

        if self.stop_loss_pct <= 0 or self.take_profit_pct <= 0:
            raise ValueError("STOP_LOSS_PCT and TAKE_PROFIT_PCT must be positive.")

        if self.take_profit_on_margin_pct < 0:
            raise ValueError("TAKE_PROFIT_ON_MARGIN_PCT must be 0 or greater.")

        if self.leverage < 1:
            raise ValueError("LEVERAGE must be at least 1.")

        if self.is_live and (not self.api_key or not self.api_secret):
            raise ValueError("API_KEY and API_SECRET are required when MODE=live.")


settings = Settings.from_env()
