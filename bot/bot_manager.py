"""Manager for executing multiple bot instances in the background."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import database
from config import Settings
from indicators import add_ema_columns, build_ohlcv_dataframe
from strategy import EMACrossoverStrategy, Signal, StrategyConfig
from trader import RealtimePriceStream, TelegramCommandListener, TelegramNotifier, Trader, TradeRecord

LOG_DIR = Path(__file__).resolve().parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


def setup_user_logger(user_id: int, log_level: str = "INFO") -> logging.Logger:
    logger_name = f"bot_user_{user_id}"
    logger = logging.getLogger(logger_name)
    logger.setLevel(getattr(logging, log_level, logging.INFO))
    logger.propagate = False

    # Check if handlers already exist to avoid duplicates
    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        LOG_DIR / f"user_{user_id}.log",
        maxBytes=2_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    return logger


def settings_from_db(db_row: dict[str, Any]) -> Settings:
    return Settings(
        api_key=db_row.get("api_key", ""),
        api_secret=db_row.get("api_secret", ""),
        telegram_token=db_row.get("telegram_token", ""),
        telegram_chat_id=db_row.get("telegram_chat_id", ""),
        mode=db_row.get("mode", "paper"),
        exchange_env=db_row.get("exchange_env", "demo"),
        symbol=db_row.get("symbol", "SOL/USDT:USDT"),
        timeframe=db_row.get("timeframe", "1m"),
        ohlcv_limit=150,
        poll_seconds=15,
        fast_ema=db_row.get("fast_ema", 9),
        slow_ema=db_row.get("slow_ema", 21),
        leverage=db_row.get("leverage", 2),
        risk_per_trade=db_row.get("risk_per_trade", 0.01),
        trade_margin_usdt=db_row.get("trade_margin_usdt", 10.0),
        max_daily_loss=db_row.get("max_daily_loss", 0.03),
        stop_loss_pct=db_row.get("stop_loss_pct", 0.01),
        take_profit_pct=db_row.get("take_profit_pct", 0.02),
        take_profit_on_margin_pct=0.0,
        cooldown_seconds=db_row.get("cooldown_seconds", 300),
        max_notional_pct=0.95,
        min_trade_usdt=10.0,
        paper_initial_balance=db_row.get("paper_initial_balance", 1000.0),
        backtest_initial_balance=1000.0,
        backtest_fee_rate=0.0004,
        backtest_candle_limit=500,
        enable_websocket=bool(db_row.get("enable_websocket", 0)),
        websocket_url="wss://stream.binancefuture.com/ws",
        log_level="INFO",
        log_dir=LOG_DIR,
        retry_attempts=3,
        retry_delay_seconds=2.0,
        retry_backoff=2.0,
        use_sandbox=True,
    )


class UserBotRunner:
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.shutdown_requested = threading.Event()
        self.thread: threading.Thread | None = None
        self.started_at: float = 0.0
        self.logger = setup_user_logger(user_id)
        
        # Thread safe state container
        self._lock = threading.Lock()
        self.state = {
            "is_running": False,
            "price": 0.0,
            "signal": "HOLD",
            "position_side": "none",
            "position_amount": 0.0,
            "position_entry": 0.0,
            "balance": 0.0,
            "total_trades": 0,
            "win_rate": 0.0,
            "realized_pnl": 0.0,
            "drawdown": 0.0,
            "last_update": None,
            "uptime": "00:00:00",
        }

    def start(self) -> None:
        self.shutdown_requested.clear()
        self.started_at = time.monotonic()
        self.thread = threading.Thread(target=self._run, name=f"bot-{self.user_id}", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.shutdown_requested.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=10)
        with self._lock:
            self.state["is_running"] = False

    def get_state(self) -> dict[str, Any]:
        with self._lock:
            res = dict(self.state)
        # Calculate dynamic uptime
        if res["is_running"]:
            uptime_seconds = int(time.monotonic() - self.started_at)
            hours, remainder = divmod(uptime_seconds, 3600)
            minutes, secs = divmod(remainder, 60)
            res["uptime"] = f"{hours:02d}h {minutes:02d}m {secs:02d}s"
        return res

    def _update_state(self, updates: dict[str, Any]) -> None:
        with self._lock:
            self.state.update(updates)
            self.state["last_update"] = datetime.now(timezone.utc).isoformat()

    def _run(self) -> None:
        # Load user configuration
        config_row = database.get_user_config(self.user_id)
        if not config_row:
            self.logger.error("No configuration found in database.")
            return

        try:
            settings = settings_from_db(config_row)
        except Exception as exc:
            self.logger.error("Configuration validation failed: %s", exc)
            return

        self._update_state({"is_running": True})
        self.logger.info("Bot starting for user %s. Symbol: %s", self.user_id, settings.symbol)

        def save_trade_db(trade: TradeRecord) -> None:
            # Trade callback hook to record trades in database
            database.record_user_trade(
                user_id=self.user_id,
                side=trade.side,
                entry_price=trade.entry_price,
                exit_price=trade.exit_price,
                amount=trade.amount,
                pnl=trade.pnl,
                reason=trade.reason,
                opened_at=trade.opened_at.isoformat(),
                closed_at=trade.closed_at.isoformat(),
            )
            self.logger.info(
                "Trade persisted to database: %s PnL: %.2f",
                trade.side.upper(),
                trade.pnl,
            )

        # Build strategy & trader
        notifier = TelegramNotifier(settings, self.logger)
        strategy = EMACrossoverStrategy(StrategyConfig(settings.fast_ema, settings.slow_ema))
        trader = Trader(settings, self.logger, notifier, on_trade_recorded=save_trade_db)

        # Backfill performance stats from DB
        recent_trades = database.get_user_trades(self.user_id, limit=100)
        # Sort in chronological order to reconstruct equity curve
        recent_trades.reverse()
        for t in recent_trades:
            # Reconstruct TradeRecord object
            try:
                dt_open = datetime.fromisoformat(t["opened_at"])
                dt_close = datetime.fromisoformat(t["closed_at"])
            except ValueError:
                dt_open = datetime.now(timezone.utc)
                dt_close = datetime.now(timezone.utc)

            tr_obj = TradeRecord(
                side=t["side"],
                entry_price=t["entry_price"],
                exit_price=t["exit_price"],
                amount=t["amount"],
                pnl=t["pnl"],
                reason=t["reason"],
                opened_at=dt_open,
                closed_at=dt_close,
            )
            trader.stats.record_trade(tr_obj)

        try:
            trader.connect()
        except Exception as exc:
            self.logger.error("Failed to connect to exchange: %s", exc)
            self._update_state({"is_running": False})
            database.set_bot_running_status(self.user_id, False)
            return

        price_stream = RealtimePriceStream(settings, self.logger) if settings.enable_websocket else None
        command_listener = TelegramCommandListener(
            settings,
            self.logger,
            trader.notifier,
            lambda: f"User {self.user_id} status:\n{trader.stats.summary()}",
        )

        if price_stream:
            price_stream.start()
        command_listener.start()

        self.logger.info("Bot main loop running.")

        try:
            while not self.shutdown_requested.is_set():
                try:
                    raw_ohlcv = trader.fetch_ohlcv()
                    df = build_ohlcv_dataframe(raw_ohlcv)
                    df = add_ema_columns(df, settings.fast_ema, settings.slow_ema)
                    signal_value = strategy.latest_signal(df)

                    stream_price = price_stream.latest_price if price_stream else None
                    price = stream_price or trader.fetch_realtime_price()
                    
                    position = trader.fetch_position()
                    pos_side = "none"
                    pos_amount = 0.0
                    pos_entry = 0.0
                    if position:
                        pos_side = position.side
                        pos_amount = position.amount
                        pos_entry = position.entry_price

                    balance = trader.fetch_balance_usdt()
                    
                    # Calculate Technical Indicators
                    fast_col = f"ema_{settings.fast_ema}"
                    slow_col = f"ema_{settings.slow_ema}"
                    fast_val = float(df[fast_col].iloc[-1]) if fast_col in df else 0.0
                    slow_val = float(df[slow_col].iloc[-1]) if slow_col in df else 0.0
                    
                    try:
                        import pandas_ta as ta
                        df.ta.rsi(length=14, append=True)
                        df.ta.macd(fast=12, slow=26, signal=9, append=True)
                        rsi_val = float(df["RSI_14"].iloc[-1]) if "RSI_14" in df else 50.0
                        macd_val = float(df["MACD_12_26_9"].iloc[-1]) if "MACD_12_26_9" in df else 0.0
                    except Exception:
                        rsi_val = 50.0
                        macd_val = 0.0

                    ema_cross_val = "BULLISH" if fast_val > slow_val else "BEARISH"

                    # Update local state snapshot
                    self._update_state({
                        "price": price,
                        "signal": signal_value.value,
                        "position_side": pos_side,
                        "position_amount": pos_amount,
                        "position_entry": pos_entry,
                        "balance": balance,
                        "total_trades": trader.stats.total_trades,
                        "win_rate": trader.stats.winrate,
                        "realized_pnl": trader.stats.realized_pnl,
                        "drawdown": trader.stats.max_drawdown,
                        "indicators": {
                            "fast_ema": fast_val,
                            "slow_ema": slow_val,
                            "ema_cross": ema_cross_val,
                            "rsi": rsi_val,
                            "macd": macd_val
                        }
                    })

                    self.logger.info(
                        "Loop update | price=%.2f | signal=%s | position=%s %.4f | pnl=%.2f",
                        price,
                        signal_value.value,
                        pos_side,
                        pos_amount,
                        trader.stats.realized_pnl,
                    )

                    trader.handle_risk_exit(price)
                    if signal_value != Signal.HOLD:
                        trader.handle_signal(signal_value, price)

                except Exception as exc:
                    self.logger.exception("Error in bot trading loop: %s", exc)

                self.shutdown_requested.wait(settings.poll_seconds)

        finally:
            command_listener.stop()
            if price_stream:
                price_stream.stop()
            self.logger.info("Bot main loop stopped gracefully.")
            self._update_state({"is_running": False})


class BotManager:
    def __init__(self) -> None:
        self._runners: dict[int, UserBotRunner] = {}
        self._lock = threading.Lock()

    def start_bot(self, user_id: int) -> bool:
        with self._lock:
            # Check if already running
            if user_id in self._runners:
                runner = self._runners[user_id]
                state = runner.get_state()
                if state["is_running"]:
                    return True
                # If not actually running, clean it up
                runner.stop()
                del self._runners[user_id]

            runner = UserBotRunner(user_id)
            runner.start()
            self._runners[user_id] = runner
            database.set_bot_running_status(user_id, True)
            return True

    def stop_bot(self, user_id: int) -> bool:
        with self._lock:
            if user_id not in self._runners:
                database.set_bot_running_status(user_id, False)
                return True
            runner = self._runners[user_id]
            runner.stop()
            del self._runners[user_id]
            database.set_bot_running_status(user_id, False)
            return True

    def get_bot_state(self, user_id: int) -> dict[str, Any]:
        with self._lock:
            runner = self._runners.get(user_id)
        if runner:
            return runner.get_state()
        
        # Return stopped state from database
        cfg = database.get_user_config(user_id) or {}
        # Fetch trades count and stats from DB
        trades = database.get_user_trades(user_id, limit=100)
        total_trades = len(trades)
        wins = sum(1 for t in trades if t["pnl"] > 0)
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0
        realized_pnl = sum(t["pnl"] for t in trades)
        
        return {
            "is_running": bool(cfg.get("is_running", 0)),
            "price": 0.0,
            "signal": "HOLD",
            "position_side": "none",
            "position_amount": 0.0,
            "position_entry": 0.0,
            "balance": cfg.get("paper_initial_balance", 1000.0) if cfg.get("mode") == "paper" else 0.0,
            "total_trades": total_trades,
            "win_rate": win_rate,
            "realized_pnl": realized_pnl,
            "drawdown": 0.0,
            "last_update": None,
            "uptime": "Stopped",
            "indicators": {
                "fast_ema": 0.0,
                "slow_ema": 0.0,
                "ema_cross": "-",
                "rsi": 50.0,
                "macd": 0.0
            }
        }

    def get_bot_logs(self, user_id: int, num_lines: int = 100) -> str:
        log_file = LOG_DIR / f"user_{user_id}.log"
        if not log_file.exists():
            return "No logs found for this user."
        try:
            with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            return "".join(lines[-num_lines:])
        except Exception as exc:
            return f"Error reading logs: {exc}"

    def auto_resume_bots(self) -> None:
        running_users = database.get_running_user_ids()
        print(f"Auto-resuming bots for users: {running_users}")
        for user_id in running_users:
            try:
                self.start_bot(user_id)
            except Exception as exc:
                print(f"Failed to auto-resume bot for user {user_id}: {exc}")


# Create a global singleton instance
bot_manager = BotManager()
