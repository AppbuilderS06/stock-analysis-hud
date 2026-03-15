import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import anthropic
import json
from datetime import datetime, timedelta

# ── Page config ──────────────────────────────────────────────
st.set_page_config(
    page_title="Stock Analysis HUD",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ── Colors ────────────────────────────────────────────────────
C = {
    "bg":       "#111827",
    "panel":    "#1A2232",
    "header":   "#0F3030",
    "green":    "#00FF88",
    "red":      "#FF6B6B",
    "yellow":   "#FACC15",
    "blue":     "#38BDF8",
    "purple":   "#818CF8",
    "teal":     "#5EEAD4",
    "white":    "#F1F5F9",
    "muted":    "#94A3B8",
    "dim":      "#4A6080",
}

VERDICT_COLORS = {
    "SWING TRADE":     {"bg": "#0A1525", "border": "#38BDF8", "color": "#38BDF8"},
    "INVEST":          {"bg": "#0A1E12", "border": "#00FF88", "color": "#00FF88"},
    "DAY TRADE":       {"bg": "#1A1000", "border": "#FACC15", "color": "#FACC15"},
    "AVOID":           {"bg": "#1E0A0A", "border": "#FF6B6B", "color": "#FF6B6B"},
    "MULTI-TIMEFRAME": {"bg": "#0A1E12", "border": "#00FF88", "color": "#00FF88"},
}

# ── CSS ───────────────────────────────────────────────────────
st.markdown("""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;600;700&display=swap');

  html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
  .stApp { background: #111827; }
  .block-container { padding: 1rem 2rem 2rem; max-width: 1200px; }

  /* Hide Streamlit chrome */
  #MainMenu, footer, header { visibility: hidden; }
  .stDeployButton { display: none; }

  /* Identity bar */
  .identity-bar {
    background: linear-gradient(135deg, #112D20 0%, #112240 100%);
    border: 1px solid #14B8A6;
    border-radius: 10px;
    padding: 16px 22px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 6px;
  }
  .ticker-name { font-family: 'JetBrains Mono', monospace; font-size: 42px; font-weight: 800; color: #00FF88; letter-spacing: 4px; text-shadow: 0 0 20px #00FF8840; }
  .company-name { font-size: 16px; font-weight: 600; color: #E2E8F0; }
  .exchange-pill { font-size: 11px; color: #99F6E4; background: #0F3030; border: 1px solid #14B8A6; padding: 3px 10px; border-radius: 4px; letter-spacing: 1px; }
  .price-display { font-family: 'JetBrains Mono', monospace; font-size: 36px; font-weight: 800; color: #FACC15; text-align: right; }
  .price-change-up { display: inline-block; background: #052A14; border: 1px solid #00FF8844; border-radius: 6px; padding: 4px 12px; font-size: 14px; font-weight: 700; color: #00FF88; font-family: 'JetBrains Mono', monospace; }
  .price-change-dn { display: inline-block; background: #2D0A0A; border: 1px solid #FF6B6B44; border-radius: 6px; padding: 4px 12px; font-size: 14px; font-weight: 700; color: #FF6B6B; font-family: 'JetBrains Mono', monospace; }

  /* Status bar */
  .status-bar {
    background: linear-gradient(90deg, #0E2218 0%, #0E1C30 100%);
    border-radius: 6px; padding: 7px 16px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px; color: #3D6050;
    margin-bottom: 10px;
  }
  .status-bar span { color: #99F6E4; font-weight: 600; }

  /* Section panels */
  .section-header {
    background: #0F3030;
    padding: 7px 14px;
    font-size: 11px; color: #5EEAD4;
    letter-spacing: 2px; text-transform: uppercase;
    border-radius: 8px 8px 0 0;
    border-bottom: 1px solid #14B8A633;
    margin-top: 8px;
  }
  .panel-body {
    background: #1A2232;
    border: 1px solid #243348;
    border-top: none;
    border-radius: 0 0 8px 8px;
    padding: 0;
    margin-bottom: 8px;
  }
  .data-row {
    display: flex; justify-content: space-between; align-items: center;
    padding: 8px 14px;
    border-bottom: 1px solid #111827;
    font-size: 13px;
  }
  .data-row:last-child { border-bottom: none; }
  .data-lbl { color: #E2E8F0; font-weight: 500; }
  .val-g { color: #00FF88; font-weight: 700; font-family: 'JetBrains Mono', monospace; }
  .val-r { color: #FF6B6B; font-weight: 700; font-family: 'JetBrains Mono', monospace; }
  .val-y { color: #FACC15; font-weight: 700; font-family: 'JetBrains Mono', monospace; }
  .val-w { color: #F1F5F9; font-weight: 600; font-family: 'JetBrains Mono', monospace; }
  .val-m { color: #94A3B8; font-family: 'JetBrains Mono', monospace; }

  /* Signal grid */
  .sig-cell {
    border-radius: 8px; padding: 10px 6px; text-align: center;
  }
  .sig-bull { background: #0D2818; border: 1px solid #00FF8830; }
  .sig-bear { background: #2D1015; border: 1px solid #FF6B6B30; }
  .sig-neut { background: #251800; border: 1px solid #FACC1530; }
  .sig-label { font-size: 10px; color: #4A6080; letter-spacing: 1px; text-transform: uppercase; margin-bottom: 5px; }
  .info-link { color: #4A6080; text-decoration: none; font-size: 10px; margin-left: 4px; opacity: 0.6; }
  .info-link:hover { color: #5EEAD4; opacity: 1; }
  .sig-val-g { font-size: 13px; font-weight: 700; color: #00FF88; font-family: 'JetBrains Mono', monospace; }
  .sig-val-r { font-size: 13px; font-weight: 700; color: #FF6B6B; font-family: 'JetBrains Mono', monospace; }
  .sig-val-y { font-size: 13px; font-weight: 700; color: #FACC15; font-family: 'JetBrains Mono', monospace; }

  /* Verdict card */
  .verdict-card { border-radius: 8px; padding: 16px 18px; border-left-width: 3px; border-left-style: solid; }
  .verdict-label { font-size: 11px; letter-spacing: 2px; text-transform: uppercase; opacity: 0.8; margin-bottom: 5px; }
  .verdict-value { font-size: 28px; font-weight: 800; letter-spacing: 1px; }
  .verdict-meta { font-size: 12px; color: #94A3B8; margin-top: 4px; }
  .verdict-note { font-size: 12px; margin-top: 6px; line-height: 1.5; opacity: 0.9; }

  /* Score card */
  .score-card { background: #1C1A50; border: 1px solid #3730A3; border-left: 3px solid #818CF8; border-radius: 8px; padding: 16px 18px; }
  .score-label { font-size: 11px; color: #818CF8; letter-spacing: 2px; text-transform: uppercase; margin-bottom: 6px; }
  .score-num { font-family: 'JetBrains Mono', monospace; font-size: 52px; font-weight: 800; line-height: 1; }
  .score-denom { font-size: 20px; color: #4A6080; }

  /* Reason cells */
  .reason-bull { background: #0D2818; border-left: 2px solid #00FF88; padding: 8px 12px; font-size: 12px; color: #86EFAC; border-radius: 0 6px 6px 0; line-height: 1.5; margin-bottom: 4px; }
  .reason-bear { background: #2D1015; border-left: 2px solid #FF6B6B; padding: 8px 12px; font-size: 12px; color: #FCA5A5; border-radius: 0 6px 6px 0; line-height: 1.5; margin-bottom: 4px; }

  /* Timeframe boxes */
  .tf-day   { background: #1A1000; border: 1px solid #FACC1533; border-radius: 8px; padding: 12px 14px; }
  .tf-swing { background: #0D1525; border: 1px solid #38BDF833; border-radius: 8px; padding: 12px 14px; }
  .tf-inv   { background: #0D2010; border: 1px solid #00FF8833; border-radius: 8px; padding: 12px 14px; }
  .tf-label { font-size: 11px; font-weight: 800; letter-spacing: 1.5px; text-transform: uppercase; margin-bottom: 6px; }
  .tf-note  { font-size: 12px; color: #CBD5E1; line-height: 1.6; }

  /* Earnings bar */
  .earn-bar { background: #1A2232; border: 1px solid #243348; border-left: 3px solid #818CF8; border-radius: 8px; padding: 10px 16px; }
  .earn-label { font-size: 9px; color: #4A6080; letter-spacing: 1px; text-transform: uppercase; margin-bottom: 3px; }
  .earn-val { font-size: 13px; font-weight: 700; font-family: 'JetBrains Mono', monospace; }

  /* Summary */
  .summary-box { background: #1A2232; border-top: 2px solid #14B8A6; border-radius: 0 0 8px 8px; padding: 14px 16px; }
  .summary-text { font-size: 13px; color: #E2E8F0; line-height: 1.8; }

  /* Pattern cards */
  .pat-bull { background: #0D2818; border: 1px solid #00FF8830; border-radius: 8px; padding: 12px 14px; }
  .pat-bear { background: #2D0A0A; border: 1px solid #FF6B6B30; border-radius: 8px; padding: 12px 14px; }
  .pat-neut { background: #251800; border: 1px solid #FACC1530; border-radius: 8px; padding: 12px 14px; }
  .pat-name { font-size: 14px; font-weight: 700; margin-bottom: 4px; }
  .pat-desc { font-size: 12px; color: #CBD5E1; line-height: 1.5; margin-top: 5px; }
  .pat-target { font-size: 12px; font-weight: 600; margin-top: 6px; }

  /* Candle pattern cards */
  .candle-card { background: #1A2232; border: 1px solid #2A3F5A; border-radius: 8px; padding: 12px 14px; text-align: center; }
  .candle-card-bull { background: #0D2818; border: 1px solid #00FF8830; border-radius: 8px; padding: 12px 14px; text-align: center; }
  .candle-card-bear { background: #2D1015; border: 1px solid #FF6B6B30; border-radius: 8px; padding: 12px 14px; text-align: center; }
  .candle-card-neut { background: #251800; border: 1px solid #FACC1530; border-radius: 8px; padding: 12px 14px; text-align: center; }

  /* Trend tiles */
  .trend-tile { background: #1A2232; border: 1px solid #2A3F5A; border-radius: 8px; padding: 12px 14px; }
  .trend-tile-label { font-size: 10px; color: #94A3B8; letter-spacing: 1px; text-transform: uppercase; margin-bottom: 5px; }
  .trend-tile-val { font-size: 15px; font-weight: 800; margin-bottom: 4px; }
  .trend-tile-desc { font-size: 12px; color: #CBD5E1; line-height: 1.5; }

  /* Range bar */
  .range-wrap { position: relative; height: 6px; background: #243348; border-radius: 3px; margin: 6px 0; }
  .range-fill { position: absolute; left: 0; top: 0; height: 6px; border-radius: 3px; background: linear-gradient(90deg, #FF6B6B, #FACC15, #00FF88); }
  .range-dot { position: absolute; top: -4px; width: 12px; height: 12px; background: #F1F5F9; border-radius: 50%; transform: translateX(-50%); border: 2px solid #111827; }

  /* Input styling */
  .stTextInput input {
    background: #081510 !important;
    border: 2px solid #00FF88 !important;
    border-radius: 8px !important;
    color: #00FF88 !important;
    font-size: 22px !important;
    font-weight: 800 !important;
    font-family: 'JetBrains Mono', monospace !important;
    text-align: center !important;
    letter-spacing: 4px !important;
    text-transform: uppercase !important;
    padding: 12px !important;
  }
  .stTextInput input:focus { box-shadow: 0 0 0 3px #00FF8818 !important; }
  .stButton button {
    background: #00FF88 !important;
    color: #080E18 !important;
    border: none !important;
    border-radius: 8px !important;
    font-size: 15px !important;
    font-weight: 800 !important;
    letter-spacing: 1px !important;
    width: 100% !important;
    padding: 14px !important;
  }
  .stButton button:hover { opacity: 0.9 !important; }

  /* Footer */
  .hud-footer { text-align: center; font-size: 10px; color: #243348; padding: 12px 0; letter-spacing: 1px; }

  /* Utility value colors */
  .val-b { color: #38BDF8; font-weight: 700; font-family: 'JetBrains Mono', monospace; }

  /* Sub-panel header */
  .data-header { background: #131F32; padding: 6px 14px; font-size: 10px; color: #5EEAD4; letter-spacing: 1.5px; text-transform: uppercase; border-bottom: 1px solid #111827; }

  /* Volatility panel */
  .vol-panel { background: #1A2232; border-radius: 8px; overflow: hidden; border: 1px solid #243348; margin-bottom: 8px; }
  .vol-row { display: flex; justify-content: space-between; align-items: center; padding: 8px 14px; border-bottom: 1px solid #111827; font-size: 13px; }
  .vol-row:last-child { border-bottom: none; }
  .vol-lbl { color: #E2E8F0; font-weight: 500; }
  .vol-bar-wrap { flex: 1; margin: 0 12px; height: 5px; background: #243348; border-radius: 3px; position: relative; overflow: hidden; }
  .vol-bar-fill { height: 5px; border-radius: 3px; }

  /* Analyst ratings */
  .analyst-panel { background: #1A2232; border-radius: 8px; overflow: hidden; border: 1px solid #243348; margin-bottom: 8px; }
  .analyst-bar-wrap { display: flex; height: 8px; border-radius: 4px; overflow: hidden; margin: 6px 0; }
  .analyst-seg-buy  { background: #00FF88; }
  .analyst-seg-hold { background: #FACC15; }
  .analyst-seg-sell { background: #FF6B6B; }
  .analyst-count { font-size: 11px; font-weight: 700; }

  /* Earnings history */
  .earn-hist-row { display: grid; grid-template-columns: 1fr 1fr 1fr 1fr; gap: 5px; padding: 8px 14px; border-bottom: 1px solid #111827; font-size: 12px; }
  .earn-hist-row:last-child { border-bottom: none; }
  .earn-beat { color: #00FF88; font-weight: 700; }
  .earn-miss { color: #FF6B6B; font-weight: 700; }
  .earn-inline { color: #FACC15; font-weight: 700; }

  /* Insider trading */
  .insider-row { display: flex; justify-content: space-between; align-items: center; padding: 7px 14px; border-bottom: 1px solid #111827; font-size: 12px; }
  .insider-row:last-child { border-bottom: none; }
  .insider-buy  { color: #00FF88; font-weight: 700; font-size: 11px; }
  .insider-sell { color: #FF6B6B; font-weight: 700; font-size: 11px; }
  .insider-name { color: #E2E8F0; flex: 1; }
  .insider-role { color: #64748B; font-size: 11px; flex: 1; }
  .insider-shares { color: #94A3B8; font-size: 11px; text-align: right; }

  /* News sentiment */
  .news-row { padding: 8px 14px; border-bottom: 1px solid #111827; }
  .news-row:last-child { border-bottom: none; }
  .news-headline { font-size: 12px; color: #E2E8F0; line-height: 1.4; margin-bottom: 3px; }
  .news-meta { display: flex; justify-content: space-between; font-size: 10px; color: #4A6080; }
  .news-bull { color: #00FF88; font-weight: 700; }
  .news-bear { color: #FF6B6B; font-weight: 700; }
  .news-neut { color: #FACC15; font-weight: 700; }

  /* Score bar */
  .score-bar-wrap { position: relative; height: 8px; background: #111827; border-radius: 4px; margin: 8px 0; overflow: hidden; }
  .score-bar-track { position: absolute; top: 0; left: 0; width: 100%; height: 8px; background: linear-gradient(90deg, #FF6B6B 0%, #FACC15 45%, #00FF88 100%); opacity: 0.2; border-radius: 4px; }
  .score-bar-fill { position: absolute; top: 0; left: 0; height: 8px; background: linear-gradient(90deg, #FF6B6B 0%, #FACC15 45%, #00FF88 100%); border-radius: 4px; }
  .score-markers { display: flex; justify-content: space-between; font-size: 9px; color: #374151; font-family: 'JetBrains Mono', monospace; }

  div[data-testid="stHorizontalBlock"] { gap: 8px; }
</style>
""", unsafe_allow_html=True)


# ── Technical Indicators ──────────────────────────────────────
def calculate_indicators(df):
    close = df['Close']
    high  = df['High']
    low   = df['Low']
    vol   = df['Volume']

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

    # Volume trend
    df['VolMA20']   = vol.rolling(20).mean()
    df['VolTrend']  = vol / df['VolMA20'].replace(0, 1)

    return df.dropna(subset=['MA20','MA50','RSI','MACD'])


def calc_signals(row):
    close = row['Close']
    sigs = {
        'MA20':  {'bull': close > row['MA20'],         'label': '20 MA',  'val': 'Above' if close > row['MA20']  else 'Below'},
        'MA50':  {'bull': close > row['MA50'],         'label': '50 MA',  'val': 'Above' if close > row['MA50']  else 'Below'},
        'MA200': {'bull': close > row['MA200'],        'label': '200 MA', 'val': 'Above' if close > row['MA200'] else 'Below'},
        'RSI':   {'bull': 40 < row['RSI'] < 70,       'label': 'RSI',    'val': f"{row['RSI']:.1f}", 'neut': True},
        'MACD':  {'bull': row['MACD'] > row['MACDSig'],'label': 'MACD',  'val': 'Bullish' if row['MACD'] > row['MACDSig'] else 'Bearish'},
        'OBV':   {'bull': row['OBV'] > 0,             'label': 'OBV',    'val': 'Rising' if row['OBV'] > 0 else 'Falling'},
        'Vol':   {'bull': row['VolTrend'] > 0.8,      'label': 'Volume', 'val': f"{row['VolTrend']:.2f}x"},
        'ATR':   {'bull': row['ATRPct'] < 0.04,       'label': 'ATR',    'val': f"${row['ATR']:.2f} ({row['ATRPct']*100:.1f}%)"},
    }
    bull_count = sum(1 for k, v in sigs.items() if v['bull'])
    return sigs, round((bull_count / 8) * 10)


def fmt_vol(v):
    if v >= 1e9: return f"${v/1e9:.2f}B"
    if v >= 1e6: return f"${v/1e6:.2f}M"
    if v >= 1e3: return f"${v/1e3:.0f}K"
    return str(v)

def fmt_cap(v):
    if v >= 1e12: return f"${v/1e12:.2f}T"
    if v >= 1e9:  return f"${v/1e9:.2f}B"
    if v >= 1e6:  return f"${v/1e6:.2f}M"
    return f"${v:.0f}"

def val_color(val, good="g"):
    if val is None or val == 0: return "val-m"
    return f"val-{good}"


# ── Claude Analysis ───────────────────────────────────────────
def get_claude_analysis(ticker, info, df, signals, score, fibs):
    row    = df.iloc[-1]
    prev   = df.iloc[-2]
    close  = float(row['Close'])
    cur    = "CA$" if ticker.endswith(".TO") else "$"

    # Last 60 closes for pattern detection
    last60 = df['Close'].tail(60).round(2).tolist()
    last60_str = ",".join(str(x) for x in last60)

    # Last 5 OHLCV for candlestick patterns
    last5 = []
    for i in range(-5, 0):
        r = df.iloc[i]
        last5.append(f"O:{r['Open']:.2f} H:{r['High']:.2f} L:{r['Low']:.2f} C:{r['Close']:.2f} V:{r['Volume']:.0f}")
    last5_str = " | ".join(last5)

    ma_context = (
        f"Price vs MAs: {'ABOVE' if close > row['MA20'] else 'BELOW'} 20MA | "
        f"{'ABOVE' if close > row['MA50'] else 'BELOW'} 50MA | "
        f"{'ABOVE' if close > row['MA200'] else 'BELOW'} 200MA"
    )

    mctx = info.get('_market_ctx', {})
    prompt = f"""You are an expert stock market analyst. Analyze {ticker}.
Return ONLY raw JSON — no markdown, no backticks, no explanation.

TECHNICAL DATA:
{ma_context}
Close: {close:.2f} | Change: {close - float(prev['Close']):.2f} ({(close/float(prev['Close'])-1)*100:.2f}%)
20MA: {row['MA20']:.2f} | 50MA: {row['MA50']:.2f} | 200MA: {row['MA200']:.2f} | 100MA: {row['MA100']:.2f}
RSI: {row['RSI']:.1f} | ATR: {row['ATRPct']*100:.1f}% | Score: {score}/10
MACD: {row['MACD']:.3f} | Signal: {row['MACDSig']:.3f} | Hist: {row['MACDHist']:.3f}
OBV: {'Rising' if row['OBV'] > df.iloc[-2]['OBV'] else 'Falling'} | Vol vs avg: {row['VolTrend']:.2f}x
52W High: {info.get('fiftyTwoWeekHigh', 0):.2f} | 52W Low: {info.get('fiftyTwoWeekLow', 0):.2f}
Fib 38.2%: {fibs[0]:.2f} | 50%: {fibs[1]:.2f} | 61.8%: {fibs[2]:.2f}

LAST 60 DAILY CLOSES (oldest→newest, use for pattern detection):
{last60_str}

LAST 5 SESSIONS OHLCV (for candlestick patterns):
{last5_str}

MARKET CONTEXT (for business cycle analysis):
S&P500: {mctx.get("spy_signal","Unknown")} ({mctx.get("spy_1m",0):+.1f}% last month)
NASDAQ: {mctx.get("qqq_signal","Unknown")} ({mctx.get("qqq_1m",0):+.1f}% last month)
DOW:    {mctx.get("dia_signal","Unknown")} ({mctx.get("dia_1m",0):+.1f}% last month)
Use this to determine if we are in early/mid/late cycle and adjust conviction accordingly.

FUNDAMENTALS:
Market Cap: {fmt_cap(info.get('marketCap', 0))}
P/E: {info.get('trailingPE', 'N/A')} | Forward P/E: {info.get('forwardPE', 'N/A')}
EPS: {info.get('trailingEps', 'N/A')} | P/B: {info.get('priceToBook', 'N/A')}
Revenue Growth: {info.get('revenueGrowth', 'N/A')} | Earnings Growth: {info.get('earningsGrowth', 'N/A')}
Next Earnings: {info.get('earningsDate', 'Unknown')}
Sector: {info.get('sector', 'N/A')}

NEWS SENTIMENT INSTRUCTIONS:
- Analyze the headlines provided below and classify each as bullish, bearish, or neutral
- Consider the impact on the specific stock {ticker}, not just general market news
- Provide a one-sentence reason for each classification
- Return results in news_sentiment array

PATTERN DETECTION INSTRUCTIONS:
- Use the 60 daily closes to detect chart patterns (Cup&Handle, Bull/Bear Flag, H&S, Double Top/Bottom, Triangles, Wedges)
- Use last 5 OHLCV sessions for candlestick patterns (Hammer, Doji, Engulfing, Marubozu, etc.)
- Derive trend direction from MA positions (price vs MA20=short, vs MA50=medium, vs MA200=long)
- Only flag patterns with >40% confidence — be honest if nothing clear
- ALWAYS return trend_short/medium/long — NEVER return N/A for these
- Return at least 1-2 candlestick observations even if subtle

Return ONLY this exact JSON:
{{"verdict":"DAY TRADE|SWING TRADE|INVEST|AVOID|MULTI-TIMEFRAME",
"confidence":"Low|Medium|High",
"risk":"Low|Medium|High|Very High",
"risk_reason":"one sentence",
"entry_low":0,"entry_high":0,
"vwap":0,"ema100":0,
"support1":0,"support1_label":"e.g. 61.8% Fib 3x bounce",
"support2":0,"support2_label":"e.g. 20MA support",
"support3":0,"support3_label":"e.g. prev weekly low",
"resistance1":0,"resistance1_label":"e.g. 200MA",
"resistance2":0,"resistance2_label":"e.g. 38.2% Fib",
"resistance3":0,"resistance3_label":"e.g. recent swing high",
"reasons_bull":["r1","r2","r3"],
"reasons_bear":["r1","r2"],
"summary":"2-3 sentence plain English analysis",
"day_trade_note":"one sentence",
"swing_note":"one sentence",
"invest_note":"one sentence",
"pb_ratio":0,"peg_ratio":0,
"eps_growth_yoy":0,"rev_growth_yoy":0,
"earnings_date":"MMM DD YYYY",
"earnings_days":0,
"last_earnings_beat":"Beat by X% or Missed by X% or Unknown",
"sector":"sector name",
"chart_patterns":[{{"name":"pattern name","type":"bullish|bearish|neutral","confidence":72,"description":"one sentence describing what you see in the 60-day price series","target_pct":12,"target_price":0}}],
"candle_patterns":[{{"name":"pattern name","type":"bullish|bearish|neutral","session":"Today|Yesterday|2d ago|3d ago|4d ago","meaning":"one sentence plain English"}}],
"trend_short":"Uptrend|Downtrend|Sideways","trend_short_desc":"one sentence based on price vs 20MA",
"trend_medium":"Uptrend|Downtrend|Sideways","trend_medium_desc":"one sentence based on price vs 50MA",
"trend_long":"Uptrend|Downtrend|Sideways","trend_long_desc":"one sentence based on price vs 200MA",
"pattern_bias":"Bullish|Bearish|Neutral","pattern_bias_desc":"one sentence overall",
"cycle_phase":"Early|Mid|Late|Recession",
"cycle_desc":"one sentence on where we are in business cycle and impact on this stock",
"market_risk":"Low|Moderate|High|Extreme",
"market_risk_desc":"one sentence on macro/market risk for this position","news_sentiment":[{"headline":"title","sentiment":"bullish|bearish|neutral","reason":"one sentence"}]}}"""

    try:
        client = anthropic.Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2500,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = msg.content[0].text.strip()
        # Strip any accidental markdown
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())
    except Exception as e:
        return {"error": str(e)}


# ── Chart ─────────────────────────────────────────────────────
def build_chart(df, ticker):
    fig = make_subplots(
        rows=3, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.6, 0.2, 0.2]
    )

    # Candlesticks
    fig.add_trace(go.Candlestick(
        x=df.index,
        open=df['Open'].values,
        high=df['High'].values,
        low=df['Low'].values,
        close=df['Close'].values,
        increasing_line_color='#00FF88',
        decreasing_line_color='#FF6B6B',
        increasing_fillcolor='#00FF88',
        decreasing_fillcolor='#FF6B6B',
        name=ticker, line_width=1
    ), row=1, col=1)

    # MAs
    for ma, color, width in [('MA20','#38BDF8',1.5),('MA50','#F59E0B',1.5),('MA200','#FF6B6B',1.5),('MA100','#A78BFA',1)]:
        if ma in df.columns:
            fig.add_trace(go.Scatter(
                x=df.index, y=df[ma].values,
                name=ma, line=dict(color=color, width=width), opacity=0.85
            ), row=1, col=1)

    # Volume — simple bar, no per-bar colors to avoid ValueError
    fig.add_trace(go.Bar(
        x=df.index,
        y=df['Volume'].values,
        name='Volume',
        marker=dict(color='rgba(56,189,248,0.35)'),
        showlegend=False
    ), row=2, col=1)

    # MACD line + signal
    fig.add_trace(go.Scatter(
        x=df.index, y=df['MACD'].values,
        name='MACD', line=dict(color='#38BDF8', width=1.2)
    ), row=3, col=1)
    fig.add_trace(go.Scatter(
        x=df.index, y=df['MACDSig'].values,
        name='Signal', line=dict(color='#F59E0B', width=1.2)
    ), row=3, col=1)

    # MACD histogram — green/red based on value
    hist_vals = df['MACDHist'].values
    fig.add_trace(go.Bar(
        x=df.index,
        y=hist_vals,
        name='Hist',
        marker=dict(color=['rgba(0,255,136,0.5)' if v >= 0 else 'rgba(255,107,107,0.5)' for v in hist_vals]),
        showlegend=False
    ), row=3, col=1)

    fig.update_layout(
        height=540,
        paper_bgcolor='#0E1828',
        plot_bgcolor='#0E1828',
        font=dict(color='#94A3B8', family='JetBrains Mono', size=11),
        xaxis_rangeslider_visible=False,
        legend=dict(bgcolor='#0E1828', bordercolor='#243348',
                    borderwidth=1, font=dict(size=10), orientation='h', y=1.02),
        margin=dict(l=50, r=20, t=10, b=10),
        hovermode='x unified'
    )
    for i in range(1, 4):
        fig.update_xaxes(showgrid=True, gridcolor='#1A2232', gridwidth=1, row=i, col=1)
        fig.update_yaxes(showgrid=True, gridcolor='#1A2232', gridwidth=1, row=i, col=1)

    fig.update_yaxes(title_text='Price', row=1, col=1)
    fig.update_yaxes(title_text='Vol',   row=2, col=1)
    fig.update_yaxes(title_text='MACD',  row=3, col=1)

    return fig


# ── Render helpers ────────────────────────────────────────────
# Multi-listed stocks disambiguation
MULTI_LISTED = {
    'TSM':  [{'ticker':'TSM',     'name':'Taiwan Semiconductor (US ADR)', 'exchange':'NYSE',   'currency':'USD'},],
    'TSMC': [{'ticker':'TSM',     'name':'Taiwan Semiconductor (US ADR)', 'exchange':'NYSE',   'currency':'USD'},],
    'RY':   [{'ticker':'RY',      'name':'Royal Bank of Canada (US)',      'exchange':'NYSE',   'currency':'USD'},
             {'ticker':'RY.TO',   'name':'Royal Bank of Canada (TSX)',     'exchange':'TSX',    'currency':'CAD'}],
    'TD':   [{'ticker':'TD',      'name':'TD Bank (US)',                   'exchange':'NYSE',   'currency':'USD'},
             {'ticker':'TD.TO',   'name':'TD Bank (TSX)',                  'exchange':'TSX',    'currency':'CAD'}],
    'SHOP': [{'ticker':'SHOP',    'name':'Shopify (NYSE)',                 'exchange':'NYSE',   'currency':'USD'},
             {'ticker':'SHOP.TO', 'name':'Shopify (TSX)',                  'exchange':'TSX',    'currency':'CAD'}],
    'BRK':  [{'ticker':'BRK-B',   'name':'Berkshire Hathaway Class B',    'exchange':'NYSE',   'currency':'USD'},
             {'ticker':'BRK-A',   'name':'Berkshire Hathaway Class A',    'exchange':'NYSE',   'currency':'USD'}],
    'BABA': [{'ticker':'BABA',    'name':'Alibaba Group (US ADR)',         'exchange':'NYSE',   'currency':'USD'},],
    'CNR':  [{'ticker':'CNI',     'name':'Canadian National Railway (US)','exchange':'NYSE',   'currency':'USD'},
             {'ticker':'CNR.TO',  'name':'Canadian National Railway (TSX)','exchange':'TSX',   'currency':'CAD'}],
    'AC':   [{'ticker':'AC.TO',   'name':'Air Canada (TSX)',               'exchange':'TSX',    'currency':'CAD'}],
    'SU':   [{'ticker':'SU',      'name':'Suncor Energy (US)',             'exchange':'NYSE',   'currency':'USD'},
             {'ticker':'SU.TO',   'name':'Suncor Energy (TSX)',            'exchange':'TSX',    'currency':'CAD'}],
    'ENB':  [{'ticker':'ENB',     'name':'Enbridge (US)',                  'exchange':'NYSE',   'currency':'USD'},
             {'ticker':'ENB.TO',  'name':'Enbridge (TSX)',                 'exchange':'TSX',    'currency':'CAD'}],
}

# Investopedia links for signals and ratios
INFO_LINKS = {
    "20 MA":   "https://www.investopedia.com/terms/m/movingaverage.asp",
    "50 MA":   "https://www.investopedia.com/terms/m/movingaverage.asp",
    "200 MA":  "https://www.investopedia.com/terms/m/movingaverage.asp",
    "RSI":     "https://www.investopedia.com/terms/r/rsi.asp",
    "MACD":    "https://www.investopedia.com/terms/m/macd.asp",
    "OBV":     "https://www.investopedia.com/terms/o/onbalancevolume.asp",
    "Volume":  "https://www.investopedia.com/terms/v/volume.asp",
    "ATR%":    "https://www.investopedia.com/terms/a/atr.asp",
    "ATR (14)": "https://www.investopedia.com/terms/a/atr.asp",
    "ATR":      "https://www.investopedia.com/terms/a/atr.asp",
    "P/E Ratio":    "https://www.investopedia.com/terms/p/price-earningsratio.asp",
    "P/B Ratio":    "https://www.investopedia.com/terms/p/price-to-bookratio.asp",
    "PEG Ratio":    "https://www.investopedia.com/terms/p/pegratio.asp",
    "EPS Growth YoY": "https://www.investopedia.com/terms/e/eps.asp",
    "Rev Growth YoY": "https://www.investopedia.com/terms/r/revenuerecognition.asp",
    "RSI (14)":  "https://www.investopedia.com/terms/r/rsi.asp",
    "MACD Hist": "https://www.investopedia.com/terms/m/macd.asp",
    "VWAP":      "https://www.investopedia.com/terms/v/vwap.asp",
    "100 EMA":   "https://www.investopedia.com/terms/e/ema.asp",
    "52W Range": "https://www.investopedia.com/terms/1/52-week-range.asp",
    "38.2% Fib": "https://www.investopedia.com/terms/f/fibonaccilevels.asp",
    "50.0% Fib": "https://www.investopedia.com/terms/f/fibonaccilevels.asp",
    "61.8% Fib": "https://www.investopedia.com/terms/f/fibonaccilevels.asp",
    "Market Cap":       "https://www.investopedia.com/terms/m/marketcapitalization.asp",
    "P/E (Trailing)":   "https://www.investopedia.com/terms/p/price-earningsratio.asp",
    "P/E (Forward)":    "https://www.investopedia.com/terms/p/price-earningsratio.asp",
    "Operating Margin": "https://www.investopedia.com/terms/o/operatingmargin.asp",
    "Profit Margin":    "https://www.investopedia.com/terms/p/profitmargin.asp",
    "Return on Equity": "https://www.investopedia.com/terms/r/returnonequity.asp",
    "Debt / Equity":    "https://www.investopedia.com/terms/d/debtequityratio.asp",
    "Current Ratio":    "https://www.investopedia.com/terms/c/currentratio.asp",
    "Dividend Yield":   "https://www.investopedia.com/terms/d/dividendyield.asp",
    "Short % Float":    "https://www.investopedia.com/terms/s/shortinterest.asp",
    "Float Shares":     "https://www.investopedia.com/terms/f/floating-stock.asp",
    "OBV":       "https://www.investopedia.com/terms/o/onbalancevolume.asp",
}

def info_icon(label):
    url = INFO_LINKS.get(label, "https://www.investopedia.com/search#q=" + label.replace(" ","+"))
    return f'<a href="{url}" target="_blank" class="info-link" title="Learn about {label} on Investopedia">ⓘ</a>'

def sig_html(label, val, bull, neut=False):
    cls = "sig-neut" if neut else ("sig-bull" if bull else "sig-bear")
    vcls = "sig-val-y" if neut else ("sig-val-g" if bull else "sig-val-r")
    prefix = "~ " if neut else ("+ " if bull else "− ")
    return f'''<div class="{cls}">
      <div class="sig-label">{label}{info_icon(label)}</div>
      <div class="{vcls}">{prefix}{val}</div>
    </div>'''

def data_row(label, val, cls="val-w", show_info=False):
    icon = info_icon(label) if show_info and label in INFO_LINKS else ""
    return f'<div class="data-row"><span class="data-lbl">{label}{icon}</span><span class="{cls}">{val}</span></div>'

def range_bar_html(low, high, current, cur):
    if high <= low: return ""
    pct = max(0, min(100, int((current - low) / (high - low) * 100)))
    return f'''
    <div class="data-row" style="flex-direction:column;gap:6px;">
      <div style="display:flex;justify-content:space-between;width:100%;font-size:13px;">
        <span class="data-lbl">52W Range</span>
        <span class="val-m" style="font-size:11px;">{cur}{low:.2f} → {cur}{high:.2f}</span>
      </div>
      <div style="display:flex;align-items:center;gap:8px;width:100%;">
        <span style="font-size:11px;color:#FF6B6B;">{cur}{low:.0f}</span>
        <div class="range-wrap" style="flex:1;position:relative;height:6px;background:#243348;border-radius:3px;">
          <div class="range-fill" style="width:{pct}%;position:absolute;top:0;left:0;height:6px;border-radius:3px;background:linear-gradient(90deg,#FF6B6B,#FACC15,#00FF88);"></div>
          <div class="range-dot" style="left:{pct}%;position:absolute;top:-4px;width:12px;height:12px;background:#F1F5F9;border-radius:50%;transform:translateX(-50%);border:2px solid #111827;"></div>
        </div>
        <span style="font-size:11px;color:#00FF88;">{cur}{high:.0f}</span>
      </div>
      <div style="text-align:center;font-size:11px;color:#94A3B8;">{cur}{current:.2f} — {pct}% of 52W range</div>
    </div>'''


# ── Main App ──────────────────────────────────────────────────
def main():
    # Input screen
    if 'analysis' not in st.session_state:
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            st.markdown("<br><br>", unsafe_allow_html=True)
            st.markdown('<div style="text-align:center;font-size:12px;color:#4A6080;letter-spacing:3px;text-transform:uppercase;margin-bottom:24px;">Stock Analysis · AI HUD</div>', unsafe_allow_html=True)
            st.markdown('<div style="text-align:center;font-size:24px;font-weight:800;color:#F1F5F9;margin-bottom:6px;">Enter a ticker</div>', unsafe_allow_html=True)
            st.markdown('<div style="text-align:center;font-size:13px;color:#4A6080;margin-bottom:20px;">Type any stock symbol and press Analyze</div>', unsafe_allow_html=True)
            ticker_in = st.text_input("", placeholder="NVDA", key="ticker_input", label_visibility="collapsed")
            ticker_upper = ticker_in.strip().upper() if ticker_in else ""

            # Check disambiguation
            if ticker_upper in MULTI_LISTED and len(MULTI_LISTED[ticker_upper]) > 1:
                st.markdown('<div style="background:#0F3030;border:1px solid #14B8A6;border-radius:8px;padding:8px 14px;margin-top:8px;font-size:11px;color:#5EEAD4;letter-spacing:1px;">MULTIPLE LISTINGS FOUND — SELECT ONE:</div>', unsafe_allow_html=True)
                for opt in MULTI_LISTED[ticker_upper]:
                    col_a, col_b, col_c = st.columns([2,3,1])
                    with col_a:
                        st.markdown(f'<span style="font-family:monospace;font-weight:800;color:#00FF88;font-size:14px;">{opt["ticker"]}</span>', unsafe_allow_html=True)
                    with col_b:
                        st.markdown(f'<span style="font-size:12px;color:#E2E8F0;">{opt["name"]}</span>', unsafe_allow_html=True)
                    with col_c:
                        if st.button(f'{opt["exchange"]}', key=f'btn_{opt["ticker"]}'):
                            run_analysis(opt["ticker"])
            elif st.button("Analyze →"):
                if ticker_upper:
                    # Normalize for yfinance
                    t = MULTI_LISTED.get(ticker_upper, [{'ticker': ticker_upper}])[0]['ticker']
                    run_analysis(t)
            st.markdown('<div style="text-align:center;font-size:11px;color:#243348;margin-top:16px;">US: AAPL · NVDA · PLTR &nbsp;|&nbsp; TSX: add .TO (RY.TO) &nbsp;|&nbsp; London: add .L</div>', unsafe_allow_html=True)
        return

    # HUD screen
    render_hud()


def run_analysis(ticker):
    prog = st.empty()
    try:
        prog.info(f"⏳ Fetching price data for {ticker}...")

        # Normalize ticker for yfinance
        yf_ticker = ticker.replace('BRK.B','BRK-B').replace('BRK.A','BRK-A')
        raw = yf.Ticker(yf_ticker)
        df  = raw.history(period="2y")
        if df.empty or len(df) < 50:
            prog.empty()
            st.error(f"No data found for {ticker}. Check the ticker symbol.")
            return

        df = calculate_indicators(df)
        if len(df) < 20:
            prog.empty()
            st.error("Not enough data to calculate indicators.")
            return

        prog.info(f"⏳ Fetching fundamentals for {ticker}...")
        try:
            info = raw.info or {}
        except:
            info = {}

        row  = df.iloc[-1]
        prev = df.iloc[-2]
        signals, score = calc_signals(row)

        # Fibonacci
        h52 = float(info.get('fiftyTwoWeekHigh', df['High'].tail(252).max()))
        l52 = float(info.get('fiftyTwoWeekLow',  df['Low'].tail(252).min()))
        rng = h52 - l52
        fibs = [h52 - rng*0.382, h52 - rng*0.500, h52 - rng*0.618]

        # ── Safe defaults ──────────────────────────────────────
        analyst_data  = {'buy':0,'hold':0,'sell':0,'target':0,'target_low':0,'target_high':0,'num_analysts':0,'rec_mean':0,'rec_key':'N/A'}
        earnings_hist = []
        insider_data  = []
        news_items    = []
        vol_data      = {'hv_30':0,'hv_90':0,'bb_upper':0,'bb_lower':0,'bb_mid':0,'bb_width':0,'bb_pct':50,'iv':0,'iv_vs_hv':0}

        # ── Market context — batched ───────────────────────────
        prog.info("⏳ Fetching market context (SPY/QQQ/DIA)...")
        try:
            idx_batch = yf.download(
                ["SPY","QQQ","DIA"], period="3mo",
                auto_adjust=True, progress=False, threads=True
            )
            def idx_sig(sym):
                try:
                    s = idx_batch["Close"][sym].dropna() if ("Close",sym) in idx_batch.columns else idx_batch["Close"].dropna()
                    if len(s)<20: return "Unknown", 0
                    c=float(s.iloc[-1]); m20=float(s.tail(20).mean())
                    m50=float(s.tail(50).mean()) if len(s)>=50 else m20
                    sig = "Bullish" if c>m20 and c>m50 else "Bearish" if c<m20 and c<m50 else "Neutral"
                    return sig, round((c/float(s.iloc[-20])-1)*100,2)
                except: return "Unknown", 0
            spy_sig,spy_chg = idx_sig("SPY")
            qqq_sig,qqq_chg = idx_sig("QQQ")
            dia_sig,dia_chg = idx_sig("DIA")
            market_ctx = {"spy_signal":spy_sig,"qqq_signal":qqq_sig,"dia_signal":dia_sig,
                          "spy_1m":spy_chg,"qqq_1m":qqq_chg,"dia_1m":dia_chg}
        except:
            market_ctx = {"spy_signal":"Unknown","qqq_signal":"Unknown","dia_signal":"Unknown",
                          "spy_1m":0,"qqq_1m":0,"dia_1m":0}
        st.session_state.market_ctx = market_ctx

        # ── News (before Claude so it can analyze them) ────────
        prog.info(f"⏳ Fetching news for {ticker}...")
        try:
            news_raw = raw.news or []
            for item in (news_raw or [])[:5]:
                try:
                    title = str(item.get('title','') or item.get('content',{}).get('title',''))
                    pub   = str(item.get('publisher','') or item.get('content',{}).get('provider',{}).get('displayName',''))
                    link  = str(item.get('link','') or item.get('content',{}).get('canonicalUrl',{}).get('url',''))
                    if title: news_items.append({'title':title,'publisher':pub,'link':link})
                except: pass
        except: pass

        # ── Claude AI analysis ─────────────────────────────────
        prog.info(f"🤖 Running AI analysis... (10-15 sec)")
        info['_market_ctx'] = market_ctx
        info['_news']       = news_items
        analysis = get_claude_analysis(ticker, info, df, signals, score, fibs)
        if 'error' in analysis:
            prog.empty()
            st.error(f"Claude API error: {analysis['error']}")
            return

        # ── Analyst ratings ────────────────────────────────────
        prog.info("⏳ Fetching analyst & earnings data...")
        try:
            rec = raw.recommendations_summary
            if rec is not None and not rec.empty:
                latest = rec.iloc[0]
                analyst_data = {
                    'buy':       int(latest.get('strongBuy',0) + latest.get('buy',0)),
                    'hold':      int(latest.get('hold',0)),
                    'sell':      int(latest.get('sell',0) + latest.get('strongSell',0)),
                    'target':    float(info.get('targetMeanPrice',0) or 0),
                    'target_low':float(info.get('targetLowPrice',0) or 0),
                    'target_high':float(info.get('targetHighPrice',0) or 0),
                    'num_analysts':int(info.get('numberOfAnalystOpinions',0) or 0),
                    'rec_mean':  float(info.get('recommendationMean',0) or 0),
                    'rec_key':   str(info.get('recommendationKey','N/A') or 'N/A'),
                }
        except: pass

        # ── Earnings history ───────────────────────────────────
        try:
            eh = raw.earnings_history
            if eh is not None and not eh.empty:
                for _, row_e in eh.tail(4).iterrows():
                    est  = float(row_e.get('epsEstimate',0) or 0)
                    act  = float(row_e.get('epsActual',0) or 0)
                    surp = float(row_e.get('surprisePercent',0) or 0) * 100
                    qtr  = str(row_e.get('period',''))
                    earnings_hist.append({'quarter':qtr,'estimate':est,'actual':act,'surprise':surp,'beat':surp>0})
        except: pass

        # ── Insider trading ────────────────────────────────────
        try:
            ins = raw.insider_transactions
            if ins is not None and not ins.empty:
                for _, row_i in ins.head(5).iterrows():
                    shares = int(row_i.get('shares',0) or 0)
                    val    = float(row_i.get('value',0) or 0)
                    text   = str(row_i.get('text','') or '')
                    name   = str(row_i.get('filerName','') or row_i.get('insider',''))
                    role   = str(row_i.get('filerRelation','') or '')
                    date_i = str(row_i.get('startDate','') or '')
                    is_buy = 'purchase' in text.lower() or 'buy' in text.lower() or shares > 0
                    insider_data.append({'name':name[:20],'role':role[:20],'type':'BUY' if is_buy else 'SELL',
                                         'shares':abs(shares),'value':abs(val),'date':str(date_i)[:10]})
        except: pass

        # ── Volatility metrics ─────────────────────────────────
        try:
            log_returns = np.log(df['Close'] / df['Close'].shift(1)).dropna()
            hv_30 = float(log_returns.tail(30).std() * np.sqrt(252) * 100)
            hv_90 = float(log_returns.tail(90).std() * np.sqrt(252) * 100) if len(log_returns)>=90 else hv_30
            bb_mid   = float(df['Close'].tail(20).mean())
            bb_std   = float(df['Close'].tail(20).std())
            bb_upper = bb_mid + 2*bb_std
            bb_lower = bb_mid - 2*bb_std
            bb_width = (bb_upper - bb_lower) / bb_mid * 100
            iv       = float(info.get('impliedVolatility',0) or 0) * 100
            close_now= float(df['Close'].iloc[-1])
            bb_pct   = (close_now - bb_lower)/(bb_upper - bb_lower)*100 if bb_upper != bb_lower else 50
            vol_data = {'hv_30':hv_30,'hv_90':hv_90,'bb_upper':bb_upper,'bb_lower':bb_lower,
                        'bb_mid':bb_mid,'bb_width':bb_width,'bb_pct':bb_pct,'iv':iv,
                        'iv_vs_hv':iv/hv_30 if hv_30>0 else 0}
        except: pass

        # ── Earnings date from calendar ────────────────────────
        earn_date_str = analysis.get('earnings_date', 'Unknown') or 'Unknown'
        days_to_earn  = 0
        try:
            cal = raw.calendar
            if cal is not None and not cal.empty:
                next_earn = cal.iloc[0].get('Earnings Date', None)
                if next_earn:
                    next_earn_dt = pd.Timestamp(next_earn).tz_localize(None) if hasattr(next_earn,'tzinfo') and next_earn.tzinfo is None else pd.Timestamp(next_earn).tz_convert(None)
                    days_to_earn = (next_earn_dt - pd.Timestamp.now()).days
                    earn_date_str = next_earn_dt.strftime("%b %d, %Y")
                else:
                    days_to_earn = 0; earn_date_str = analysis.get('earnings_date','Unknown') or 'Unknown'
            else:
                days_to_earn = 0; earn_date_str = analysis.get('earnings_date','Unknown') or 'Unknown'
        except:
            days_to_earn = 0; earn_date_str = analysis.get('earnings_date','Unknown') or 'Unknown'

        # ── Store in session ───────────────────────────────────
        prog.empty()
        st.session_state.analysis      = analysis
        st.session_state.analyst_data  = analyst_data
        st.session_state.earnings_hist = earnings_hist
        st.session_state.insider_data  = insider_data
        st.session_state.news_items    = news_items
        st.session_state.vol_data      = vol_data
        st.session_state.earn_date_str = earn_date_str
        st.session_state.days_to_earn  = days_to_earn
        st.session_state.df            = df
        st.session_state.info          = info
        st.session_state.ticker        = ticker
        st.session_state.signals       = signals
        st.session_state.score         = score
        st.session_state.fibs          = fibs
        st.session_state.row           = row
        st.session_state.prev          = prev
        st.rerun()

    except Exception as e:
        prog.empty()
        st.error(f"Error: {str(e)}")


def render_hud():
    a            = st.session_state.analysis
    df           = st.session_state.df
    info         = st.session_state.info
    ticker       = st.session_state.ticker
    signals      = st.session_state.signals
    score        = st.session_state.score
    fibs         = st.session_state.fibs
    row          = st.session_state.row
    prev         = st.session_state.prev
    analyst_data = st.session_state.get('analyst_data', {})
    earnings_hist = st.session_state.get('earnings_hist', [])
    insider_data = st.session_state.get('insider_data', [])
    news_items   = st.session_state.get('news_items', [])
    vol_data      = st.session_state.get('vol_data', {})
    earn_date_str = st.session_state.get('earn_date_str', 'Unknown')
    days_to_earn  = st.session_state.get('days_to_earn', 0)

    close    = float(row['Close'])
    prev_c   = float(prev['Close'])
    chg      = close - prev_c
    chg_pct  = (chg / prev_c) * 100 if prev_c else 0
    cur      = "CA$" if ticker.endswith(".TO") else "$"
    sign     = "+" if chg >= 0 else ""
    vc       = VERDICT_COLORS.get(a.get('verdict','SWING TRADE'), VERDICT_COLORS['SWING TRADE'])
    score_col= "#00FF88" if score >= 7 else "#FACC15" if score >= 4 else "#FF6B6B"

    h52  = float(info.get('fiftyTwoWeekHigh', df['High'].tail(252).max()))
    l52  = float(info.get('fiftyTwoWeekLow',  df['Low'].tail(252).min()))
    vol  = float(row['Volume'])
    atr_pct = float(row['ATRPct'])

    company = info.get('longName', info.get('shortName', ticker))
    sector  = info.get('sector', a.get('sector',''))
    exchange = 'TSX' if ticker.endswith('.TO') else 'LSE' if ticker.endswith('.L') else 'NYSE / NASDAQ'

    # Back button
    if st.button("← New ticker"):
        for k in ['analysis','df','info','ticker','signals','score','fibs','row','prev']:
            if k in st.session_state: del st.session_state[k]
        st.rerun()

    # ── ZONE 1: IDENTITY ─────────────────────────────────────
    chg_badge = f'<span class="price-change-up">▲ {sign}{chg:.2f} ({sign}{chg_pct:.2f}%)</span>' if chg >= 0 else \
                f'<span class="price-change-dn">▼ {chg:.2f} ({chg_pct:.2f}%)</span>'
    st.markdown(f'''
    <div class="identity-bar" style="border-top:3px solid {vc["border"]};">
      <div style="display:flex;align-items:center;gap:18px;">
        <div class="ticker-name">{ticker}</div>
        <div>
          <div class="company-name">{company}</div>
          <div style="margin-top:4px;">
            <span class="exchange-pill">{exchange}</span>
            <span style="font-size:11px;color:#4B5563;margin-left:8px;">{sector}</span>
          </div>
        </div>
      </div>
      <div style="text-align:right;">
        <div class="price-display">{cur}{close:.2f}</div>
        <div style="text-align:right;margin-top:6px;">{chg_badge}</div>
      </div>
    </div>''', unsafe_allow_html=True)

    # ── ZONE 2: STATUS BAR ───────────────────────────────────
    # Detect user timezone via JS — works globally for any visitor
    try:
        import streamlit.components.v1 as components
        tz_key = "user_timezone"
        if tz_key not in st.session_state:
            st.session_state[tz_key] = "UTC"
        # Inject JS to capture browser timezone and store via query param
        components.html("""
            <script>
            const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
            const url = new URL(window.parent.location.href);
            if (!url.searchParams.get('tz')) {
                url.searchParams.set('tz', tz);
                window.parent.history.replaceState({}, '', url.toString());
            }
            </script>
        """, height=0)
        # Read timezone from query params if available
        params = st.query_params
        user_tz = params.get("tz", "UTC")
        import zoneinfo
        try:
            tz_obj   = zoneinfo.ZoneInfo(user_tz)
            analyzed = datetime.now(tz_obj).strftime("%b %d · %I:%M %p")
        except:
            analyzed = datetime.now().strftime("%b %d · %I:%M %p")
    except:
        analyzed = datetime.now().strftime("%b %d · %I:%M %p")
    st.markdown(f'''
    <div class="status-bar">
      O&nbsp;<span>{row['Open']:.2f}</span>&nbsp;&nbsp;
      H&nbsp;<span>{row['High']:.2f}</span>&nbsp;&nbsp;
      L&nbsp;<span>{row['Low']:.2f}</span>&nbsp;&nbsp;
      VOL&nbsp;<span>{fmt_vol(vol)}</span>&nbsp;&nbsp;
      AVG&nbsp;<span>{row['VolTrend']:.2f}x</span>&nbsp;&nbsp;
      ATR&nbsp;<span>{cur}{float(row["ATR"]):.2f}&nbsp;({atr_pct*100:.1f}%)</span>&nbsp;&nbsp;
      <span style="float:right;color:#5EEAD4;">{analyzed}</span>
    </div>''', unsafe_allow_html=True)

    # ── ZONE 3: VERDICT + SCORE ──────────────────────────────
    bull_count = sum(1 for k,v in signals.items() if v['bull'])
    c1, c2 = st.columns([1.5, 0.7])
    with c1:
        st.markdown(f'''
        <div class="verdict-card" style="background:{vc['bg']};border-left-color:{vc['border']};">
          <div class="verdict-label" style="color:{vc['color']};">AI Verdict</div>
          <div class="verdict-value" style="color:{vc['color']};">{a.get('verdict','')}</div>
          <div class="verdict-meta">Confidence: {a.get('confidence','')} &nbsp;·&nbsp; Risk: {a.get('risk','')}</div>
          <div class="verdict-note" style="color:{vc['color']};">{a.get('risk_reason','')}</div>
        </div>''', unsafe_allow_html=True)
    with c2:
        st.markdown(f'''
        <div class="score-card">
          <div class="score-label">Signal Score</div>
          <div><span class="score-num" style="color:{score_col};">{score}</span><span class="score-denom">/10</span></div>
          <div class="score-bar-wrap">
            <div class="score-bar-track"></div>
            <div class="score-bar-fill" style="width:{score*10}%;"></div>
          </div>
          <div class="score-markers"><span>AVOID</span><span>NEUTRAL</span><span>STRONG</span></div>
          <div style="font-size:11px;color:#6B7280;margin-top:6px;">{bull_count} of 8 signals bullish</div>
        </div>''', unsafe_allow_html=True)

    # ── ZONE 3b: AI SUMMARY ─────────────────────────────────
    st.markdown('<div class="section-header" style="border-radius:8px 8px 0 0;margin-top:8px;">AI Summary</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="summary-box"><div class="summary-text">{a.get("summary","")}</div></div>', unsafe_allow_html=True)

    # ── ZONE 4: SIGNAL GRID ──────────────────────────────────
    sig_keys = ['MA20','MA50','MA200','RSI','MACD','OBV','Vol','ATR']
    cols = st.columns(8)
    for i, k in enumerate(sig_keys):
        s = signals[k]
        with cols[i]:
            st.markdown(sig_html(s['label'], s['val'], s['bull'], s.get('neut', False)), unsafe_allow_html=True)

    # ── ZONE 5: KEY LEVELS + FUNDAMENTALS ───────────────────
    vwap    = float(a.get('vwap', close))
    ema100  = float(a.get('ema100', float(row['MA100'])))
    fib382, fib500, fib618 = fibs

    def lc(val, is_support=True):
        if not val: return "val-m"
        return "val-g" if (is_support and close > val) or (not is_support and close < val) else "val-r"

    c1, c2 = st.columns(2)
    with c1:
        st.markdown('<div class="section-header">Key Levels & Technical Indicators</div>', unsafe_allow_html=True)
        levels_html = '<div class="panel-body">'
        atr_dollar = float(row['ATR'])
        atr_low    = round(close - atr_dollar, 2)
        atr_high   = round(close + atr_dollar, 2)
        levels_html += data_row("Entry zone", f"{cur}{a.get('entry_low',0):.2f} – {cur}{a.get('entry_high',0):.2f}", "val-y")
        levels_html += data_row("ATR (14)",   f"{cur}{atr_dollar:.2f}  →  expected range {cur}{atr_low:.2f} – {cur}{atr_high:.2f}", "val-b", True)
        levels_html += data_row("VWAP",    f"{cur}{vwap:.2f}",   "val-g" if close > vwap  else "val-r")
        levels_html += data_row("100 EMA", f"{cur}{ema100:.2f}", "val-g" if close > ema100 else "val-r")
        levels_html += data_row("38.2% Fib", f"{cur}{fib382:.2f}", "val-m", show_info=True)
        levels_html += data_row("50.0% Fib", f"{cur}{fib500:.2f}", "val-m", show_info=True)
        levels_html += data_row("61.8% Fib", f"{cur}{fib618:.2f}", "val-m", show_info=True)
        levels_html += data_row(a.get('support1_label','Support 1'),    f"{cur}{a.get('support1',0):.2f}",    "val-g")
        levels_html += data_row(a.get('resistance1_label','Resistance 1'), f"{cur}{a.get('resistance1',0):.2f}", "val-r")
        levels_html += data_row(a.get('support2_label','Support 2'),    f"{cur}{a.get('support2',0):.2f}",    "val-g")
        levels_html += data_row(a.get('resistance2_label','Resistance 2'), f"{cur}{a.get('resistance2',0):.2f}", "val-r")
        levels_html += range_bar_html(l52, h52, close, cur)
        levels_html += '</div>'
        st.markdown(levels_html, unsafe_allow_html=True)

    with c2:
        st.markdown('<div class="section-header">Fundamentals & Growth</div>', unsafe_allow_html=True)
        pe  = info.get('trailingPE', 0) or 0
        pb  = info.get('priceToBook', a.get('pb_ratio', 0)) or 0
        peg = info.get('pegRatio', a.get('peg_ratio', 0)) or 0
        eps_g = (info.get('earningsGrowth', 0) or 0) * 100
        rev_g = (info.get('revenueGrowth', 0) or 0) * 100
        mc  = info.get('marketCap', 0) or 0
        ma20_pct = (close/float(row['MA20'])-1)*100
        ma50_pct = (close/float(row['MA50'])-1)*100
        ma200_pct= (close/float(row['MA200'])-1)*100

        # Additional fundamentals from yfinance
        div_yield  = (info.get('dividendYield', 0) or 0) * 100
        fwd_pe     = info.get('forwardPE', 0) or 0
        op_margin  = (info.get('operatingMargins', 0) or 0) * 100
        profit_m   = (info.get('profitMargins', 0) or 0) * 100
        rd_expense = info.get('researchAndDevelopment', info.get('totalRevenue', 0)) or 0
        float_sh   = info.get('floatShares', 0) or 0
        short_pct  = (info.get('shortPercentOfFloat', 0) or 0) * 100
        roe        = (info.get('returnOnEquity', 0) or 0) * 100
        debt_eq    = info.get('debtToEquity', 0) or 0
        curr_ratio = info.get('currentRatio', 0) or 0

        funds_html = '<div class="panel-body">'
        funds_html += data_row("Market Cap",       fmt_cap(mc) if mc else "N/A",                   "val-w",  True)
        funds_html += data_row("P/E (Trailing)",   f"{pe:.1f}" if pe else "N/A",                   "val-r" if pe > 40 else "val-y" if pe > 20 else "val-g", True)
        funds_html += data_row("P/E (Forward)",    f"{fwd_pe:.1f}" if fwd_pe else "N/A",           "val-r" if fwd_pe > 35 else "val-y" if fwd_pe > 18 else "val-g", True)
        funds_html += data_row("P/B Ratio",        f"{pb:.1f}" if pb else "N/A",                   "val-r" if pb > 5 else "val-g", True)
        funds_html += data_row("PEG Ratio",        f"{peg:.2f}" if peg else "N/A",                 "val-r" if peg > 3 else "val-y" if peg > 1.5 else "val-g", True)
        funds_html += data_row("EPS Growth YoY",   f"{eps_g:+.1f}%" if eps_g else "N/A",           "val-g" if eps_g > 0 else "val-r", True)
        funds_html += data_row("Rev Growth YoY",   f"{rev_g:+.1f}%" if rev_g else "N/A",           "val-g" if rev_g > 0 else "val-r", True)
        funds_html += data_row("Operating Margin", f"{op_margin:.1f}%" if op_margin else "N/A",    "val-g" if op_margin > 15 else "val-y" if op_margin > 0 else "val-r", True)
        funds_html += data_row("Profit Margin",    f"{profit_m:.1f}%" if profit_m else "N/A",      "val-g" if profit_m > 10 else "val-y" if profit_m > 0 else "val-r", True)
        funds_html += data_row("Return on Equity", f"{roe:.1f}%" if roe else "N/A",                "val-g" if roe > 15 else "val-y" if roe > 0 else "val-r", True)
        funds_html += data_row("Debt / Equity",    f"{debt_eq:.2f}" if debt_eq else "N/A",         "val-r" if debt_eq > 2 else "val-y" if debt_eq > 1 else "val-g", True)
        funds_html += data_row("Current Ratio",    f"{curr_ratio:.2f}" if curr_ratio else "N/A",   "val-g" if curr_ratio > 1.5 else "val-y" if curr_ratio > 1 else "val-r", True)
        funds_html += data_row("Dividend Yield",   f"{div_yield:.2f}%" if div_yield else "None",   "val-g" if div_yield > 2 else "val-m", True)
        funds_html += data_row("Short % Float",    f"{short_pct:.1f}%" if short_pct else "N/A",    "val-r" if short_pct > 20 else "val-y" if short_pct > 10 else "val-g", True)
        funds_html += data_row("Float Shares",     fmt_cap(float_sh).replace("$","") if float_sh else "N/A", "val-m", True)
        funds_html += '</div>' 
        st.markdown(funds_html, unsafe_allow_html=True)

    # ── ZONE 5b: VOLATILITY PANEL ───────────────────────────
    cur_close = float(row['Close'])
    bb_upper  = vol_data.get('bb_upper', 0)
    bb_lower  = vol_data.get('bb_lower', 0)
    bb_mid    = vol_data.get('bb_mid', 0)
    bb_pct    = vol_data.get('bb_pct', 50)
    hv_30     = vol_data.get('hv_30', 0)
    hv_90     = vol_data.get('hv_90', 0)
    iv        = vol_data.get('iv', 0)
    iv_vs_hv  = vol_data.get('iv_vs_hv', 0)
    bb_col    = "#FF6B6B" if bb_pct > 80 else "#00FF88" if bb_pct < 20 else "#FACC15"
    hv_col    = "#FF6B6B" if hv_30 > 50 else "#FACC15" if hv_30 > 25 else "#00FF88"
    iv_col    = "#FF6B6B" if iv > 60 else "#FACC15" if iv > 30 else "#00FF88"
    iv_label  = "IV > HV — big move expected" if iv_vs_hv > 1.3 else "IV < HV — calm expected" if iv_vs_hv < 0.7 and iv > 0 else "IV ≈ HV — normal" if iv > 0 else "N/A"

    st.markdown('<div class="section-header">Volatility Analysis</div>', unsafe_allow_html=True)
    vc1, vc2, vc3 = st.columns(3)
    with vc1:
        st.markdown('<div class="vol-panel"><div class="data-header">Historical Volatility</div>', unsafe_allow_html=True)
        hv_rows = ''
        hv_rows += f'<div class="vol-row"><span class="vol-lbl">HV 30d <a href="https://www.investopedia.com/terms/h/historicalvolatility.asp" target="_blank" style="color:#4A6080;text-decoration:none;font-size:10px;">ⓘ</a></span><span style="color:{hv_col};font-weight:700;font-family:monospace;">{hv_30:.1f}%</span></div>'
        hv_rows += f'<div class="vol-row"><span class="vol-lbl">HV 90d</span><span style="color:{hv_col};font-weight:700;font-family:monospace;">{hv_90:.1f}%</span></div>'
        hv_rows += f'<div class="vol-row"><span class="vol-lbl">ATR (14) $</span><span style="color:#38BDF8;font-weight:700;font-family:monospace;">{cur}{float(row["ATR"]):.2f}</span></div>'
        hv_rows += f'<div class="vol-row"><span class="vol-lbl">ATR % price</span><span style="color:#38BDF8;font-weight:700;font-family:monospace;">{float(row["ATRPct"])*100:.1f}%</span></div>'
        st.markdown(hv_rows + '</div>', unsafe_allow_html=True)
    with vc2:
        st.markdown('<div class="vol-panel"><div class="data-header">Bollinger Bands (20,2)</div>', unsafe_allow_html=True)
        bb_rows = ''
        bb_rows += f'<div class="vol-row"><span class="vol-lbl">Upper Band <a href="https://www.investopedia.com/terms/b/bollingerbands.asp" target="_blank" style="color:#4A6080;text-decoration:none;font-size:10px;">ⓘ</a></span><span style="color:#FF6B6B;font-weight:700;font-family:monospace;">{cur}{bb_upper:.2f}</span></div>'
        bb_rows += f'<div class="vol-row"><span class="vol-lbl">Middle (20MA)</span><span style="color:#94A3B8;font-weight:700;font-family:monospace;">{cur}{bb_mid:.2f}</span></div>'
        bb_rows += f'<div class="vol-row"><span class="vol-lbl">Lower Band</span><span style="color:#00FF88;font-weight:700;font-family:monospace;">{cur}{bb_lower:.2f}</span></div>'
        bb_rows += f'<div class="vol-row"><span class="vol-lbl">BB Width</span><span style="color:#A78BFA;font-weight:700;font-family:monospace;">{vol_data.get("bb_width",0):.1f}%</span></div>'
        # BB position bar
        bb_rows += f'<div class="vol-row" style="flex-direction:column;gap:4px;"><span class="vol-lbl">Price position in band</span><div style="width:100%;height:6px;background:#243348;border-radius:3px;margin-top:4px;position:relative;"><div style="position:absolute;left:{min(max(bb_pct,2),98):.0f}%;top:-3px;width:10px;height:10px;background:{bb_col};border-radius:50%;transform:translateX(-50%);"></div></div><div style="display:flex;justify-content:space-between;font-size:10px;margin-top:6px;"><span style="color:#00FF88;">Oversold</span><span style="color:{bb_col};">{bb_pct:.0f}%</span><span style="color:#FF6B6B;">Overbought</span></div></div>'
        st.markdown(bb_rows + '</div>', unsafe_allow_html=True)
    with vc3:
        st.markdown('<div class="vol-panel"><div class="data-header">Implied Volatility</div>', unsafe_allow_html=True)
        iv_rows = ''
        iv_rows += f'<div class="vol-row"><span class="vol-lbl">IV <a href="https://www.investopedia.com/terms/i/iv.asp" target="_blank" style="color:#4A6080;text-decoration:none;font-size:10px;">ⓘ</a></span><span style="color:{iv_col};font-weight:700;font-family:monospace;">{iv:.1f}% {"(N/A)" if iv == 0 else ""}</span></div>'
        iv_rows += f'<div class="vol-row"><span class="vol-lbl">IV vs HV 30d</span><span style="color:{"#FF6B6B" if iv_vs_hv > 1.3 else "#00FF88"};font-weight:700;font-family:monospace;">{iv_vs_hv:.2f}x {"↑" if iv_vs_hv > 1.3 else "↓"}</span></div>'
        iv_rows += f'<div class="vol-row" style="flex-direction:column;"><span class="vol-lbl" style="margin-bottom:4px;">Signal</span><span style="color:{"#FF6B6B" if iv_vs_hv > 1.3 else "#00FF88" if iv > 0 else "#94A3B8"};font-size:12px;">{iv_label}</span></div>'
        iv_rows += f'<div class="vol-row"><span class="vol-lbl">Day range est.</span><span style="color:#38BDF8;font-family:monospace;">{cur}{cur_close - float(row["ATR"]):.2f} – {cur}{cur_close + float(row["ATR"]):.2f}</span></div>'
        st.markdown(iv_rows + '</div>', unsafe_allow_html=True)


    # ── ZONE 5c: ANALYST RATINGS ─────────────────────────────
    buy   = analyst_data.get('buy', 0)
    hold  = analyst_data.get('hold', 0)
    sell  = analyst_data.get('sell', 0)
    total_analysts = buy + hold + sell
    target     = analyst_data.get('target', 0)
    target_low = analyst_data.get('target_low', 0)
    target_high= analyst_data.get('target_high', 0)
    rec_key    = analyst_data.get('rec_key', 'N/A').replace('-',' ').title()
    num_analysts = analyst_data.get('num_analysts', 0)
    upside = ((target / cur_close) - 1) * 100 if target > 0 and cur_close > 0 else 0
    up_col = "#00FF88" if upside > 10 else "#FACC15" if upside > 0 else "#FF6B6B"
    cons_col = "#00FF88" if 'Buy' in rec_key or 'Strong' in rec_key else "#FF6B6B" if 'Sell' in rec_key else "#FACC15"

    st.markdown('<div class="section-header">Analyst Ratings</div>', unsafe_allow_html=True)
    ac1, ac2, ac3, ac4, ac5 = st.columns(5)
    for acol, lbl, val, col in [
        (ac1, "Consensus",    rec_key if rec_key != 'N/A' else "N/A", cons_col),
        (ac2, "# Analysts",   str(num_analysts) if num_analysts else "N/A", "#94A3B8"),
        (ac3, "Price Target", f"{cur}{target:.2f}" if target else "N/A", up_col),
        (ac4, "Upside",       f"{upside:+.1f}%" if target else "N/A", up_col),
        (ac5, "Target Range", f"{cur}{target_low:.0f}–{cur}{target_high:.0f}" if target_low else "N/A", "#94A3B8"),
    ]:
        with acol:
            st.markdown(f'<div class="earn-bar" style="border-left-color:{col};"><div class="earn-label">{lbl}</div><div class="earn-val" style="color:{col};">{val}</div></div>', unsafe_allow_html=True)

    if total_analysts > 0:
        buy_pct  = int(buy  / total_analysts * 100)
        hold_pct = int(hold / total_analysts * 100)
        sell_pct = 100 - buy_pct - hold_pct
        st.markdown(f'''<div style="background:#1A2232;border:1px solid #243348;border-radius:0 0 8px 8px;padding:10px 16px;">
          <div style="display:flex;justify-content:space-between;font-size:11px;margin-bottom:6px;">
            <span style="color:#00FF88;font-weight:700;">Buy {buy} ({buy_pct}%)</span>
            <span style="color:#FACC15;font-weight:700;">Hold {hold} ({hold_pct}%)</span>
            <span style="color:#FF6B6B;font-weight:700;">Sell {sell} ({sell_pct}%)</span>
          </div>
          <div style="display:flex;height:8px;border-radius:4px;overflow:hidden;">
            <div style="width:{buy_pct}%;background:#00FF88;"></div>
            <div style="width:{hold_pct}%;background:#FACC15;"></div>
            <div style="width:{sell_pct}%;background:#FF6B6B;"></div>
          </div>
        </div>''', unsafe_allow_html=True)


    # ── ZONE 6: REASONS ──────────────────────────────────────
    bulls = a.get('reasons_bull', [])
    bears = a.get('reasons_bear', [])
    c1, c2 = st.columns(2)
    with c1:
        for b in bulls:
            st.markdown(f'<div class="reason-bull">+ &nbsp;{b}</div>', unsafe_allow_html=True)
    with c2:
        for b in bears:
            st.markdown(f'<div class="reason-bear">− &nbsp;{b}</div>', unsafe_allow_html=True)

    # ── ZONE 7: TIMEFRAMES ───────────────────────────────────
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(f'<div class="tf-day"><div class="tf-label" style="color:#FACC15;">Day Trade</div><div class="tf-note">{a.get("day_trade_note","")}</div></div>', unsafe_allow_html=True)
    with c2:
        st.markdown(f'<div class="tf-swing"><div class="tf-label" style="color:#38BDF8;">Swing Trade</div><div class="tf-note">{a.get("swing_note","")}</div></div>', unsafe_allow_html=True)
    with c3:
        st.markdown(f'<div class="tf-inv"><div class="tf-label" style="color:#00FF88;">Invest</div><div class="tf-note">{a.get("invest_note","")}</div></div>', unsafe_allow_html=True)

    # ── ZONE 8: EARNINGS ─────────────────────────────────────
    # Earnings from session state (computed in run_analysis)
    beat_str = a.get('last_earnings_beat', 'Unknown') or 'Unknown'
    if earnings_hist:
        last_e = earnings_hist[-1]
        s = last_e.get('surprise', 0) or 0
        beat_str = f"Beat +{s:.1f}%" if s > 0 else f"Missed {s:.1f}%"

    earn_days  = days_to_earn
    earn_col   = "#FF6B6B" if 0 < earn_days < 14 else "#FACC15" if 0 < earn_days < 30 else "#94A3B8"
    beat_col   = "#00FF88" if "Beat" in beat_str else "#FF6B6B" if "Miss" in beat_str else "#FACC15"
    c1,c2,c3,c4 = st.columns(4)
    for col, lbl, val, col2 in [
        (c1,"Next Earnings",  earn_date_str, "#94A3B8"),
        (c2,"Countdown",      f"{earn_days} days" if earn_days > 0 else "Unknown", earn_col),
        (c3,"Last Result",    beat_str, beat_col),
        (c4,"Sector",         info.get('sector', a.get('sector','N/A')), "#6B7280"),
    ]:
        with col:
            st.markdown(f'<div class="earn-bar"><div class="earn-label">{lbl}</div><div class="earn-val" style="color:{col2};">{val}</div></div>', unsafe_allow_html=True)

    # AI Summary moved to Zone 3b (after verdict)

    # ── ZONE 8b: EARNINGS HISTORY ───────────────────────────
    st.markdown('<div class="section-header">Earnings History — Last 4 Quarters</div>', unsafe_allow_html=True)
    if not earnings_hist:
        st.markdown('<div class="panel-body"><div style="padding:12px 14px;font-size:12px;color:#4A6080;">No earnings history available</div></div>', unsafe_allow_html=True)
    else:
        eh_html = '<div style="background:#1A2232;border:1px solid #243348;border-radius:0 0 8px 8px;">'
        eh_html += '<div class="earn-hist-row" style="background:#131F32;font-size:11px;color:#64748B;"><span>Quarter</span><span>Est EPS</span><span>Actual EPS</span><span>Surprise</span></div>'
        for e in reversed(earnings_hist):
            beat_cls = "earn-beat" if e["beat"] else "earn-miss"
            icon     = "▲" if e["beat"] else "▼"
            surp_str = f'{icon} {e["surprise"]:+.1f}%'
            eh_html += f'<div class="earn-hist-row"><span style="color:#E2E8F0;">{e["quarter"]}</span><span style="color:#94A3B8;font-family:monospace;">{cur}{e["estimate"]:.2f}</span><span style="color:#E2E8F0;font-family:monospace;">{cur}{e["actual"]:.2f}</span><span class="{beat_cls};">{surp_str}</span></div>'
        st.markdown(eh_html + '</div>', unsafe_allow_html=True)

    # ── ZONE 8c: INSIDER TRADING ─────────────────────────────
    st.markdown('<div class="section-header">Insider Transactions <a href="https://www.investopedia.com/terms/i/insidertrading.asp" target="_blank" style="color:#4A6080;text-decoration:none;font-size:10px;letter-spacing:0;text-transform:none;">ⓘ What is insider trading?</a></div>', unsafe_allow_html=True)
    if not insider_data:
        st.markdown('<div style="background:#1A2232;border:1px solid #243348;border-radius:0 0 8px 8px;padding:12px 14px;font-size:12px;color:#4A6080;">No recent insider transactions found</div>', unsafe_allow_html=True)
    else:
        ins_html = '<div style="background:#1A2232;border:1px solid #243348;border-radius:0 0 8px 8px;">'
        ins_html += '<div class="insider-row" style="background:#131F32;"><span style="font-size:11px;color:#64748B;flex:1;">Insider</span><span style="font-size:11px;color:#64748B;flex:1;">Role</span><span style="font-size:11px;color:#64748B;width:60px;text-align:center;">Type</span><span style="font-size:11px;color:#64748B;text-align:right;">Shares / Value</span></div>'
        for ins in insider_data:
            t_col = "#00FF88" if ins["type"]=="BUY" else "#FF6B6B"
            val_str = f'${ins["value"]:,.0f}' if ins["value"] > 0 else "N/A"
            ins_html += f'<div class="insider-row"><span class="insider-name">{ins["name"]}</span><span class="insider-role">{ins["role"]}</span><span style="color:{t_col};font-weight:700;font-size:11px;width:60px;text-align:center;">{ins["type"]}</span><span class="insider-shares">{ins["shares"]:,} / {val_str}</span></div>'
        st.markdown(ins_html + '</div>', unsafe_allow_html=True)

    # ── ZONE 8d: NEWS SENTIMENT ───────────────────────────────
    news_sentiment = a.get('news_sentiment', [])
    sentiment_map = {n.get('headline','')[:30]: n for n in news_sentiment}

    st.markdown('<div class="section-header">News & Sentiment</div>', unsafe_allow_html=True)
    if not news_items:
        st.markdown('<div style="background:#1A2232;border:1px solid #243348;border-radius:0 0 8px 8px;padding:12px 14px;font-size:12px;color:#4A6080;">No recent news available</div>', unsafe_allow_html=True)
    else:
        news_html = '<div style="background:#1A2232;border:1px solid #243348;border-radius:0 0 8px 8px;">'
        for i, news in enumerate(news_items):
            title = news.get('title','')
            pub   = news.get('publisher','')
            link  = news.get('link','')
            # Find matching sentiment from Claude
            sent_data = next((s for s in news_sentiment if title[:20] in s.get('headline','') or s.get('headline','')[:20] in title), None)
            sent      = sent_data.get('sentiment','neutral') if sent_data else 'neutral'
            reason    = sent_data.get('reason','') if sent_data else ''
            sent_col  = "#00FF88" if sent=='bullish' else "#FF6B6B" if sent=='bearish' else "#FACC15"
            sent_icon = "▲" if sent=='bullish' else "▼" if sent=='bearish' else "↔"
            sent_lbl  = sent.capitalize()
            news_html += f'<div class="news-row">'
            if link:
                news_html += f'<div class="news-headline"><a href="{link}" target="_blank" style="color:#E2E8F0;text-decoration:none;">{title}</a></div>'
            else:
                news_html += f'<div class="news-headline">{title}</div>'
            news_html += f'<div class="news-meta"><span style="color:#4A6080;">{pub}</span><span style="color:{sent_col};font-weight:700;">{sent_icon} {sent_lbl}</span></div>'
            if reason:
                news_html += f'<div style="font-size:11px;color:#64748B;margin-top:3px;">{reason}</div>'
            news_html += '</div>'
        st.markdown(news_html + '</div>', unsafe_allow_html=True)

    # ── ZONE 9b: MARKET CONTEXT + BUSINESS CYCLE ─────────────
    mctx = st.session_state.get('market_ctx', {})
    cycle = a.get('cycle_phase','')
    cycle_col = "#00FF88" if cycle=="Early" else "#38BDF8" if cycle=="Mid" else "#FACC15" if cycle=="Late" else "#FF6B6B"
    mkt_risk = a.get('market_risk','')
    risk_col  = "#00FF88" if mkt_risk=="Low" else "#38BDF8" if mkt_risk=="Moderate" else "#FACC15" if mkt_risk=="High" else "#FF6B6B"

    st.markdown('<div class="section-header" style="margin-top:8px;">Market Context & Business Cycle</div>', unsafe_allow_html=True)
    mc1,mc2,mc3,mc4,mc5 = st.columns(5)
    for mcol, lbl, val, chg, sig in [
        (mc1,"S&P 500","SPY", mctx.get("spy_1m",0), mctx.get("spy_signal","—")),
        (mc2,"NASDAQ", "QQQ", mctx.get("qqq_1m",0), mctx.get("qqq_signal","—")),
        (mc3,"DOW",    "DIA", mctx.get("dia_1m",0), mctx.get("dia_signal","—")),
        (mc4,"Cycle Phase", cycle, 0, ""),
        (mc5,"Market Risk",  mkt_risk, 0, ""),
    ]:
        scol = "#00FF88" if sig=="Bullish" or val in ["Early"] else "#FF6B6B" if sig=="Bearish" or val in ["Recession"] else "#FACC15"
        if lbl in ["Cycle Phase","Market Risk"]:
            vcol = cycle_col if lbl=="Cycle Phase" else risk_col
            vval = val or "—"
            desc_txt = a.get("cycle_desc","") if lbl=="Cycle Phase" else a.get("market_risk_desc","")
        else:
            vcol = "#00FF88" if chg >= 0 else "#FF6B6B"
            vval = f"{chg:+.1f}% (1M)"
            desc_txt = sig
        with mcol:
            st.markdown(f'''<div class="earn-bar" style="border-left-color:{vcol};">
              <div class="earn-label">{lbl}</div>
              <div class="earn-val" style="color:{vcol};font-size:12px;">{vval}</div>
              <div style="font-size:10px;color:#6B7280;margin-top:2px;">{desc_txt}</div>
            </div>''', unsafe_allow_html=True)

    # ── ZONE 10: LIVE CHART ──────────────────────────────────
    st.markdown('<div class="section-header" style="margin-top:12px;">Live Chart · Daily Candles · 1 Year</div>', unsafe_allow_html=True)
    chart_df = df.tail(252).copy()
    st.plotly_chart(build_chart(chart_df, ticker), use_container_width=True, config={'displayModeBar': True})

    # ── ZONE 11: PATTERN ANALYSIS ────────────────────────────
    # Chart patterns
    chart_pats = a.get('chart_patterns', [])
    st.markdown('<div class="section-header">Chart Patterns Detected</div>', unsafe_allow_html=True)
    if not chart_pats:
        st.markdown('<div class="panel-body"><div style="padding:14px;text-align:center;font-size:12px;color:#4A6080;">No significant chart patterns detected in current price action</div></div>', unsafe_allow_html=True)
    else:
        cols = st.columns(min(len(chart_pats), 3))
        for i, p in enumerate(chart_pats[:3]):
            ptype = p.get('type','neutral')
            pcls  = "pat-bull" if ptype=="bullish" else "pat-bear" if ptype=="bearish" else "pat-neut"
            pcol  = "#00FF88" if ptype=="bullish" else "#FF6B6B" if ptype=="bearish" else "#FACC15"
            conf  = min(100, max(0, int(p.get('confidence', 0))))
            with cols[i]:
                pat_name   = p.get("name","")
                inv_url    = f"https://www.investopedia.com/search#q={pat_name.replace(' ','+')}"
                bias_label = "▲ Bullish" if ptype=="bullish" else "▼ Bearish" if ptype=="bearish" else "↔ Neutral"
                target_html = f'<div class="pat-target" style="color:{pcol};">Target: {p.get("target_pct",0):+.1f}% → {cur}{p.get("target_price",0):.2f}</div>' if p.get('target_price') else ''
                st.markdown(f"""
                <div class="{pcls}">
                  <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:4px;">
                    <div class="pat-name" style="color:{pcol};">{pat_name}</div>
                    <a href="{inv_url}" target="_blank" style="font-size:10px;color:#4A6080;text-decoration:none;" title="Learn on Investopedia">ⓘ</a>
                  </div>
                  <div style="font-size:11px;font-weight:700;color:{pcol};margin-bottom:4px;">{bias_label}</div>
                  <div style="font-size:10px;color:#6B7280;margin-bottom:4px;">Confidence: {conf}%</div>
                  <div style="height:3px;background:#243348;border-radius:2px;margin-bottom:6px;">
                    <div style="width:{conf}%;height:3px;background:{pcol};border-radius:2px;"></div>
                  </div>
                  <div class="pat-desc">{p.get("description","")}</div>
                  {target_html}
                </div>""", unsafe_allow_html=True)

    # Candlestick patterns
    candle_pats = a.get('candle_patterns', [])
    st.markdown('<div class="section-header">Candlestick Patterns · Last 5 Sessions</div>', unsafe_allow_html=True)
    if not candle_pats:
        st.markdown('<div class="panel-body"><div style="padding:14px;text-align:center;font-size:12px;color:#4A6080;">No significant candlestick patterns in the last 5 sessions</div></div>', unsafe_allow_html=True)
    else:
        cols = st.columns(min(len(candle_pats), 4))
        for i, c in enumerate(candle_pats[:4]):
            ctype  = c.get('type','neutral')
            ccol   = "#00FF88" if ctype=="bullish" else "#FF6B6B" if ctype=="bearish" else "#FACC15"
            ccls   = "candle-card-bull" if ctype=="bullish" else "candle-card-bear" if ctype=="bearish" else "candle-card-neut"
            clabel = "▲ Bullish" if ctype=="bullish" else "▼ Bearish" if ctype=="bearish" else "↔ Neutral"
            inv_c  = f"https://www.investopedia.com/search#q={c.get('name','').replace(' ','+')}"
            with cols[i]:
                st.markdown(f'''
                <div class="{ccls}">
                  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:3px;">
                    <div style="font-size:13px;font-weight:700;color:{ccol};">{c.get("name","")}</div>
                    <a href="{inv_c}" target="_blank" style="font-size:10px;color:#4A6080;text-decoration:none;">ⓘ</a>
                  </div>
                  <div style="font-size:11px;color:{ccol};font-weight:700;margin-bottom:5px;">{clabel} · {c.get("session","")}</div>
                  <div style="font-size:12px;color:#CBD5E1;line-height:1.5;">{c.get("meaning","")}</div>
                </div>''', unsafe_allow_html=True)

    # Trend context
    st.markdown('<div class="section-header">Trend Context</div>', unsafe_allow_html=True)
    trend_items = [
        ("Short-term (5d)",  a.get('trend_short','N/A'),  a.get('trend_short_desc','')),
        ("Medium-term (20d)",a.get('trend_medium','N/A'), a.get('trend_medium_desc','')),
        ("Long-term (200d)", a.get('trend_long','N/A'),   a.get('trend_long_desc','')),
        ("Pattern Bias",     a.get('pattern_bias','N/A'), a.get('pattern_bias_desc','')),
    ]
    cols = st.columns(4)
    for i, (lbl, val, desc) in enumerate(trend_items):
        tcol = "#00FF88" if val=="Uptrend" or val=="Bullish" else "#FF6B6B" if val in ["Downtrend","Bearish"] else "#FACC15"
        arrow = " ↗" if val in ["Uptrend","Bullish"] else " ↘" if val in ["Downtrend","Bearish"] else " ↔"
        with cols[i]:
            st.markdown(f'''
            <div class="trend-tile">
              <div class="trend-tile-label">{lbl}</div>
              <div class="trend-tile-val" style="color:{tcol};">{val}{arrow}</div>
              <div class="trend-tile-desc">{desc}</div>
            </div>''', unsafe_allow_html=True)

    st.markdown('<div class="hud-footer">NOT FINANCIAL ADVICE · AI-GENERATED · EDUCATIONAL PURPOSES ONLY</div>', unsafe_allow_html=True)


if __name__ == "__main__":
    main()
