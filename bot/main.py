"""Main entrypoint for the Binance Futures Testnet crypto trading bot."""

from __future__ import annotations

import signal
import sys
import time
from datetime import datetime, timezone
from threading import Event, Lock

from config import settings
from indicators import add_ema_columns, build_ohlcv_dataframe
from logger import setup_logger
from strategy import EMABacktester, EMACrossoverStrategy, Signal, StrategyConfig
from trader import RealtimePriceStream, TelegramCommandListener, TelegramNotifier, Trader


shutdown_requested = Event()


def request_shutdown(_signum: int, _frame: object) -> None:
    shutdown_requested.set()


class RuntimeStatusSnapshot:
    """Thread-safe cache used by Telegram /status without extra exchange calls."""

    def __init__(self) -> None:
        self.started_at = time.monotonic()
        self._lock = Lock()
        self._state: dict[str, object] = {
            "updated_at": None,
            "price": None,
            "signal": "STARTING",
            "position": "none",
            "balance": None,
            "stats": "total_trades=0 | winrate=0.00% | pnl=0.00 | drawdown=0.00%",
        }

    def update(
        self,
        price: float,
        signal_value: Signal,
        position_text: str,
        balance: float,
        stats_text: str,
    ) -> None:
        with self._lock:
            self._state = {
                "updated_at": datetime.now(timezone.utc),
                "price": price,
                "signal": signal_value.value,
                "position": position_text,
                "balance": balance,
                "stats": stats_text,
            }

    def render(self) -> str:
        with self._lock:
            state = dict(self._state)

        updated_at = state["updated_at"]
        if isinstance(updated_at, datetime):
            updated_text = updated_at.strftime("%Y-%m-%d %H:%M:%S UTC")
            age_seconds = max(0, int((datetime.now(timezone.utc) - updated_at).total_seconds()))
            age_text = f"{age_seconds}s ago"
        else:
            updated_text = "not yet"
            age_text = "waiting for first loop"

        price = state["price"]
        balance = state["balance"]
        price_text = f"{price:.4f}" if isinstance(price, float) else "unknown"
        balance_text = f"{balance:.2f} USDT" if isinstance(balance, float) else "unknown"

        margin_text = (
            f"{settings.trade_margin_usdt:.2f} USDT"
            if settings.trade_margin_usdt > 0
            else f"risk-based ({settings.risk_per_trade:.2%})"
        )
        tp_text = (
            f"{settings.take_profit_on_margin_pct:.2%} on margin "
            f"({settings.effective_take_profit_pct:.4%} price)"
            if settings.take_profit_on_margin_pct > 0
            else f"{settings.effective_take_profit_pct:.4%} price"
        )

        return (
            "Bot Status\n"
            f"Mode: {settings.mode.upper()} / {settings.exchange_env.upper()}\n"
            f"Symbol: {settings.symbol}\n"
            f"Uptime: {self._format_duration(time.monotonic() - self.started_at)}\n"
            f"Last update: {updated_text} ({age_text})\n"
            f"Price: {price_text}\n"
            f"Signal: {state['signal']}\n"
            f"Position: {state['position']}\n"
            f"Balance: {balance_text}\n"
            f"Leverage: {settings.leverage}x\n"
            f"Margin/trade: {margin_text}\n"
            f"Take profit: {tp_text}\n"
            f"Stop loss: {settings.stop_loss_pct:.4%} price\n"
            f"Stats: {state['stats']}"
        )

    @staticmethod
    def _format_duration(seconds: float) -> str:
        total_seconds = int(seconds)
        hours, remainder = divmod(total_seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        return f"{hours:02d}h {minutes:02d}m {secs:02d}s"


def run_backtest(trader: Trader, strategy: EMACrossoverStrategy) -> None:
    logger = trader.logger
    logger.info("Starting backtest for %s on timeframe %s", settings.symbol, settings.timeframe)

    raw_ohlcv = trader.fetch_ohlcv(limit=settings.backtest_candle_limit)
    df = build_ohlcv_dataframe(raw_ohlcv)
    df = add_ema_columns(df, settings.fast_ema, settings.slow_ema)

    backtester = EMABacktester(
        strategy=strategy,
        initial_balance=settings.backtest_initial_balance,
        risk_per_trade=settings.risk_per_trade,
        stop_loss_pct=settings.stop_loss_pct,
        take_profit_pct=settings.effective_take_profit_pct,
        fee_rate=settings.backtest_fee_rate,
    )
    result = backtester.run(df)

    logger.info(
        "BACKTEST RESULT | initial=%.2f | ending=%.2f | trades=%s | winrate=%.2f%% | "
        "pnl=%.2f | drawdown=%.2f%%",
        result.initial_balance,
        result.ending_balance,
        result.total_trades,
        result.winrate,
        result.pnl,
        result.max_drawdown,
    )

    for trade in result.trades[-5:]:
        logger.info(
            "BACKTEST TRADE | side=%s | entry=%s %.2f | exit=%s %.2f | pnl=%.2f | reason=%s",
            trade.side,
            trade.entry_time,
            trade.entry_price,
            trade.exit_time,
            trade.exit_price,
            trade.pnl,
            trade.exit_reason,
        )


def run_trading_loop(trader: Trader, strategy: EMACrossoverStrategy) -> None:
    logger = trader.logger
    price_stream = RealtimePriceStream(settings, logger) if settings.enable_websocket else None
    status_snapshot = RuntimeStatusSnapshot()
    command_listener = TelegramCommandListener(settings, logger, trader.notifier, status_snapshot.render)

    if price_stream:
        price_stream.start()
    command_listener.start()

    logger.info("Bot started in %s mode for %s.", settings.mode.upper(), settings.symbol)
    logger.info("Press Ctrl+C to stop gracefully.")

    try:
        while not shutdown_requested.is_set():
            try:
                raw_ohlcv = trader.fetch_ohlcv()
                df = build_ohlcv_dataframe(raw_ohlcv)
                df = add_ema_columns(df, settings.fast_ema, settings.slow_ema)
                signal_value = strategy.latest_signal(df)

                stream_price = price_stream.latest_price if price_stream else None
                price = stream_price or trader.fetch_realtime_price()
                position = trader.fetch_position()
                position_text = "none"
                if position:
                    position_text = f"{position.side} amount={position.amount:.6f} entry={position.entry_price:.2f}"
                balance = trader.fetch_balance_usdt()
                stats_text = trader.stats.summary()
                status_snapshot.update(price, signal_value, position_text, balance, stats_text)

                logger.info(
                    "STATUS | price=%.2f | signal=%s | position=%s | balance=%.2f | %s",
                    price,
                    signal_value.value,
                    position_text,
                    balance,
                    stats_text,
                )

                trader.handle_risk_exit(price)
                if signal_value != Signal.HOLD:
                    trader.handle_signal(signal_value, price)

            except Exception as exc:
                logger.exception("Main loop error: %s", exc)
                trader.notifier.send(f"ERROR: Main loop error: {exc}")

            shutdown_requested.wait(settings.poll_seconds)
    finally:
        command_listener.stop()
        if price_stream:
            price_stream.stop()
        logger.info("Graceful shutdown complete. Final stats: %s", trader.stats.summary())


def main() -> int:
    signal.signal(signal.SIGINT, request_shutdown)
    signal.signal(signal.SIGTERM, request_shutdown)

    logger = setup_logger(settings)
    notifier = TelegramNotifier(settings, logger)
    strategy = EMACrossoverStrategy(StrategyConfig(settings.fast_ema, settings.slow_ema))
    trader = Trader(settings, logger, notifier)

    try:
        trader.connect()
        if settings.is_backtest:
            run_backtest(trader, strategy)
        else:
            run_trading_loop(trader, strategy)
        return 0
    except Exception as exc:
        logger.exception("Bot stopped with fatal error: %s", exc)
        notifier.send(f"FATAL: Bot stopped: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
