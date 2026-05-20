"""Main entrypoint for the Binance Futures Testnet crypto trading bot."""

from __future__ import annotations

import signal
import sys
import time
from threading import Event

from config import settings
from indicators import add_ema_columns, build_ohlcv_dataframe
from logger import setup_logger
from strategy import EMABacktester, EMACrossoverStrategy, Signal, StrategyConfig
from trader import RealtimePriceStream, TelegramNotifier, Trader


shutdown_requested = Event()


def request_shutdown(_signum: int, _frame: object) -> None:
    shutdown_requested.set()


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

    if price_stream:
        price_stream.start()

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

                logger.info(
                    "STATUS | price=%.2f | signal=%s | position=%s | balance=%.2f | %s",
                    price,
                    signal_value.value,
                    position_text,
                    trader.fetch_balance_usdt(),
                    trader.stats.summary(),
                )

                trader.handle_risk_exit(price)
                if signal_value != Signal.HOLD:
                    trader.handle_signal(signal_value, price)

            except Exception as exc:
                logger.exception("Main loop error: %s", exc)
                trader.notifier.send(f"ERROR: Main loop error: {exc}")

            shutdown_requested.wait(settings.poll_seconds)
    finally:
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
