# Binance Futures Demo Trading Bot

Professional, modular Python trading bot for Binance USD-M Futures Demo Trading. It supports authenticated Binance demo futures trading, paper trading, REST market data, optional websocket prices, EMA crossover signals, risk controls, Telegram alerts, and a simple backtest mode.

This repository is intentionally non-real-money only. CCXT no longer supports authenticated Binance futures calls through the old sandbox/testnet path, so authenticated futures trading uses Binance Demo Trading via `enable_demo_trading(True)`. Endpoint checks still refuse real Binance Futures hosts.

## Project Structure

```text
bot/
|-- main.py
|-- strategy.py
|-- indicators.py
|-- trader.py
|-- config.py
|-- logger.py
|-- requirements.txt
|-- .env.example
|-- README.md
`-- logs/
    |-- .gitkeep
    `-- sample.log
```

## Setup

Use Python 3.13 or newer. The current `pandas-ta` package available to pip requires Python 3.12+, and Python 3.13 has good 64-bit package support on this machine. Python 3.8 cannot install the required pandas version and will also fail on modern type-hint syntax used by the bot.

1. Create and activate a virtual environment:

```bash
py -3.13 -m venv .venv
.venv\Scripts\activate
```

If PowerShell blocks activation, run the venv Python directly:

```bash
.\.venv\Scripts\python.exe main.py
```

2. Install dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

3. Create your local environment file:

```bash
copy .env.example .env
```

4. Add Binance Demo Trading API credentials to `.env` only if you want `MODE=live`.

Create those keys from Binance Demo Trading API Management, not the old Futures Testnet dashboard and not your live Binance account.

## Run

From the `bot` directory:

```bash
python main.py
```

Recommended first run:

```env
MODE=paper
EXCHANGE_ENV=demo
USE_SANDBOX=true
```

Backtest mode:

```env
MODE=backtest
EXCHANGE_ENV=demo
USE_SANDBOX=true
```

Authenticated Binance demo futures execution:

```env
MODE=live
EXCHANGE_ENV=demo
API_KEY=your_demo_trading_key
API_SECRET=your_demo_trading_secret
USE_SANDBOX=true
```

## Strategy

The default strategy is an EMA crossover:

- Fast EMA: 9
- Slow EMA: 21
- BUY signal: EMA 9 crosses above EMA 21
- SELL signal: EMA 9 crosses below EMA 21

The bot opens one futures position at a time. A BUY signal opens or maintains a long position. A SELL signal opens or maintains a short position. If the opposite signal appears while a position is open, the bot exits the current position and waits for the cooldown before opening another trade.

## Risk Management

Risk controls are configured through `.env`:

- `RISK_PER_TRADE`: fraction of account balance risked per entry
- `MAX_DAILY_LOSS`: daily realized loss threshold that blocks new entries
- `STOP_LOSS_PCT`: local stop loss percentage
- `TAKE_PROFIT_PCT`: local take profit percentage
- `LEVERAGE`: futures leverage setting
- `COOLDOWN_SECONDS`: delay after entries and exits
- `MAX_NOTIONAL_PCT`: caps position notional relative to available leveraged balance

Stop loss and take profit are monitored locally by the running bot. For real production use, consider adding exchange-side reduce-only protective orders after every entry.

## Module Guide

- `config.py`: Loads environment variables, validates settings, and enforces safe non-real-money operation.
- `logger.py`: Configures console and rotating file logs.
- `indicators.py`: Converts CCXT OHLCV data into pandas DataFrames and adds pandas-ta EMA columns.
- `strategy.py`: Contains the EMA crossover signal engine and optional backtester.
- `trader.py`: Handles CCXT exchange connection, demo endpoint checks, market orders, risk checks, paper simulation, Telegram notifications, websocket prices, retries, and performance stats.
- `main.py`: Wires everything together, handles graceful shutdown, runs live/paper loops, or runs backtests.

## Sample Logs

```text
2026-05-12 09:15:00 | INFO | binance_futures_testnet_bot | Connected to Binance Futures DEMO environment.
2026-05-12 09:15:01 | INFO | binance_futures_testnet_bot | Bot started in PAPER mode for BTC/USDT:USDT.
2026-05-12 09:15:16 | INFO | binance_futures_testnet_bot | STATUS | price=64250.10 | signal=HOLD | position=none | balance=1000.00 | total_trades=0 | winrate=0.00% | pnl=0.00 | drawdown=0.00%
2026-05-12 09:18:31 | INFO | binance_futures_testnet_bot | ENTRY | LONG | amount=0.029571 | price=64250.10 | sl=63607.60 | tp=65535.10
2026-05-12 09:27:46 | INFO | binance_futures_testnet_bot | EXIT | LONG | reason=take_profit | entry=64250.10 | exit=65535.10 | pnl=37.99 | total_trades=1 | winrate=100.00% | pnl=37.99 | drawdown=0.00%
```

## Important Safety Notes

- Never put real Binance credentials in `.env`.
- Use Binance Demo Trading API credentials for `MODE=live`.
- Keep `EXCHANGE_ENV=demo` for authenticated futures API calls.
- Keep `USE_SANDBOX=true`.
- Demo liquidity and fills may differ from real markets.
- This is educational software, not financial advice.
