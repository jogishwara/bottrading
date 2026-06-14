from __future__ import annotations

import pandas as pd
import pandas_ta as ta


OHLCV_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


def build_ohlcv_dataframe(ohlcv: list[list[float]]) -> pd.DataFrame:
    """Convert raw CCXT OHLCV rows into a typed DataFrame."""
    df = pd.DataFrame(ohlcv, columns=OHLCV_COLUMNS)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)

    numeric_columns = ["open", "high", "low", "close", "volume"]
    df[numeric_columns] = df[numeric_columns].apply(pd.to_numeric, errors="coerce")
    df = df.dropna(subset=numeric_columns).reset_index(drop=True)
    return df


def add_ema_columns(df: pd.DataFrame, fast_length: int, slow_length: int) -> pd.DataFrame:
    """Add fast and slow EMA columns used by the crossover strategy."""
    enriched = df.copy()
    enriched[f"ema_{fast_length}"] = ta.ema(enriched["close"], length=fast_length)
    enriched[f"ema_{slow_length}"] = ta.ema(enriched["close"], length=slow_length)
    return enriched.dropna().reset_index(drop=True)
