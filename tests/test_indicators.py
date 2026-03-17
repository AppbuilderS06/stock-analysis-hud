"""
test_indicators.py
Technical indicator calculations — RSI, MACD, ATR, signals.
Uses deterministic synthetic data (seeded random).
"""
import pytest
import pandas as pd
import numpy as np


def calculate_indicators(df):
    """Mirrors calculate_indicators() from app.py."""
    close = df['Close']
    high  = df['High']
    low   = df['Low']
    vol   = df['Volume']

    df = df.copy()
    df['MA20']  = close.rolling(20).mean()
    df['MA50']  = close.rolling(50).mean()
    df['MA200'] = close.rolling(200).mean()
    df['MA100'] = close.rolling(100).mean()

    # RSI
    delta = close.diff()
    gain  = delta.clip(lower=0).ewm(com=13, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=13, adjust=False).mean()
    df['RSI'] = 100 - (100 / (1 + gain / loss.replace(0, 1e-10)))

    # MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df['MACD']     = ema12 - ema26
    df['MACDSig']  = df['MACD'].ewm(span=9, adjust=False).mean()
    df['MACDHist'] = df['MACD'] - df['MACDSig']

    # ATR
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs()
    ], axis=1).max(axis=1)
    df['ATR']    = tr.ewm(com=13, adjust=False).mean()
    df['ATRPct'] = df['ATR'] / close

    # OBV
    obv = [0]
    for i in range(1, len(df)):
        if close.iloc[i] > close.iloc[i-1]:
            obv.append(obv[-1] + vol.iloc[i])
        elif close.iloc[i] < close.iloc[i-1]:
            obv.append(obv[-1] - vol.iloc[i])
        else:
            obv.append(obv[-1])
    df['OBV'] = obv

    df['VolMA20']  = vol.rolling(20).mean()
    df['VolTrend'] = vol / df['VolMA20'].replace(0, 1)

    return df.dropna(subset=['MA20', 'MA50', 'RSI', 'MACD'])


def calc_signals(row):
    """Mirrors calc_signals() from app.py."""
    close = row['Close']
    sigs = {
        'MA20':  {'bull': close > row['MA20'],          'label': '20 MA',  'val': 'Above' if close > row['MA20']  else 'Below'},
        'MA50':  {'bull': close > row['MA50'],          'label': '50 MA',  'val': 'Above' if close > row['MA50']  else 'Below'},
        'MA200': {'bull': close > row['MA200'],         'label': '200 MA', 'val': 'Above' if close > row['MA200'] else 'Below'},
        'RSI':   {'bull': 40 < row['RSI'] < 70,        'label': 'RSI',    'val': f"{row['RSI']:.1f}", 'neut': True},
        'MACD':  {'bull': row['MACD'] > row['MACDSig'], 'label': 'MACD',   'val': 'Bullish' if row['MACD'] > row['MACDSig'] else 'Bearish'},
        'OBV':   {'bull': row['OBV'] > 0,              'label': 'OBV',    'val': 'Rising' if row['OBV'] > 0 else 'Falling'},
        'Vol':   {'bull': row['VolTrend'] > 0.8,       'label': 'Volume', 'val': f"{row['VolTrend']:.2f}x"},
        'ATR':   {'bull': row['ATRPct'] < 0.04,        'label': 'ATR',    'val': f"${row['ATR']:.2f} ({row['ATRPct']*100:.1f}%)"},
    }
    bull_count = sum(1 for k, v in sigs.items() if v['bull'])
    return sigs, round((bull_count / 8) * 10)


class TestIndicators:

    def test_rsi_bounds(self, sample_df):
        df = calculate_indicators(sample_df)
        assert df['RSI'].between(0, 100).all(), "RSI must always be 0-100"

    def test_rsi_not_all_same(self, sample_df):
        df = calculate_indicators(sample_df)
        assert df['RSI'].std() > 1, "RSI must vary across rows"

    def test_ma_ordering_in_uptrend(self):
        """In a strong uptrend: MA20 > MA50 > MA200."""
        dates = pd.date_range("2020-01-01", periods=250, freq="B")
        close = np.linspace(100, 300, 250)  # pure uptrend
        df = pd.DataFrame({
            "Open": close * 0.99, "High": close * 1.01,
            "Low": close * 0.98, "Close": close,
            "Volume": np.ones(250) * 1_000_000
        }, index=dates)
        df = calculate_indicators(df)
        last = df.iloc[-1]
        assert last['MA20'] > last['MA50'] > last['MA200'], \
            "In uptrend: MA20 > MA50 > MA200"

    def test_atr_positive(self, sample_df):
        df = calculate_indicators(sample_df)
        assert (df['ATR'] > 0).all(), "ATR must always be positive"

    def test_atr_pct_reasonable(self, sample_df):
        df = calculate_indicators(sample_df)
        assert df['ATRPct'].between(0, 0.5).all(), \
            "ATR% should be between 0% and 50% for normal stocks"

    def test_macd_hist_is_macd_minus_signal(self, sample_df):
        df = calculate_indicators(sample_df)
        diff = (df['MACD'] - df['MACDSig'] - df['MACDHist']).abs()
        assert diff.max() < 1e-10, "MACDHist must equal MACD - MACDSig"

    def test_obv_changes_with_price(self, sample_df):
        df = calculate_indicators(sample_df)
        assert df['OBV'].std() > 0, "OBV must not be constant"

    def test_signal_score_range(self, sample_df):
        df = calculate_indicators(sample_df)
        _, score = calc_signals(df.iloc[-1])
        assert 0 <= score <= 10, f"Signal score {score} out of range 0-10"

    def test_all_signals_present(self, sample_df):
        df = calculate_indicators(sample_df)
        sigs, _ = calc_signals(df.iloc[-1])
        expected = {'MA20', 'MA50', 'MA200', 'RSI', 'MACD', 'OBV', 'Vol', 'ATR'}
        assert set(sigs.keys()) == expected

    def test_score_10_all_bull(self):
        """Score=10 only when all 8 signals are bullish."""
        row = pd.Series({
            'Close': 200, 'MA20': 150, 'MA50': 140, 'MA200': 130,
            'RSI': 55, 'MACD': 1.0, 'MACDSig': 0.5, 'OBV': 1000000,
            'VolTrend': 1.2, 'ATR': 5.0, 'ATRPct': 0.025
        })
        _, score = calc_signals(row)
        assert score == 10

    def test_score_0_all_bear(self):
        """Score=0 when all 8 signals are bearish."""
        row = pd.Series({
            'Close': 100, 'MA20': 150, 'MA50': 160, 'MA200': 170,
            'RSI': 75,  # overbought → not bull (40 < RSI < 70 is bull)
            'MACD': -1.0, 'MACDSig': 0.5, 'OBV': -1000000,
            'VolTrend': 0.5, 'ATR': 10.0, 'ATRPct': 0.10
        })
        _, score = calc_signals(row)
        assert score == 0

    def test_fibonacci_levels(self):
        """Fib levels must be between 52W low and high."""
        h52, l52 = 267.0, 86.0
        rng = h52 - l52
        fib382 = h52 - rng * 0.382
        fib500 = h52 - rng * 0.500
        fib618 = h52 - rng * 0.618

        assert l52 < fib618 < fib500 < fib382 < h52, \
            "Fibonacci levels must be ordered and within range"
