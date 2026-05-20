"""EMA crossover strategy and optional backtesting logic."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import pandas as pd


class Signal(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass(frozen=True)
class StrategyConfig:
    fast_ema: int = 9
    slow_ema: int = 21


class EMACrossoverStrategy:
    """Generates BUY/SELL only when the two EMA lines actually cross."""

    def __init__(self, config: StrategyConfig):
        self.config = config
        self.fast_column = f"ema_{config.fast_ema}"
        self.slow_column = f"ema_{config.slow_ema}"

    def latest_signal(self, df: pd.DataFrame) -> Signal:
        if len(df) < 2:
            return Signal.HOLD

        previous = df.iloc[-2]
        current = df.iloc[-1]

        crossed_up = (
            previous[self.fast_column] <= previous[self.slow_column]
            and current[self.fast_column] > current[self.slow_column]
        )
        crossed_down = (
            previous[self.fast_column] >= previous[self.slow_column]
            and current[self.fast_column] < current[self.slow_column]
        )

        if crossed_up:
            return Signal.BUY
        if crossed_down:
            return Signal.SELL
        return Signal.HOLD

    def signal_at(self, df: pd.DataFrame, index: int) -> Signal:
        if index < 1:
            return Signal.HOLD
        window = df.iloc[index - 1 : index + 1]
        return self.latest_signal(window)


@dataclass
class BacktestTrade:
    side: str
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    amount: float
    pnl: float
    exit_reason: str


@dataclass
class BacktestResult:
    initial_balance: float
    ending_balance: float
    total_trades: int
    wins: int
    losses: int
    pnl: float
    winrate: float
    max_drawdown: float
    trades: list[BacktestTrade] = field(default_factory=list)


class EMABacktester:
    """Simple single-position backtester for the same EMA strategy."""

    def __init__(
        self,
        strategy: EMACrossoverStrategy,
        initial_balance: float,
        risk_per_trade: float,
        stop_loss_pct: float,
        take_profit_pct: float,
        fee_rate: float,
    ):
        self.strategy = strategy
        self.initial_balance = initial_balance
        self.risk_per_trade = risk_per_trade
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.fee_rate = fee_rate

    def run(self, df: pd.DataFrame) -> BacktestResult:
        balance = self.initial_balance
        equity_curve = [balance]
        position: dict[str, float | str] | None = None
        trades: list[BacktestTrade] = []

        for index in range(1, len(df)):
            candle = df.iloc[index]
            signal = self.strategy.signal_at(df, index)

            if position:
                exit_price, reason = self._find_exit(position, candle, signal)
                if exit_price:
                    pnl = self._calculate_pnl(position, exit_price)
                    fees = (position["entry_price"] * position["amount"] + exit_price * position["amount"]) * self.fee_rate
                    pnl -= fees
                    balance += pnl
                    trades.append(
                        BacktestTrade(
                            side=str(position["side"]),
                            entry_time=str(position["entry_time"]),
                            exit_time=str(candle["timestamp"]),
                            entry_price=float(position["entry_price"]),
                            exit_price=float(exit_price),
                            amount=float(position["amount"]),
                            pnl=float(pnl),
                            exit_reason=reason,
                        )
                    )
                    equity_curve.append(balance)
                    position = None
                    continue

            if not position and signal in {Signal.BUY, Signal.SELL}:
                entry_price = float(candle["close"])
                risk_amount = balance * self.risk_per_trade
                amount = risk_amount / (entry_price * self.stop_loss_pct)
                position = {
                    "side": "long" if signal == Signal.BUY else "short",
                    "entry_time": str(candle["timestamp"]),
                    "entry_price": entry_price,
                    "amount": amount,
                }

        ending_balance = balance
        pnl = ending_balance - self.initial_balance
        wins = sum(1 for trade in trades if trade.pnl > 0)
        losses = sum(1 for trade in trades if trade.pnl <= 0)
        total = len(trades)
        winrate = (wins / total * 100) if total else 0.0
        max_drawdown = self._max_drawdown(equity_curve)

        return BacktestResult(
            initial_balance=self.initial_balance,
            ending_balance=ending_balance,
            total_trades=total,
            wins=wins,
            losses=losses,
            pnl=pnl,
            winrate=winrate,
            max_drawdown=max_drawdown,
            trades=trades,
        )

    def _find_exit(
        self,
        position: dict[str, float | str],
        candle: pd.Series,
        signal: Signal,
    ) -> tuple[float | None, str]:
        entry = float(position["entry_price"])
        side = str(position["side"])

        if side == "long":
            stop = entry * (1 - self.stop_loss_pct)
            target = entry * (1 + self.take_profit_pct)
            if float(candle["low"]) <= stop:
                return stop, "stop_loss"
            if float(candle["high"]) >= target:
                return target, "take_profit"
            if signal == Signal.SELL:
                return float(candle["close"]), "opposite_signal"
        else:
            stop = entry * (1 + self.stop_loss_pct)
            target = entry * (1 - self.take_profit_pct)
            if float(candle["high"]) >= stop:
                return stop, "stop_loss"
            if float(candle["low"]) <= target:
                return target, "take_profit"
            if signal == Signal.BUY:
                return float(candle["close"]), "opposite_signal"

        return None, ""

    @staticmethod
    def _calculate_pnl(position: dict[str, float | str], exit_price: float) -> float:
        entry = float(position["entry_price"])
        amount = float(position["amount"])
        if position["side"] == "long":
            return (exit_price - entry) * amount
        return (entry - exit_price) * amount

    @staticmethod
    def _max_drawdown(equity_curve: list[float]) -> float:
        peak = equity_curve[0] if equity_curve else 0.0
        max_dd = 0.0
        for equity in equity_curve:
            peak = max(peak, equity)
            if peak > 0:
                max_dd = max(max_dd, (peak - equity) / peak * 100)
        return max_dd
