"""Exchange, execution, risk, notification, and performance components."""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, date, timezone
from functools import wraps
from typing import Any, Callable

import ccxt
import requests

from config import Settings
from strategy import Signal


def retry_api_call(func: Callable[..., Any]) -> Callable[..., Any]:
    """Retry transient exchange/API failures with exponential backoff."""

    @wraps(func)
    def wrapper(self: "Trader", *args: Any, **kwargs: Any) -> Any:
        delay = self.settings.retry_delay_seconds
        last_error: Exception | None = None

        for attempt in range(1, self.settings.retry_attempts + 1):
            try:
                return func(self, *args, **kwargs)
            except (ccxt.NetworkError, ccxt.ExchangeError, requests.RequestException) as exc:
                last_error = exc
                self.logger.warning(
                    "API failure in %s attempt %s/%s: %s",
                    func.__name__,
                    attempt,
                    self.settings.retry_attempts,
                    exc,
                )
                if attempt < self.settings.retry_attempts:
                    time.sleep(delay)
                    delay *= self.settings.retry_backoff

        raise RuntimeError(f"{func.__name__} failed after retries") from last_error

    return wrapper


class TelegramNotifier:
    """Small Telegram wrapper. It silently disables itself if credentials are empty."""

    def __init__(self, settings: Settings, logger: logging.Logger):
        self.settings = settings
        self.logger = logger
        self.enabled = bool(settings.telegram_token and settings.telegram_chat_id)

    def send(self, message: str) -> None:
        if not self.enabled:
            return

        url = f"https://api.telegram.org/bot{self.settings.telegram_token}/sendMessage"
        payload = {
            "chat_id": self.settings.telegram_chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
        except requests.RequestException as exc:
            self.logger.error("Telegram notification failed: %s", exc)


class TelegramCommandListener:
    """Polls Telegram commands and replies to the configured chat."""

    def __init__(
        self,
        settings: Settings,
        logger: logging.Logger,
        notifier: TelegramNotifier,
        status_provider: Callable[[], str],
    ):
        self.settings = settings
        self.logger = logger
        self.notifier = notifier
        self.status_provider = status_provider
        self.enabled = notifier.enabled
        self.offset: int | None = None
        self._running = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.enabled:
            return

        self._prime_offset()
        self._running.set()
        self._thread = threading.Thread(target=self._run, name="telegram-commands", daemon=True)
        self._thread.start()
        self.logger.info("Telegram command listener started. Available commands: /status, /help")

    def stop(self) -> None:
        self._running.clear()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def _prime_offset(self) -> None:
        try:
            updates = self._get_updates(timeout=0)
            if updates:
                self.offset = max(int(update["update_id"]) for update in updates) + 1
        except Exception as exc:
            self.logger.warning("Telegram command listener could not prime offset: %s", exc)

    def _run(self) -> None:
        while self._running.is_set():
            try:
                updates = self._get_updates(timeout=20)
                for update in updates:
                    self.offset = int(update["update_id"]) + 1
                    self._handle_update(update)
            except requests.RequestException as exc:
                self.logger.error("Telegram command polling failed: %s", exc)
                time.sleep(5)
            except Exception as exc:
                self.logger.exception("Telegram command handling failed: %s", exc)
                time.sleep(5)

    def _get_updates(self, timeout: int) -> list[dict[str, Any]]:
        url = f"https://api.telegram.org/bot{self.settings.telegram_token}/getUpdates"
        params: dict[str, Any] = {
            "timeout": timeout,
            "allowed_updates": json.dumps(["message"]),
        }
        if self.offset is not None:
            params["offset"] = self.offset

        response = requests.get(url, params=params, timeout=timeout + 10)
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram getUpdates failed: {payload}")
        return list(payload.get("result", []))

    def _handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        if chat_id != str(self.settings.telegram_chat_id):
            self.logger.warning("Ignored Telegram command from unauthorized chat_id=%s", chat_id)
            return

        text = str(message.get("text", "")).strip()
        if not text.startswith("/"):
            return

        command = text.split()[0].split("@")[0].lower()
        if command == "/status":
            self.notifier.send(self.status_provider())
        elif command in {"/help", "/start"}:
            self.notifier.send("Commands:\n/status - show current bot status\n/help - show this help")
        else:
            self.notifier.send("Unknown command. Send /help for available commands.")


@dataclass
class Position:
    side: str
    amount: float
    entry_price: float
    opened_at: datetime
    stop_loss: float
    take_profit: float
    exchange_position: dict[str, Any] = field(default_factory=dict)


@dataclass
class TradeRecord:
    side: str
    entry_price: float
    exit_price: float
    amount: float
    pnl: float
    reason: str
    opened_at: datetime
    closed_at: datetime


class PerformanceStats:
    """Tracks realized performance for live and paper sessions."""

    def __init__(self, starting_balance: float):
        self.starting_balance = starting_balance
        self.equity = starting_balance
        self.equity_curve: list[float] = [starting_balance]
        self.trades: list[TradeRecord] = []
        self.daily_pnl: dict[date, float] = {}

    def record_trade(self, trade: TradeRecord) -> None:
        self.trades.append(trade)
        self.equity += trade.pnl
        self.equity_curve.append(self.equity)
        trade_day = trade.closed_at.date()
        self.daily_pnl[trade_day] = self.daily_pnl.get(trade_day, 0.0) + trade.pnl

    @property
    def total_trades(self) -> int:
        return len(self.trades)

    @property
    def wins(self) -> int:
        return sum(1 for trade in self.trades if trade.pnl > 0)

    @property
    def winrate(self) -> float:
        if not self.trades:
            return 0.0
        return self.wins / len(self.trades) * 100

    @property
    def realized_pnl(self) -> float:
        return sum(trade.pnl for trade in self.trades)

    @property
    def max_drawdown(self) -> float:
        peak = self.equity_curve[0] if self.equity_curve else 0.0
        max_dd = 0.0
        for equity in self.equity_curve:
            peak = max(peak, equity)
            if peak > 0:
                max_dd = max(max_dd, (peak - equity) / peak * 100)
        return max_dd

    def pnl_today(self) -> float:
        return self.daily_pnl.get(datetime.now(timezone.utc).date(), 0.0)

    def summary(self) -> str:
        return (
            f"total_trades={self.total_trades} | winrate={self.winrate:.2f}% | "
            f"pnl={self.realized_pnl:.2f} | drawdown={self.max_drawdown:.2f}%"
        )


class RealtimePriceStream:
    """Optional Binance Futures testnet websocket price stream."""

    def __init__(self, settings: Settings, logger: logging.Logger):
        self.settings = settings
        self.logger = logger
        self.latest_price: float | None = None
        self._ws = None
        self._thread: threading.Thread | None = None
        self._running = threading.Event()

    def start(self) -> None:
        try:
            import websocket
        except ImportError:
            self.logger.warning("websocket-client is not installed; falling back to REST prices.")
            return

        stream_url = f"{self.settings.websocket_url}/{self.settings.stream_symbol}@trade"
        self._running.set()

        def on_message(_ws: Any, message: str) -> None:
            try:
                payload = json.loads(message)
                self.latest_price = float(payload["p"])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                self.logger.warning("Invalid websocket price payload: %s", exc)

        def on_error(_ws: Any, error: Any) -> None:
            self.logger.error("Websocket error: %s", error)

        def on_close(_ws: Any, _status_code: Any, _message: Any) -> None:
            self.logger.info("Websocket price stream closed.")

        def run() -> None:
            while self._running.is_set():
                self._ws = websocket.WebSocketApp(
                    stream_url,
                    on_message=on_message,
                    on_error=on_error,
                    on_close=on_close,
                )
                self._ws.run_forever(ping_interval=20, ping_timeout=10)
                if self._running.is_set():
                    time.sleep(5)

        self._thread = threading.Thread(target=run, name="price-websocket", daemon=True)
        self._thread.start()
        self.logger.info("Started testnet websocket price stream: %s", stream_url)

    def stop(self) -> None:
        self._running.clear()
        if self._ws:
            self._ws.close()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)


class Trader:
    """High-level trading service for testnet execution and paper simulation."""

    def __init__(
        self,
        settings: Settings,
        logger: logging.Logger,
        notifier: TelegramNotifier,
    ):
        self.settings = settings
        self.logger = logger
        self.notifier = notifier
        self.exchange: ccxt.binance | None = None
        self.paper_position: Position | None = None
        self.paper_balance = settings.paper_initial_balance
        self.last_trade_ts = 0.0
        self.stats = PerformanceStats(settings.paper_initial_balance)

    def connect(self) -> None:
        self.exchange = ccxt.binance(
            {
                "apiKey": self.settings.api_key,
                "secret": self.settings.api_secret,
                "enableRateLimit": True,
                "options": {
                    "defaultType": "future",
                    "defaultSubType": "linear",
                    "fetchMarkets": {"types": ["linear"]},
                    "adjustForTimeDifference": True,
                },
            }
        )
        self._enable_safe_binance_environment()
        self._assert_safe_binance_environment()

        try:
            self.exchange.load_markets()
            self.logger.info(
                "Connected to Binance Futures %s environment.",
                self.settings.exchange_env.upper(),
            )
            if self.settings.is_live:
                self.set_leverage()
        except Exception as exc:
            self.logger.exception("Exchange connection failed: %s", exc)
            raise

    def _enable_safe_binance_environment(self) -> None:
        if not self.exchange:
            raise RuntimeError("Exchange is not initialized.")

        if not self.settings.use_sandbox:
            raise RuntimeError("Sandbox mode is disabled. Refusing to start.")

        if self.settings.exchange_env == "demo":
            # Binance/CCXT deprecated authenticated futures sandbox calls.
            # Demo trading is now the supported virtual futures API environment.
            self.exchange.enable_demo_trading(True)
            return

        self.exchange.set_sandbox_mode(True)

    def _assert_safe_binance_environment(self) -> None:
        if not self.exchange:
            raise RuntimeError("Exchange is not initialized.")

        api_urls = self.exchange.urls.get("api", {})
        flat_urls = self._flatten_urls(api_urls)
        futures_urls = [
            url.lower()
            for url in flat_urls
            if "fapi" in url.lower() or "dapi" in url.lower() or "binancefuture" in url.lower()
        ]

        live_hosts = (
            "https://fapi.binance.com",
            "https://dapi.binance.com",
            "https://api.binance.com",
            "https://papi.binance.com",
        )
        if any(url.startswith(host) for url in flat_urls for host in live_hosts):
            raise RuntimeError("Live Binance futures endpoint detected. Refusing to start.")

        if self.settings.exchange_env == "demo":
            if futures_urls and not any("demo-" in url for url in futures_urls):
                raise RuntimeError("No Binance futures demo endpoint detected. Refusing to start.")
            return

        if futures_urls and not any("testnet" in url for url in futures_urls):
            raise RuntimeError("No Binance futures testnet endpoint detected. Refusing to start.")

    def _flatten_urls(self, value: Any) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            urls: list[str] = []
            for child in value.values():
                urls.extend(self._flatten_urls(child))
            return urls
        if isinstance(value, list):
            urls = []
            for child in value:
                urls.extend(self._flatten_urls(child))
            return urls
        return []

    @retry_api_call
    def set_leverage(self) -> None:
        if not self.exchange:
            raise RuntimeError("Exchange is not connected.")
        self.exchange.set_leverage(self.settings.leverage, self.settings.symbol)
        self.logger.info("Configured leverage: %sx for %s", self.settings.leverage, self.settings.symbol)

    @retry_api_call
    def fetch_ohlcv(self, limit: int | None = None) -> list[list[float]]:
        if not self.exchange:
            raise RuntimeError("Exchange is not connected.")
        return self.exchange.fetch_ohlcv(
            self.settings.symbol,
            timeframe=self.settings.timeframe,
            limit=limit or self.settings.ohlcv_limit,
        )

    @retry_api_call
    def fetch_realtime_price(self) -> float:
        if not self.exchange:
            raise RuntimeError("Exchange is not connected.")
        ticker = self.exchange.fetch_ticker(self.settings.symbol)
        return float(ticker["last"])

    @retry_api_call
    def fetch_balance_usdt(self) -> float:
        if self.settings.is_paper or self.settings.is_backtest:
            return self.paper_balance
        if not self.exchange:
            raise RuntimeError("Exchange is not connected.")

        balance = self.exchange.fetch_balance({"type": "future"})
        usdt = balance.get("USDT", {})
        free = usdt.get("free")
        total = usdt.get("total")
        return float(free if free is not None else total or 0.0)

    @retry_api_call
    def fetch_position(self) -> Position | None:
        if self.settings.is_paper:
            return self.paper_position
        if not self.exchange:
            raise RuntimeError("Exchange is not connected.")

        positions = self.exchange.fetch_positions([self.settings.symbol])
        for raw_position in positions:
            amount = self._position_amount(raw_position)
            if abs(amount) <= 0:
                continue

            side = "long" if amount > 0 else "short"
            entry_price = float(raw_position.get("entryPrice") or raw_position.get("entry_price") or 0)
            if entry_price <= 0:
                entry_price = float(raw_position.get("info", {}).get("entryPrice", 0) or 0)
            if entry_price <= 0:
                continue

            return Position(
                side=side,
                amount=abs(amount),
                entry_price=entry_price,
                opened_at=datetime.now(timezone.utc),
                stop_loss=self._stop_loss_price(side, entry_price),
                take_profit=self._take_profit_price(side, entry_price),
                exchange_position=raw_position,
            )
        return None

    def _position_amount(self, raw_position: dict[str, Any]) -> float:
        contracts = raw_position.get("contracts")
        side = raw_position.get("side")
        if contracts is not None and side:
            sign = 1 if str(side).lower() == "long" else -1
            return sign * float(contracts)

        info = raw_position.get("info", {})
        amount = info.get("positionAmt") or raw_position.get("contractSize") or 0
        return float(amount)

    def handle_signal(self, signal: Signal, price: float) -> None:
        try:
            position = self.fetch_position()
            if position:
                self._handle_open_position_signal(position, signal, price)
                return

            if signal in {Signal.BUY, Signal.SELL}:
                self.open_position(signal, price)
        except Exception as exc:
            self.logger.exception("Signal handling failed: %s", exc)
            self.notifier.send(f"ERROR: Signal handling failed: {exc}")

    def handle_risk_exit(self, price: float) -> None:
        try:
            position = self.fetch_position()
            if not position:
                return

            if position.side == "long":
                if price <= position.stop_loss:
                    self.close_position(position, price, "stop_loss")
                elif price >= position.take_profit:
                    self.close_position(position, price, "take_profit")
            else:
                if price >= position.stop_loss:
                    self.close_position(position, price, "stop_loss")
                elif price <= position.take_profit:
                    self.close_position(position, price, "take_profit")
        except Exception as exc:
            self.logger.exception("Risk exit check failed: %s", exc)
            self.notifier.send(f"ERROR: Risk exit check failed: {exc}")

    def _handle_open_position_signal(self, position: Position, signal: Signal, price: float) -> None:
        if position.side == "long" and signal == Signal.SELL:
            self.close_position(position, price, "opposite_signal")
        elif position.side == "short" and signal == Signal.BUY:
            self.close_position(position, price, "opposite_signal")
        elif signal != Signal.HOLD:
            self.logger.info("Duplicate position prevented: already %s %.6f", position.side, position.amount)

    def open_position(self, signal: Signal, price: float) -> None:
        if not self._can_trade():
            return

        balance = self.fetch_balance_usdt()
        amount = self.calculate_order_amount(balance, price)
        notional = amount * price

        if notional < self.settings.min_trade_usdt:
            self.logger.warning(
                "Order skipped: notional %.2f is below MIN_TRADE_USDT %.2f",
                notional,
                self.settings.min_trade_usdt,
            )
            return

        side = "long" if signal == Signal.BUY else "short"
        order_side = "buy" if signal == Signal.BUY else "sell"

        if self.settings.is_paper:
            self.paper_position = Position(
                side=side,
                amount=amount,
                entry_price=price,
                opened_at=datetime.now(timezone.utc),
                stop_loss=self._stop_loss_price(side, price),
                take_profit=self._take_profit_price(side, price),
            )
        else:
            self._place_market_order(order_side, amount, reduce_only=False)

        self.last_trade_ts = time.time()
        message = (
            f"ENTRY | {side.upper()} | amount={amount:.6f} | price={price:.2f} | "
            f"sl={self._stop_loss_price(side, price):.2f} | tp={self._take_profit_price(side, price):.2f}"
        )
        self.logger.info(message)
        self.notifier.send(message)

    def close_position(self, position: Position, price: float, reason: str) -> None:
        close_side = "sell" if position.side == "long" else "buy"

        if self.settings.is_paper:
            pnl = self._calculate_pnl(position, price)
            self.paper_balance += pnl
            self.paper_position = None
        else:
            self._place_market_order(close_side, position.amount, reduce_only=True)
            pnl = self._calculate_pnl(position, price)

        trade = TradeRecord(
            side=position.side,
            entry_price=position.entry_price,
            exit_price=price,
            amount=position.amount,
            pnl=pnl,
            reason=reason,
            opened_at=position.opened_at,
            closed_at=datetime.now(timezone.utc),
        )
        self.stats.record_trade(trade)
        self.last_trade_ts = time.time()

        message = (
            f"EXIT | {position.side.upper()} | reason={reason} | entry={position.entry_price:.2f} | "
            f"exit={price:.2f} | pnl={pnl:.2f} | {self.stats.summary()}"
        )
        self.logger.info(message)
        self.notifier.send(message)

    @retry_api_call
    def _place_market_order(self, side: str, amount: float, reduce_only: bool) -> dict[str, Any]:
        if not self.exchange:
            raise RuntimeError("Exchange is not connected.")

        params = {"reduceOnly": True} if reduce_only else {}
        precise_amount = float(self.exchange.amount_to_precision(self.settings.symbol, amount))
        order = self.exchange.create_order(
            symbol=self.settings.symbol,
            type="market",
            side=side,
            amount=precise_amount,
            params=params,
        )
        self.logger.info("Order submitted: side=%s amount=%s reduce_only=%s", side, precise_amount, reduce_only)
        return order

    def calculate_order_amount(self, balance: float, price: float) -> float:
        if self.settings.trade_margin_usdt > 0:
            if balance < self.settings.trade_margin_usdt:
                self.logger.warning(
                    "Order skipped: balance %.2f is below TRADE_MARGIN_USDT %.2f",
                    balance,
                    self.settings.trade_margin_usdt,
                )
                return 0.0

            notional = self.settings.trade_margin_usdt * self.settings.leverage
            amount = notional / price
            if self.exchange and not self.settings.is_paper:
                amount = float(self.exchange.amount_to_precision(self.settings.symbol, amount))
            return amount

        risk_amount = balance * self.settings.risk_per_trade
        stop_distance = price * self.settings.stop_loss_pct
        risk_based_amount = risk_amount / stop_distance

        max_notional = balance * self.settings.leverage * self.settings.max_notional_pct
        max_amount = max_notional / price

        amount = max(0.0, min(risk_based_amount, max_amount))
        if self.exchange and not self.settings.is_paper:
            amount = float(self.exchange.amount_to_precision(self.settings.symbol, amount))
        return amount

    def _can_trade(self) -> bool:
        seconds_since_trade = time.time() - self.last_trade_ts
        if seconds_since_trade < self.settings.cooldown_seconds:
            remaining = self.settings.cooldown_seconds - seconds_since_trade
            self.logger.info("Cooldown active: %.0f seconds remaining.", remaining)
            return False

        balance = self.fetch_balance_usdt()
        max_loss = balance * self.settings.max_daily_loss
        if self.stats.pnl_today() <= -max_loss:
            self.logger.warning(
                "Max daily loss reached: pnl_today=%.2f limit=-%.2f",
                self.stats.pnl_today(),
                max_loss,
            )
            self.notifier.send("Max daily loss reached. New entries are disabled for today.")
            return False

        return True

    def _stop_loss_price(self, side: str, entry_price: float) -> float:
        if side == "long":
            return entry_price * (1 - self.settings.stop_loss_pct)
        return entry_price * (1 + self.settings.stop_loss_pct)

    def _take_profit_price(self, side: str, entry_price: float) -> float:
        if side == "long":
            return entry_price * (1 + self.settings.effective_take_profit_pct)
        return entry_price * (1 - self.settings.effective_take_profit_pct)

    @staticmethod
    def _calculate_pnl(position: Position, exit_price: float) -> float:
        if position.side == "long":
            return (exit_price - position.entry_price) * position.amount
        return (position.entry_price - exit_price) * position.amount
