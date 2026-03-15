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
  .candle-card { background: #161B22; border: 1px solid #243348; border-radius: 8px; padding: 10px 12px; text-align: center; }

  /* Trend tiles */
  .trend-tile { background: #161B22; border: 1px solid #243348; border-radius: 8px; padding: 10px 12px; }
  .trend-tile-label { font-size: 10px; color: #4A6080; letter-spacing: 1px; text-transform: uppercase; margin-bottom: 4px; }
  .trend-tile-val { font-size: 14px; font-weight: 700; margin-bottom: 3px; }
  .trend-tile-desc { font-size: 11px; color: #94A3B8; line-height: 1.4; }

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
        'ATR':   {'bull': row['ATRPct'] < 0.04,       'label': 'ATR%',   'val': f"{row['ATRPct']*100:.1f}%"},
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

FUNDAMENTALS:
Market Cap: {fmt_cap(info.get('marketCap', 0))}
P/E: {info.get('trailingPE', 'N/A')} | Forward P/E: {info.get('forwardPE', 'N/A')}
EPS: {info.get('trailingEps', 'N/A')} | P/B: {info.get('priceToBook', 'N/A')}
Revenue Growth: {info.get('revenueGrowth', 'N/A')} | Earnings Growth: {info.get('earningsGrowth', 'N/A')}
Next Earnings: {info.get('earningsDate', 'Unknown')}
Sector: {info.get('sector', 'N/A')}

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
"pattern_bias":"Bullish|Bearish|Neutral","pattern_bias_desc":"one sentence overall"}}"""

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
    "Market Cap": "https://www.investopedia.com/terms/m/marketcapitalization.asp",
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
            if st.button("Analyze →"):
                if ticker_in.strip():
                    run_analysis(ticker_in.strip().upper())
            st.markdown('<div style="text-align:center;font-size:11px;color:#243348;margin-top:16px;">US: AAPL · NVDA · PLTR &nbsp;|&nbsp; TSX: add .TO (RY.TO) &nbsp;|&nbsp; London: add .L</div>', unsafe_allow_html=True)
        return

    # HUD screen
    render_hud()


def run_analysis(ticker):
    with st.spinner(f"Analyzing {ticker}..."):
        try:
            # Fetch data — 2 years for enough MA200 history
            raw = yf.Ticker(ticker)
            df  = raw.history(period="2y")
            if df.empty or len(df) < 50:
                st.error(f"No data found for {ticker}. Check the ticker symbol.")
                return

            df = calculate_indicators(df)
            if len(df) < 20:
                st.error("Not enough data to calculate indicators.")
                return

            info = raw.info or {}
            row  = df.iloc[-1]
            prev = df.iloc[-2]

            signals, score = calc_signals(row)

            # Fibonacci
            h52 = float(info.get('fiftyTwoWeekHigh', df['High'].tail(252).max()))
            l52 = float(info.get('fiftyTwoWeekLow',  df['Low'].tail(252).min()))
            rng = h52 - l52
            fibs = [h52 - rng*0.382, h52 - rng*0.500, h52 - rng*0.618]

            # Claude analysis
            analysis = get_claude_analysis(ticker, info, df, signals, score, fibs)
            if 'error' in analysis:
                st.error(f"Claude API error: {analysis['error']}")
                return

            # Store in session
            st.session_state.analysis  = analysis
            st.session_state.df        = df
            st.session_state.info      = info
            st.session_state.ticker    = ticker
            st.session_state.signals   = signals
            st.session_state.score     = score
            st.session_state.fibs      = fibs
            st.session_state.row       = row
            st.session_state.prev      = prev
            st.rerun()

        except Exception as e:
            st.error(f"Error: {str(e)}")


def render_hud():
    a       = st.session_state.analysis
    df      = st.session_state.df
    info    = st.session_state.info
    ticker  = st.session_state.ticker
    signals = st.session_state.signals
    score   = st.session_state.score
    fibs    = st.session_state.fibs
    row     = st.session_state.row
    prev    = st.session_state.prev

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
      ATR&nbsp;<span>{atr_pct*100:.1f}%</span>&nbsp;&nbsp;
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
        st.markdown('<div class="section-header">Key Levels</div>', unsafe_allow_html=True)
        levels_html = '<div class="panel-body">'
        levels_html += data_row("Entry zone", f"{cur}{a.get('entry_low',0):.2f} – {cur}{a.get('entry_high',0):.2f}", "val-y")
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

        funds_html = '<div class="panel-body">'
        funds_html += data_row("Market Cap",     fmt_cap(mc, show_info=True) if mc else "N/A", "val-w", show_info=True)
        funds_html += data_row("P/E Ratio",      f"{pe:.1f}" if pe else "N/A", "val-r" if pe > 40 else "val-y" if pe > 20 else "val-g")
        funds_html += data_row("P/B Ratio",      f"{pb:.1f}" if pb else "N/A", "val-r" if pb > 5 else "val-g")
        funds_html += data_row("PEG Ratio",      f"{peg:.1f}" if peg else "N/A","val-r" if peg > 3 else "val-y" if peg > 1.5 else "val-g")
        funds_html += data_row("EPS Growth YoY", f"{eps_g:+.1f}%" if eps_g else "N/A", "val-g" if eps_g > 0 else "val-r")
        funds_html += data_row("Rev Growth YoY", f"{rev_g:+.1f}%" if rev_g else "N/A", "val-g" if rev_g > 0 else "val-r")
        funds_html += data_row("20 MA",  f"{cur}{row['MA20']:.2f}  ({ma20_pct:+.1f}%)",  "val-g" if close > row['MA20']  else "val-r")
        funds_html += data_row("50 MA",  f"{cur}{row['MA50']:.2f}  ({ma50_pct:+.1f}%)",  "val-g" if close > row['MA50']  else "val-r")
        funds_html += data_row("200 MA", f"{cur}{row['MA200']:.2f}  ({ma200_pct:+.1f}%)", "val-g" if close > row['MA200'] else "val-r")
        funds_html += data_row("RSI (14)", f"{row['RSI']:.1f}  {'Overbought' if row['RSI']>70 else 'Oversold' if row['RSI']<30 else 'Neutral'}", "val-r" if row['RSI']>70 else "val-g" if row['RSI']<30 else "val-y")
        funds_html += data_row("MACD Hist", f"{row['MACDHist']:+.3f}", "val-g" if row['MACDHist']>0 else "val-r")
        funds_html += data_row("OBV", "Rising" if row['OBV'] > prev['OBV'] else "Falling", "val-g" if row['OBV'] > prev['OBV'] else "val-r")
        funds_html += '</div>'
        st.markdown(funds_html, unsafe_allow_html=True)

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
    # Get earnings from yfinance directly — more reliable than Claude
    try:
        cal = raw.calendar
        if cal is not None and not cal.empty:
            next_earn = cal.iloc[0].get('Earnings Date', None)
            if next_earn:
                from datetime import timezone
                next_earn_dt = pd.Timestamp(next_earn).tz_localize(None) if hasattr(next_earn, 'tzinfo') and next_earn.tzinfo is None else pd.Timestamp(next_earn).tz_convert(None)
                days_to_earn = (next_earn_dt - pd.Timestamp.now()).days
                earn_date_str = next_earn_dt.strftime("%b %d, %Y")
            else:
                days_to_earn = 0
                earn_date_str = "Unknown"
        else:
            days_to_earn = 0
            earn_date_str = a.get('earnings_date', 'Unknown') or 'Unknown'
    except:
        days_to_earn = 0
        earn_date_str = a.get('earnings_date', 'Unknown') or 'Unknown'

    # Last earnings beat from yfinance
    try:
        earnings_hist = raw.earnings_history
        if earnings_hist is not None and not earnings_hist.empty:
            last = earnings_hist.iloc[-1]
            surprise = last.get('surprisePercent', None)
            if surprise is not None:
                surprise_pct = float(surprise) * 100
                beat_str = f"Beat +{surprise_pct:.1f}%" if surprise_pct > 0 else f"Missed {surprise_pct:.1f}%"
            else:
                beat_str = a.get('last_earnings_beat', 'Unknown') or 'Unknown'
        else:
            beat_str = a.get('last_earnings_beat', 'Unknown') or 'Unknown'
    except:
        beat_str = a.get('last_earnings_beat', 'Unknown') or 'Unknown'

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

    # ── ZONE 9: AI SUMMARY ───────────────────────────────────
    st.markdown('<div class="section-header" style="border-radius:8px 8px 0 0;">AI Summary</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="summary-box"><div class="summary-text">{a.get("summary","")}</div></div>', unsafe_allow_html=True)

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
                target_html = f'<div class="pat-target" style="color:{pcol};">Target: {p.get("target_pct",0):+.1f}% → {cur}{p.get("target_price",0):.2f}</div>' if p.get('target_price') else ''
                st.markdown(f'''
                <div class="{pcls}">
                  <div class="pat-name" style="color:{pcol};">{p.get("name","")}</div>
                  <div style="font-size:10px;color:#6B7280;margin-bottom:4px;">Confidence: {conf}%</div>
                  <div style="height:3px;background:#243348;border-radius:2px;margin-bottom:6px;">
                    <div style="width:{conf}%;height:3px;background:{pcol};border-radius:2px;"></div>
                  </div>
                  <div class="pat-desc">{p.get("description","")}</div>
                  {target_html}
                </div>''', unsafe_allow_html=True)

    # Candlestick patterns
    candle_pats = a.get('candle_patterns', [])
    st.markdown('<div class="section-header">Candlestick Patterns · Last 5 Sessions</div>', unsafe_allow_html=True)
    if not candle_pats:
        st.markdown('<div class="panel-body"><div style="padding:14px;text-align:center;font-size:12px;color:#4A6080;">No significant candlestick patterns in the last 5 sessions</div></div>', unsafe_allow_html=True)
    else:
        cols = st.columns(min(len(candle_pats), 4))
        for i, c in enumerate(candle_pats[:4]):
            ctype = c.get('type','neutral')
            ccol  = "#00FF88" if ctype=="bullish" else "#FF6B6B" if ctype=="bearish" else "#FACC15"
            with cols[i]:
                st.markdown(f'''
                <div class="candle-card">
                  <div style="font-size:13px;font-weight:700;color:{ccol};margin-bottom:3px;">{c.get("name","")}</div>
                  <div style="font-size:10px;color:{ccol};font-weight:600;margin-bottom:4px;">{c.get("session","")}</div>
                  <div style="font-size:11px;color:#6B7280;line-height:1.4;">{c.get("meaning","")}</div>
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
