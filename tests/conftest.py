"""
conftest.py — Shared fixtures for Stock Analysis HUD test suite.
All fixtures are deterministic and require no live API calls.
"""
import pytest
import pandas as pd
import numpy as np
import sys
import os

# ── Make app.py importable ────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ── App source (for static analysis tests) ───────────────────
@pytest.fixture(scope="session")
def app_source():
    with open(os.path.join(os.path.dirname(__file__), "..", "app.py"), "r") as f:
        return f.read()

# ── Minimal OHLCV price history DataFrame ────────────────────
@pytest.fixture
def sample_df():
    """250 days of deterministic synthetic price data."""
    np.random.seed(42)
    n = 250
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    close = 150.0 + np.cumsum(np.random.randn(n) * 2)
    close = np.maximum(close, 10)  # floor at $10
    df = pd.DataFrame({
        "Open":   close * 0.99,
        "High":   close * 1.01,
        "Low":    close * 0.98,
        "Close":  close,
        "Volume": np.random.randint(1_000_000, 5_000_000, n).astype(float),
    }, index=dates)
    return df

# ── Minimal info dict (what fetch_ticker_data returns) ───────
@pytest.fixture
def sample_info():
    return {
        "longName":               "NVIDIA Corporation",
        "shortName":              "NVIDIA Corporation",
        "sector":                 "Technology",
        "industry":               "Semiconductors",
        "marketCap":              2_800_000_000_000,
        "regularMarketPrice":     875.0,
        "fiftyTwoWeekHigh":       974.0,
        "fiftyTwoWeekLow":        400.0,
        "sharesOutstanding":      24_400_000_000,
        "trailingPE":             65.2,
        "forwardPE":              35.0,
        "priceToBook":            40.5,
        "returnOnEquity":         0.85,
        "operatingMargins":       0.62,
        "profitMargins":          0.55,
        "revenueGrowth":          1.22,
        "earningsGrowth":         2.70,
        "debtToEquity":           0.41,
        "currentRatio":           4.17,
        "dividendYield":          0.001,
        "targetMeanPrice":        1050.0,
        "targetHighPrice":        1200.0,
        "targetLowPrice":         800.0,
        "numberOfAnalystOpinions": 42,
        "recommendationKey":      "buy",
        "recommendationMean":     1.8,
    }

# ── Minimal Claude analysis dict ─────────────────────────────
@pytest.fixture
def sample_analysis():
    return {
        "verdict":           "SWING TRADE",
        "confidence":        "High",
        "risk":              "Medium",
        "risk_reason":       "High volatility but strong momentum",
        "entry_low":         870.0,
        "entry_high":        880.0,
        "vwap":              872.0,
        "ema100":            820.0,
        "support1":          850.0,
        "support1_label":    "50MA Support",
        "support2":          800.0,
        "support2_label":    "200MA Support",
        "support3":          780.0,
        "support3_label":    "52W base",
        "resistance1":       920.0,
        "resistance1_label": "Recent high",
        "resistance2":       960.0,
        "resistance2_label": "ATH area",
        "resistance3":       1000.0,
        "resistance3_label": "Round number",
        "reasons_bull":      ["Strong revenue growth", "AI demand", "Above all MAs"],
        "reasons_bear":      ["High valuation", "Overbought RSI"],
        "summary":           "NVDA shows strong momentum with AI tailwinds.",
        "day_trade_note":    "High ATR provides intraday range.",
        "swing_note":        "Watch for breakout above $920.",
        "invest_note":       "Long-term AI story intact.",
        "earnings_date":     "May 28 2025",
        "earnings_days":     45,
        "last_earnings_beat":"Beat +12.5%",
        "sector":            "Technology",
        "chart_patterns":    [],
        "candle_patterns":   [],
        "trend_short":       "Uptrend",
        "trend_short_desc":  "Price above 20MA",
        "trend_medium":      "Uptrend",
        "trend_medium_desc": "Price above 50MA",
        "trend_long":        "Uptrend",
        "trend_long_desc":   "Price above 200MA",
        "pattern_bias":      "Bullish",
        "pattern_bias_desc": "Multiple confirming signals",
        "cycle_phase":       "Mid",
        "cycle_desc":        "Mid-cycle expansion",
        "market_risk":       "Moderate",
        "market_risk_desc":  "Broad market healthy",
        "news_sentiment":    [],
    }

# ── FMP rate-limit response ───────────────────────────────────
@pytest.fixture
def fmp_rate_limit_response():
    return {"Error Message": "Limit Reach . Please upgrade your plan or visit our documentation for more details at -- https://site.financialmodelingprep.com/developer/docs/pricing"}

@pytest.fixture
def fmp_auth_error_response():
    return {"message": "Invalid API KEY. Please retry or visit our documentation to create one FREE_KEY at https://financialmodelingprep.com/developer/docs"}

# ── Earnings history rows ─────────────────────────────────────
@pytest.fixture
def sample_earnings_hist():
    return [
        {"quarter": "2024-11-01", "estimate": 0.71, "actual": 0.81, "surprise": 14.1,  "beat": True},
        {"quarter": "2024-08-01", "estimate": 0.64, "actual": 0.68, "surprise":  6.3,  "beat": True},
        {"quarter": "2024-05-01", "estimate": 5.51, "actual": 6.12, "surprise": 11.1,  "beat": True},
        {"quarter": "2024-02-01", "estimate": 4.59, "actual": 5.16, "surprise": 12.4,  "beat": True},
    ]

# ── Insider transaction rows ──────────────────────────────────
@pytest.fixture
def sample_insider_buy():
    return {"Shares": 1000, "Value": 875000, "Text": "Purchase of shares",
            "Transaction": "Acquisition", "Insider": "Jensen Huang",
            "Position": "CEO", "Date": "2024-11-15"}

@pytest.fixture
def sample_insider_sell():
    return {"Shares": 5000, "Value": 4375000, "Text": "Sale of shares",
            "Transaction": "Dispose", "Insider": "Colette Kress",
            "Position": "CFO", "Date": "2024-11-10"}
