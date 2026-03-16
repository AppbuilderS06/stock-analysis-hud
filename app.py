import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import anthropic
import json
from datetime import datetime

# ── Data layer: FMP primary, yfinance fallback ───────────────
# FMP API key stored in Streamlit secrets as FMP_API_KEY
# Get free key at financialmodelingprep.com (250 calls/day free)

def _fmp_get(endpoint, api_key, params=""):
    """Make a single FMP API call. Returns parsed JSON or None.
    Detects FMP rate-limit responses (HTTP 200 with error body) and returns None.
    """
    import requests
    try:
        if not api_key:
            return None
        url = f"https://financialmodelingprep.com/api/{endpoint}?apikey={api_key}{params}"
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if not data:
                return None
            # FMP returns {"Error Message": "Limit Reach..."} or {"message": "..."} on rate limit / bad key
            if isinstance(data, dict) and ("Error Message" in data or "message" in data):
                return None
            return data
        return None
    except:
        return None

def search_ticker_fmp(query, fmp_key=""):
    """Search FMP. Uses session-state cache — never caches empty results."""
    if not fmp_key or not query:
        return []
    cache_key = f"_fmp_search_{query.upper()}"
    cached = st.session_state.get(cache_key)
    if cached:  # only truthy (non-empty) results get cached
        return cached
    results = _fmp_get(f"v3/search?query={query}&limit=15", fmp_key)
    if not results or not isinstance(results, list):
        return []
    stocks = [r for r in results if r.get("symbol","")]
    major = {"NYSE","NASDAQ","TSX","LSE","EURONEXT","XETRA","ASX","HKG","NSE","AMEX","BATS"}
    def sort_key(r):
        is_exact = 0 if r.get("symbol","").upper() == query.upper() else 1
        is_major = 0 if r.get("exchangeShortName","") in major else 1
        return (is_exact, is_major)
    stocks.sort(key=sort_key)
    result = stocks[:12]
    if result:  # only cache non-empty
        st.session_state[cache_key] = result
    return result

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_ticker_data(ticker, fmp_key="", _v=13):
    """Hybrid: yfinance for price+fundamentals, FMP only for analyst/earnings/insider.
    Uses ~4 FMP calls per ticker instead of 11. Search is separate cached call."""
    import time, requests
    yf_ticker = ticker.replace('BRK.B','BRK-B').replace('BRK.A','BRK-A')
    use_fmp   = bool(fmp_key)

    # ── PRICE HISTORY — yfinance (free, unlimited) ────────────
    df = None
    for attempt in range(3):
        try:
            raw = yf.Ticker(yf_ticker)
            df  = raw.history(period="2y")
            if not df.empty: break
        except:
            if attempt < 2: time.sleep(2 ** attempt)
    if df is None: df = pd.DataFrame()

    # ── FUNDAMENTALS — yfinance financial statements ──────────
    # These use different Yahoo URLs and are reliable
    info = {}
    try:
        fi = raw.fast_info
        for attr, key in [
            ('market_cap',          'marketCap'),
            ('year_high',           'fiftyTwoWeekHigh'),
            ('year_low',            'fiftyTwoWeekLow'),
            ('last_price',          'regularMarketPrice'),
            ('shares',              'sharesOutstanding'),
            ('fifty_day_average',   'fiftyDayAverage'),
            ('two_hundred_day_average', 'twoHundredDayAverage'),
        ]:
            try:
                v = getattr(fi, attr, None)
                if v is not None and v != 0:
                    info[key] = v
            except: pass
    except: pass

    # Income statement → margins, growth, EPS
    try:
        inc = raw.income_stmt
        if inc is not None and not inc.empty:
            price  = float(info.get('regularMarketPrice', 0) or 0)
            shares = float(info.get('sharesOutstanding', 1) or 1)
            def _row(df, *names):
                for n in names:
                    for idx in df.index:
                        if n.lower() in str(idx).lower():
                            try:
                                v = float(df.loc[idx].iloc[0] or 0)
                                if v != 0: return v
                            except: pass
                return 0
            rev      = _row(inc, 'Total Revenue')
            rev_prev = _row(inc.iloc[:,1:2] if inc.shape[1]>1 else inc, 'Total Revenue')
            net      = _row(inc, 'Net Income')
            net_prev = _row(inc.iloc[:,1:2] if inc.shape[1]>1 else inc, 'Net Income')
            op_inc   = _row(inc, 'Operating Income')
            gross    = _row(inc, 'Gross Profit')
            if rev > 0:
                info['operatingMargins'] = op_inc / rev
                info['profitMargins']    = net / rev
                info['grossMargins']     = gross / rev
            if rev > 0 and rev_prev > 0:
                info['revenueGrowth'] = (rev - rev_prev) / abs(rev_prev)
            if net_prev != 0:
                info['earningsGrowth'] = (net - net_prev) / abs(net_prev)
            eps = net / shares if shares else 0
            if eps != 0:
                calc_pe = round(price / abs(eps), 2)
                if 1 < calc_pe < 500:
                    info['trailingPE'] = calc_pe
                info['trailingEps'] = round(eps, 4)
    except: pass

    # Balance sheet → D/E, current ratio, P/B
    try:
        bs = raw.balance_sheet
        if bs is not None and not bs.empty:
            price  = float(info.get('regularMarketPrice', 0) or 0)
            shares = float(info.get('sharesOutstanding', 1) or 1)
            def _b(*names):
                for n in names:
                    for idx in bs.index:
                        if n.lower() in str(idx).lower():
                            try:
                                v = float(bs.loc[idx].iloc[0] or 0)
                                if v != 0: return v
                            except: pass
                return 0
            equity    = _b('Stockholders Equity','Total Equity','Common Stock Equity')
            debt      = _b('Total Debt','Long Term Debt')
            c_assets  = _b('Current Assets')
            c_liab    = _b('Current Liabilities')
            if equity != 0:
                if debt != 0:  info['debtToEquity'] = debt / abs(equity)
                bvps = equity / shares
                if bvps != 0 and price > 0:
                    info['priceToBook'] = round(price / abs(bvps), 2)
                net_inc = float(info.get('trailingEps', 0) or 0) * shares
                if net_inc != 0:
                    info['returnOnEquity'] = net_inc / abs(equity)
            if c_liab != 0:
                info['currentRatio'] = c_assets / abs(c_liab)
    except: pass

    # Dividends
    try:
        divs = raw.dividends
        if divs is not None and not divs.empty:
            annual_div = float(divs.tail(4).sum())
            price = float(info.get('regularMarketPrice', 0) or 0)
            if price > 0 and annual_div > 0:
                info['dividendYield'] = annual_div / price
    except: pass

    # Sector/industry from info (try raw.info but don't depend on it)
    try:
        yf_info = raw.info or {}
        for k in ['sector','industry','longName','shortName','country',
                  'shortPercentOfFloat','floatShares','pegRatio','forwardPE',
                  'targetMeanPrice','targetLowPrice','targetHighPrice',
                  'numberOfAnalystOpinions','recommendationKey','recommendationMean']:
            if yf_info.get(k) and k not in info:
                info[k] = yf_info[k]
    except: pass

    # ── NEWS — yfinance (free, reliable enough) ───────────────
    news = []
    try:
        for item in (raw.news or [])[:5]:
            try:
                t = str(item.get('title','') or item.get('content',{}).get('title',''))
                p = str(item.get('publisher','') or '')
                l = str(item.get('link','') or item.get('content',{}).get('canonicalUrl',{}).get('url',''))
                if t: news.append({'title':t,'publisher':p,'link':l})
            except: pass
    except: pass
    if not news and use_fmp:
        try:
            articles = _fmp_get(f"v3/stock_news?tickers={ticker}&limit=5", fmp_key)
            if articles:
                for a in articles[:5]:
                    t = str(a.get("title",""))
                    if t: news.append({"title":t,"publisher":str(a.get("site","")),"link":str(a.get("url",""))})
        except: pass

    # ── FMP-ONLY SECTION (4 calls) ────────────────────────────
    earn_hist       = None
    insider         = None
    rec_summary     = None
    analyst_targets = None
    calendar        = None
    earn_dates      = None

    if use_fmp:
        # Call 0: Profile — company name + sector + forwardPE (yfinance broken)
        try:
            # Try both BRK-B and BRK.B formats — FMP is inconsistent
            profile = _fmp_get(f"v3/profile/{ticker}", fmp_key)
            if not profile or not isinstance(profile, list) or not profile:
                alt = ticker.replace('-','.')
                profile = _fmp_get(f"v3/profile/{alt}", fmp_key)
            if profile and isinstance(profile, list) and profile:
                p = profile[0]
                # Name fields: ALWAYS overwrite — FMP is authoritative, yfinance returns ticker symbol
                if p.get('companyName',''):
                    info['longName']  = p['companyName']
                    info['shortName'] = p['companyName']
                # Other fields: only set if missing
                for key, val in [
                    ('sector',    p.get('sector','')),
                    ('industry',  p.get('industry','')),
                    ('country',   p.get('country','')),
                    ('forwardPE', p.get('pe', 0)),
                    ('beta',      p.get('beta', 0)),
                ]:
                    if val and key not in info:
                        info[key] = val
        except: pass

        # Call 1: Earnings history (beat/miss) — yfinance can't do this
        try:
            surp = _fmp_get(f"v3/earnings-surprises/{ticker}", fmp_key)
            if surp and isinstance(surp, list):
                rows = [{"period": e.get("date",""),
                         "epsEstimate": e.get("estimatedEps",0),
                         "epsActual":   e.get("actualEps",0),
                         "surprisePercent": ((e.get("actualEps",0)-e.get("estimatedEps",0))/
                                             abs(e.get("estimatedEps",1) or 1))}
                        for e in surp[:4]]
                earn_hist = pd.DataFrame(rows)
        except: pass

        # Call 2: Analyst consensus + price targets
        try:
            tp = _fmp_get(f"v3/price-target-consensus/{ticker}", fmp_key)
            if tp and isinstance(tp, list) and tp:
                t = tp[0]
                analyst_targets = {"mean": t.get("targetConsensus",0),
                                   "high": t.get("targetHigh",0),
                                   "low":  t.get("targetLow",0)}
                info.update({"targetMeanPrice": analyst_targets["mean"],
                             "targetHighPrice": analyst_targets["high"],
                             "targetLowPrice":  analyst_targets["low"]})
        except: pass

        # Call 3: Buy/hold/sell counts
        try:
            est = _fmp_get(f"v3/analyst-stock-recommendations/{ticker}", fmp_key)
            if est and isinstance(est, list) and est:
                e = est[0]
                rec_summary = pd.DataFrame([{
                    "strongBuy": e.get("strongBuy",0), "buy": e.get("buy",0),
                    "hold": e.get("hold",0), "sell": e.get("sell",0),
                    "strongSell": e.get("strongSell",0)
                }])
                total = sum(e.get(k,0) for k in ["strongBuy","buy","hold","sell","strongSell"])
                info["numberOfAnalystOpinions"] = total
        except: pass

        # Call 4: Next earnings date
        try:
            from datetime import datetime, timedelta
            today = datetime.now().strftime("%Y-%m-%d")
            fut   = (datetime.now() + timedelta(days=90)).strftime("%Y-%m-%d")
            cal = _fmp_get(f"v3/earning_calendar?from={today}&to={fut}", fmp_key)
            if cal:
                matches = [e for e in cal if e.get("symbol","").upper() == ticker.upper()]
                if matches:
                    ned = matches[0].get("date","")
                    calendar = {"Earnings Date": ned}
                    info["earningsDate"] = ned
        except: pass

    # Insider — yfinance works for most US stocks
    try:
        ins = raw.insider_transactions
        if ins is not None and not ins.empty:
            insider = ins
    except: pass
    if insider is None or (hasattr(insider,'empty') and insider.empty):
        try:
            insider = raw.insider_purchases
        except: pass

    # Calendar fallback — yfinance
    if not calendar:
        try:
            cal_yf = raw.calendar
            if cal_yf is not None:
                calendar = cal_yf if isinstance(cal_yf, dict) else (cal_yf.to_dict() if hasattr(cal_yf,'to_dict') else None)
        except: pass

    # IV from options chain
    iv = 0.0
    try:
        opts = raw.options
        if opts:
            chain = raw.option_chain(opts[0])
            cp    = float(df["Close"].iloc[-1]) if not df.empty else 0
            atm   = chain.calls.iloc[(chain.calls["strike"]-cp).abs().argsort()[:1]]
            iv    = float(atm["impliedVolatility"].values[0]) * 100
    except: pass

    return {"df":df,"info":info,"calendar":calendar,"rec_summary":rec_summary,
            "earn_hist":earn_hist,"earn_dates":earn_dates,"insider":insider,
            "analyst_targets":analyst_targets,"news":news,"iv":iv}


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_market_context():
    """Cache SPY/QQQ/DIA for 15 minutes — shared across all users."""
    try:
        idx_df = yf.download(["SPY","QQQ","DIA"], period="3mo",
                              auto_adjust=True, progress=False, threads=True)
        def idx_sig(sym):
            try:
                s = idx_df["Close"][sym].dropna() if ("Close",sym) in idx_df.columns else idx_df["Close"].dropna()
                if len(s) < 20: return "Unknown", 0
                c=float(s.iloc[-1]); m20=float(s.tail(20).mean())
                m50=float(s.tail(50).mean()) if len(s)>=50 else m20
                sig = "Bullish" if c>m20 and c>m50 else "Bearish" if c<m20 and c<m50 else "Neutral"
                return sig, round((c/float(s.iloc[-20])-1)*100, 2)
            except: return "Unknown", 0
        spy_s,spy_c = idx_sig("SPY")
        qqq_s,qqq_c = idx_sig("QQQ")
        dia_s,dia_c = idx_sig("DIA")
        return {"spy_signal":spy_s,"qqq_signal":qqq_s,"dia_signal":dia_s,
                "spy_1m":spy_c,"qqq_1m":qqq_c,"dia_1m":dia_c}
    except:
        return {"spy_signal":"Unknown","qqq_signal":"Unknown","dia_signal":"Unknown",
                "spy_1m":0,"qqq_1m":0,"dia_1m":0}

# ── Page config ──────────────────────────────────────────────
st.set_page_config(
    page_title="Stock Analysis HUD",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed"
)

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
    border-radius: 8px; padding: 12px 8px; text-align: center;
  }
  .sig-bull { background: #0D2818; border: 1px solid #00FF8830; }
  .sig-bear { background: #2D1015; border: 1px solid #FF6B6B30; }
  .sig-neut { background: #251800; border: 1px solid #FACC1530; }
  .sig-label { font-size: 10px; color: #94A3B8; letter-spacing: 1px; text-transform: uppercase; margin-bottom: 5px; font-weight: 600; }
  .info-link { color: #4A6080; text-decoration: none; font-size: 10px; margin-left: 4px; opacity: 0.6; }
  .info-link:hover { color: #5EEAD4; opacity: 1; }
  .sig-val-g { font-size: 14px; font-weight: 700; color: #00FF88; font-family: 'JetBrains Mono', monospace; }
  .sig-val-r { font-size: 14px; font-weight: 700; color: #FF6B6B; font-family: 'JetBrains Mono', monospace; }
  .sig-val-y { font-size: 14px; font-weight: 700; color: #FACC15; font-family: 'JetBrains Mono', monospace; }

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
def get_claude_analysis(ticker, info, df, signals, score, fibs, news_items, market_ctx):
    """Build prompt and call Claude. All data passed as explicit args — no f-string surprises."""
    row   = df.iloc[-1]
    prev  = df.iloc[-2]
    close = float(row['Close'])
    cur   = "CA$" if ticker.endswith(".TO") else "$"

    # Last 60 closes for pattern detection
    last60 = ",".join(str(round(float(x), 2)) for x in df['Close'].tail(60))

    # Last 5 OHLCV
    last5_parts = []
    for i in range(-5, 0):
        r = df.iloc[i]
        last5_parts.append(
            f"O:{float(r['Open']):.2f} H:{float(r['High']):.2f} "
            f"L:{float(r['Low']):.2f} C:{float(r['Close']):.2f} V:{float(r['Volume']):.0f}"
        )
    last5 = " | ".join(last5_parts)

    # MA context
    above_below = lambda v, ma: "ABOVE" if close > float(row[ma]) else "BELOW"
    ma_ctx = (f"Price vs MAs: {above_below(close,'MA20')} 20MA | "
              f"{above_below(close,'MA50')} 50MA | "
              f"{above_below(close,'MA200')} 200MA")

    # Daily change
    prev_close = float(prev['Close'])
    chg_abs = close - prev_close
    chg_pct = (chg_abs / prev_close * 100) if prev_close else 0

    # Market context
    spy_sig = market_ctx.get('spy_signal', 'Unknown')
    qqq_sig = market_ctx.get('qqq_signal', 'Unknown')
    dia_sig = market_ctx.get('dia_signal', 'Unknown')
    spy_chg = market_ctx.get('spy_1m', 0)
    qqq_chg = market_ctx.get('qqq_1m', 0)
    dia_chg = market_ctx.get('dia_1m', 0)

    # News headlines — plain string, NOT inside f-string braces
    if news_items:
        headlines_text = "\n".join(f"- {n.get('title','')}" for n in news_items[:5])
    else:
        headlines_text = "No recent news available"

    # Fundamentals
    mc  = info.get('marketCap', 0) or 0
    pe  = info.get('trailingPE', 'N/A')
    fpe = info.get('forwardPE', 'N/A')
    eps = info.get('trailingEps', 'N/A')
    pb  = info.get('priceToBook', 'N/A')
    rev_g = info.get('revenueGrowth', 'N/A')
    eps_g = info.get('earningsGrowth', 'N/A')
    earn_date = info.get('earningsDate', 'Unknown')
    sector = info.get('sector', 'N/A')
    h52 = info.get('fiftyTwoWeekHigh', 0) or 0
    l52 = info.get('fiftyTwoWeekLow', 0) or 0

    # Build prompt with NO f-string JSON schemas — use string concatenation for JSON examples
    prompt = (
        f"You are an expert stock market analyst. Analyze {ticker}.\n"
        "Return ONLY raw JSON — no markdown, no backticks, no explanation.\n\n"
        f"TECHNICAL DATA:\n"
        f"{ma_ctx}\n"
        f"Close: {close:.2f} | Change: {chg_abs:.2f} ({chg_pct:.2f}%)\n"
        f"20MA: {float(row['MA20']):.2f} | 50MA: {float(row['MA50']):.2f} | "
        f"200MA: {float(row['MA200']):.2f} | 100MA: {float(row['MA100']):.2f}\n"
        f"RSI: {float(row['RSI']):.1f} | ATR%: {float(row['ATRPct'])*100:.1f}% | Score: {score}/10\n"
        f"MACD: {float(row['MACD']):.3f} | Signal: {float(row['MACDSig']):.3f} | "
        f"Hist: {float(row['MACDHist']):.3f}\n"
        f"OBV: {'Rising' if float(row['OBV']) > float(prev['OBV']) else 'Falling'} | "
        f"Vol vs avg: {float(row['VolTrend']):.2f}x\n"
        f"52W High: {h52:.2f} | 52W Low: {l52:.2f}\n"
        f"Fib 38.2%: {fibs[0]:.2f} | 50%: {fibs[1]:.2f} | 61.8%: {fibs[2]:.2f}\n\n"
        f"LAST 60 DAILY CLOSES (oldest to newest, use for pattern detection):\n"
        f"{last60}\n\n"
        f"LAST 5 SESSIONS OHLCV (for candlestick patterns):\n"
        f"{last5}\n\n"
        f"MARKET CONTEXT:\n"
        f"S&P500: {spy_sig} ({spy_chg:+.1f}% last month)\n"
        f"NASDAQ: {qqq_sig} ({qqq_chg:+.1f}% last month)\n"
        f"DOW:    {dia_sig} ({dia_chg:+.1f}% last month)\n\n"
        f"RECENT NEWS HEADLINES:\n"
        f"{headlines_text}\n\n"
        f"FUNDAMENTALS:\n"
        f"Market Cap: {fmt_cap(mc)} | P/E: {pe} | Fwd P/E: {fpe}\n"
        f"EPS: {eps} | P/B: {pb}\n"
        f"Revenue Growth: {rev_g} | Earnings Growth: {eps_g}\n"
        f"Next Earnings: {earn_date} | Sector: {sector}\n\n"
        "INSTRUCTIONS:\n"
        "CHART PATTERNS — analyze the 60 closes carefully:\n"
        "- Identify patterns: Cup&Handle, Head&Shoulders, Flags, Triangles, Wedges, Double Top/Bottom\n"
        "- description: explain WHAT you see in the price data — mention actual price levels and closes\n"
        "- confidence_reason: explain WHY that score. 40-55%=early/weak, 56-70%=developing, 71-85%=confirmed, 86%+=textbook\n"
        "- still_valid: true if price is still within the pattern structure, false if already broken or resolved\n"
        "- validity_note: one sentence — is the signal still actionable or already played out?\n"
        "- Only include patterns with confidence >40%. Return empty array if nothing clear.\n\n"
        "CANDLESTICK PATTERNS — use last 5 OHLCV:\n"
        "- meaning: explain what the candle body/wick structure tells you about buyer vs seller pressure\n\n"
        "OTHER:\n"
        "- Classify each news headline as bullish/bearish/neutral for this specific stock\n"
        "- ALWAYS return trend_short/medium/long — NEVER return N/A\n"
        "- Use market context for business cycle phase\n\n"
        "Return ONLY this JSON (no markdown, no extra text):\n"
        '{"verdict":"DAY TRADE|SWING TRADE|INVEST|AVOID|MULTI-TIMEFRAME",'
        '"confidence":"Low|Medium|High",'
        '"risk":"Low|Medium|High|Very High",'
        '"risk_reason":"one sentence",'
        '"entry_low":0,"entry_high":0,'
        '"vwap":0,"ema100":0,'
        '"support1":0,"support1_label":"label",'
        '"support2":0,"support2_label":"label",'
        '"support3":0,"support3_label":"label",'
        '"resistance1":0,"resistance1_label":"label",'
        '"resistance2":0,"resistance2_label":"label",'
        '"resistance3":0,"resistance3_label":"label",'
        '"reasons_bull":["r1","r2","r3"],'
        '"reasons_bear":["r1","r2"],'
        '"summary":"2-3 sentence plain English analysis",'
        '"day_trade_note":"one sentence",'
        '"swing_note":"one sentence",'
        '"invest_note":"one sentence",'
        '"pb_ratio":0,"peg_ratio":0,'
        '"eps_growth_yoy":0,"rev_growth_yoy":0,'
        '"earnings_date":"MMM DD YYYY","earnings_days":0,'
        '"last_earnings_beat":"Beat +X% or Missed X%",'
        '"sector":"sector name",'
        '"chart_patterns":[{"name":"pattern name","type":"bullish|bearish|neutral","confidence":70,"description":"what you see in the price data and key levels involved","confidence_reason":"why this confidence score — what confirms or weakens it","still_valid":true,"validity_note":"is price still within pattern or has it broken","target_pct":10,"target_price":0}],'
        '"candle_patterns":[{"name":"pattern","type":"bullish|bearish|neutral","session":"Today|Yesterday|2d ago","meaning":"one sentence"}],'
        '"trend_short":"Uptrend|Downtrend|Sideways","trend_short_desc":"one sentence",'
        '"trend_medium":"Uptrend|Downtrend|Sideways","trend_medium_desc":"one sentence",'
        '"trend_long":"Uptrend|Downtrend|Sideways","trend_long_desc":"one sentence",'
        '"pattern_bias":"Bullish|Bearish|Neutral","pattern_bias_desc":"one sentence",'
        '"cycle_phase":"Early|Mid|Late|Recession",'
        '"cycle_desc":"one sentence",'
        '"market_risk":"Low|Moderate|High|Extreme",'
        '"market_risk_desc":"one sentence",'
        '"news_sentiment":[{"headline":"title","sentiment":"bullish|bearish|neutral","reason":"one sentence"}]}'
    )

    try:
        client = anthropic.Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2500,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = msg.content[0].text.strip()
        # Strip accidental markdown
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
    url = INFO_LINKS.get(label, "https://www.investopedia.com/search?q=" + label.replace(" ","+"))
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
    # ── Sidebar: cache controls ──────────────────────────────
    with st.sidebar:
        st.markdown("### ⚙️ Controls")
        if st.button("🔄 Clear Cache & Refresh", use_container_width=True):
            st.cache_data.clear()
            for k in list(st.session_state.keys()):
                del st.session_state[k]
            st.success("Cache cleared!")
            st.rerun()
        st.markdown("---")
        st.markdown("*Cache TTL: 60 min*")
        fmp_active = bool(st.secrets.get("FMP_API_KEY",""))
        st.markdown(f"Data: {'🟢 FMP' if fmp_active else '🟡 yfinance'}")

    if 'analysis' not in st.session_state:
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            st.markdown("<br>", unsafe_allow_html=True)
            st.markdown('<div style="text-align:center;font-size:12px;color:#4A6080;letter-spacing:3px;text-transform:uppercase;margin-bottom:16px;">Stock Analysis · AI HUD</div>', unsafe_allow_html=True)

            tab1, tab2 = st.tabs(["📊 Stock Analysis", "🎙️ Earnings Call Analyzer"])

            with tab1:
                st.markdown('<div style="text-align:center;font-size:24px;font-weight:800;color:#F1F5F9;margin-bottom:6px;">Enter a ticker</div>', unsafe_allow_html=True)
                st.markdown('<div style="text-align:center;font-size:13px;color:#4A6080;margin-bottom:16px;">Type any symbol — a dropdown will guide you</div>', unsafe_allow_html=True)

                fmp_key_lp = st.secrets.get("FMP_API_KEY", "")

                # ── Ticker input — NO form so it updates on every keystroke ──
                ticker_in = st.text_input("", placeholder="NVDA",
                                          key="ticker_input",
                                          label_visibility="collapsed")
                ticker_upper = ticker_in.strip().upper() if ticker_in else ""

                analyze_clicked = st.button("Analyze →", type="primary",
                                            use_container_width=True)

                # ── Enter key detection ────────────────────────────
                # Streamlit reruns on Enter but doesn't set analyze_clicked.
                # If the ticker value is the same as the last rerun, Enter was pressed.
                prev_val = st.session_state.get('_prev_ticker_val', '')
                st.session_state['_prev_ticker_val'] = ticker_upper
                enter_pressed = (ticker_upper != '' and ticker_upper == prev_val and not analyze_clicked)
                should_analyze = analyze_clicked or enter_pressed

                # ── Live dropdown ─────────────────────────────────
                selected_ticker = None

                def show_dropdown(rows):
                    """Render a dropdown list. rows = list of dicts with sym/name/exch/curr/key."""
                    st.markdown(
                        '<div style="background:#0D1B2A;border:1px solid #14B8A6;'
                        'border-radius:8px;margin-top:6px;overflow:hidden;">'
                        '<div style="padding:5px 14px;font-size:10px;color:#5EEAD4;'
                        'letter-spacing:1.5px;background:#071420;">'
                        '▼ SELECT EXCHANGE / SHARE CLASS</div>',
                        unsafe_allow_html=True)
                    for row in rows:
                        rl, rr = st.columns([5, 1])
                        with rl:
                            st.markdown(
                                f'<div style="padding:7px 14px;border-bottom:1px solid #111827;">'
                                f'<span style="font-family:monospace;font-weight:800;color:#00FF88;font-size:14px;">{row["sym"]}</span>'
                                f'&nbsp;&nbsp;<span style="font-size:12px;color:#CBD5E1;">{row["name"]}</span>'
                                f'&nbsp;&nbsp;<span style="font-size:11px;color:#5EEAD4;">{row["exch"]} · {row["curr"]}</span>'
                                f'</div>', unsafe_allow_html=True)
                        with rr:
                            if st.button("▶ Analyze", key=row["key"]):
                                return row["sym"]
                    st.markdown("</div>", unsafe_allow_html=True)
                    return None

                if ticker_upper:
                    # ── STEP 1: hardcoded MULTI_LISTED — always checked first ──
                    # Handles BRK→BRK-A/BRK-B, RY→NYSE/TSX, SHOP→NYSE/TSX etc.
                    if ticker_upper in MULTI_LISTED:
                        rows = [{"sym": o["ticker"], "name": o["name"],
                                 "exch": o["exchange"], "curr": o["currency"],
                                 "key": f'ml_{o["ticker"]}'}
                                for o in MULTI_LISTED[ticker_upper]]
                        result = show_dropdown(rows)
                        if result:
                            selected_ticker = result

                    # ── STEP 2: FMP live search for everything else ──
                    elif fmp_key_lp:
                        results = search_ticker_fmp(ticker_upper, fmp_key_lp)
                        if results:
                            rows = [{"sym":  r.get("symbol",""),
                                     "name": r.get("name","")[:42],
                                     "exch": r.get("exchangeShortName",""),
                                     "curr": r.get("currency","USD"),
                                     "key":  f'fmp_{r.get("symbol","")}_{r.get("exchangeShortName","")}'}
                                    for r in results[:10] if r.get("symbol","")]
                            result = show_dropdown(rows)
                            if result:
                                selected_ticker = result
                        elif should_analyze:
                            # FMP has nothing → try the ticker directly (e.g. NPK.TO typed in full)
                            selected_ticker = ticker_upper

                    # ── STEP 3: No FMP key → direct run ──
                    else:
                        if should_analyze:
                            selected_ticker = ticker_upper

                # ── Run analysis on selection ─────────────────────
                if selected_ticker:
                    run_analysis(selected_ticker)

                st.markdown('<div style="text-align:center;font-size:11px;color:#243348;margin-top:20px;">US: AAPL · NVDA · PLTR &nbsp;|&nbsp; TSX: add .TO (RY.TO) &nbsp;|&nbsp; London: add .L</div>', unsafe_allow_html=True)

            with tab2:
                render_earnings_analyzer()

        return

    render_hud()



def run_analysis(ticker):
    prog = st.empty()
    # Session-level cache: if same ticker already in session, reuse data instantly
    # Session cache disabled for now — was serving stale data
    # Will re-enable once data quality is stable
    cache_key = f"_ticker_cache_{ticker.upper()}"
    # All variables initialized BEFORE any try block
    analyst_data  = {'buy':0,'hold':0,'sell':0,'target':0,'target_low':0,
                     'target_high':0,'num_analysts':0,'rec_mean':0,'rec_key':'N/A'}
    earnings_hist = []
    insider_data  = []
    news_items    = []
    vol_data      = {'hv_30':0,'hv_90':0,'bb_upper':0,'bb_lower':0,
                     'bb_mid':0,'bb_width':0,'bb_pct':50,'iv':0,'iv_vs_hv':0}
    market_ctx    = {'spy_signal':'Unknown','qqq_signal':'Unknown','dia_signal':'Unknown',
                     'spy_1m':0,'qqq_1m':0,'dia_1m':0}
    earn_date_str = 'Unknown'
    days_to_earn  = 0

    try:
        # ── 1. Fetch all data (cached 15 min) ──────────────────
        prog.info(f"⏳ Fetching data for {ticker}...")
        fmp_key = st.secrets.get("FMP_API_KEY", "")
        data  = fetch_ticker_data(ticker, fmp_key, _v=13)
        df    = data['df']
        info  = data['info']

        if df.empty or len(df) < 50:
            prog.empty()
            st.error(f"No data found for {ticker}. Check the ticker symbol.")
            return

        df = calculate_indicators(df)
        if len(df) < 20:
            prog.empty()
            st.error("Not enough data to calculate indicators.")
            return

        row  = df.iloc[-1]
        prev = df.iloc[-2]
        signals, score = calc_signals(row)

        # Fibonacci
        h52  = float(info.get('fiftyTwoWeekHigh', df['High'].tail(252).max()))
        l52  = float(info.get('fiftyTwoWeekLow',  df['Low'].tail(252).min()))
        rng  = h52 - l52
        fibs = [h52 - rng*0.382, h52 - rng*0.500, h52 - rng*0.618]

        # ── 2. Market context (cached 15 min, shared) ──────────
        prog.info("⏳ Fetching market context...")
        market_ctx = fetch_market_context()

        # ── 3. News ────────────────────────────────────────────
        prog.info(f"⏳ Processing news for {ticker}...")
        try:
            for item in (data['news'] or [])[:5]:
                try:
                    title = (item.get('title') or
                             item.get('content', {}).get('title', ''))
                    pub   = (item.get('publisher') or
                             item.get('content', {}).get('provider', {}).get('displayName', ''))
                    link  = (item.get('link') or
                             item.get('content', {}).get('canonicalUrl', {}).get('url', ''))
                    if title:
                        news_items.append({'title': str(title), 'publisher': str(pub), 'link': str(link)})
                except:
                    pass
        except:
            pass

        # ── 5. Claude AI ───────────────────────────────────────
        prog.info(f"🤖 Running AI analysis for {ticker}... (10-15 sec)")
        analysis = get_claude_analysis(ticker, info, df, signals, score, fibs, news_items, market_ctx)
        if 'error' in analysis:
            prog.empty()
            st.error(f"Claude API error: {analysis['error']}")
            return

        # ── 6. Analyst ratings ──────────────────────────────────
        # Uses data already fetched in fetch_ticker_data — no extra FMP calls
        prog.info("⏳ Processing analyst & earnings data...")
        target_mean = target_low = target_high = 0.0
        num_ana = 0
        rec_mean = 0.0
        rec_key = ''
        buy_cnt = hold_cnt = sell_cnt = 0

        # Source 1: data['analyst_targets'] — from FMP price-target-consensus in cache
        try:
            at = data.get('analyst_targets') or {}
            if at and isinstance(at, dict):
                target_mean = float(at.get('mean') or 0)
                target_high = float(at.get('high') or 0)
                target_low  = float(at.get('low')  or 0)
        except: pass

        # Source 2: data['rec_summary'] — from FMP analyst-recommendations in cache
        try:
            rs = data.get('rec_summary')
            if rs is not None and not (hasattr(rs,'empty') and rs.empty):
                r = rs.iloc[0]
                buy_cnt  = int((r.get('strongBuy',  0) or 0) + (r.get('buy',  0) or 0))
                hold_cnt = int(r.get('hold', 0) or 0)
                sell_cnt = int((r.get('strongSell', 0) or 0) + (r.get('sell', 0) or 0))
                num_ana  = buy_cnt + hold_cnt + sell_cnt
        except: pass

        # Source 3: info dict — populated by FMP profile call in fetch_ticker_data
        if target_mean == 0:
            target_mean = float(info.get('targetMeanPrice') or info.get('targetPrice') or 0)
            target_low  = float(info.get('targetLowPrice')  or 0)
            target_high = float(info.get('targetHighPrice') or 0)
        if num_ana == 0:
            num_ana = int(info.get('numberOfAnalystOpinions') or info.get('numAnalystOpinions') or 0)

        # Source 4: yfinance fallback (only if FMP returned nothing)
        if target_mean == 0:
            try:
                _yt = yf.Ticker(ticker.replace('BRK.B','BRK-B').replace('BRK.A','BRK-A'))
                apt = _yt.analyst_price_targets
                if apt and isinstance(apt, dict):
                    target_mean = float(apt.get('mean') or apt.get('current') or 0)
                    target_low  = float(apt.get('low')  or 0)
                    target_high = float(apt.get('high') or 0)
            except: pass
        if buy_cnt == 0:
            try:
                _yt = yf.Ticker(ticker.replace('BRK.B','BRK-B').replace('BRK.A','BRK-A'))
                yrs = _yt.recommendations_summary
                if yrs is not None and not yrs.empty:
                    r = yrs.iloc[0]
                    buy_cnt  = int((r.get('strongBuy',  r.get('strong_buy',  0)) or 0) + (r.get('buy', 0) or 0))
                    hold_cnt = int(r.get('hold', 0) or 0)
                    sell_cnt = int((r.get('strongSell', r.get('strong_sell', 0)) or 0) + (r.get('sell', 0) or 0))
                    num_ana  = max(num_ana, buy_cnt + hold_cnt + sell_cnt)
            except: pass

        # Build rec_key from counts or rec_mean
        rec_mean = float(info.get('recommendationMean') or 0)
        rec_key  = str(info.get('recommendationKey') or '')
        if not rec_key:
            if rec_mean:
                if rec_mean <= 1.5:   rec_key = 'strong-buy'
                elif rec_mean <= 2.5: rec_key = 'buy'
                elif rec_mean <= 3.5: rec_key = 'hold'
                elif rec_mean <= 4.5: rec_key = 'sell'
                else:                 rec_key = 'strong-sell'
            elif buy_cnt + hold_cnt + sell_cnt > 0:
                total = buy_cnt + hold_cnt + sell_cnt
                buy_pct = buy_cnt / total
                if buy_pct >= 0.6:   rec_key = 'buy'
                elif buy_pct >= 0.4: rec_key = 'hold'
                else:                rec_key = 'sell'

        if target_mean > 0 or buy_cnt > 0 or num_ana > 0:
            analyst_data = {
                'buy': buy_cnt, 'hold': hold_cnt, 'sell': sell_cnt,
                'target': target_mean, 'target_low': target_low,
                'target_high': target_high, 'num_analysts': num_ana,
                'rec_mean': rec_mean, 'rec_key': rec_key or 'N/A',
            }

        # ── 7. Earnings history ──────────────────────────────────
        # FMP earn_hist is most reliable (clean columns, already 4 rows)
        # Fall back to yfinance endpoints if FMP empty
        eh = data.get('earn_hist')
        if eh is None or (hasattr(eh,'empty') and eh.empty):
            try:
                _rt = yf.Ticker(ticker.replace('BRK.B','BRK-B').replace('BRK.A','BRK-A'))
                eh  = _rt.earnings_history
            except: pass
        if eh is None or (hasattr(eh,'empty') and eh.empty):
            try:
                _rt = yf.Ticker(ticker.replace('BRK.B','BRK-B').replace('BRK.A','BRK-A'))
                _ed = _rt.earnings_dates
                if _ed is not None and not _ed.empty:
                    # earnings_dates has past AND future rows — filter to past only
                    _ed = _ed[_ed.index <= pd.Timestamp.now()]
                    if not _ed.empty:
                        eh = _ed
            except: pass
        try:
            if eh is not None and not eh.empty:
                for _, er in eh.head(4).iterrows():
                    # Handle both FMP column names (epsEstimate/epsActual/period)
                    # and yfinance column names (EPS Estimate/Reported EPS/Surprise(%))
                    est = float(er.get('epsEstimate',  er.get('EPS Estimate',  er.get('estimate', 0))) or 0)
                    act = float(er.get('epsActual',    er.get('Reported EPS',  er.get('actual',   0))) or 0)
                    surp_raw = er.get('surprisePercent', er.get('Surprise(%)', er.get('surprise', None)))
                    if surp_raw is not None:
                        sv = float(surp_raw or 0)
                        # FMP returns decimal (0.052 = 5.2%), yfinance returns percent (5.2)
                        surp = sv * 100 if abs(sv) <= 2 else sv
                    else:
                        surp = ((act - est) / abs(est) * 100) if est != 0 else 0
                    qtr = str(er.get('period', er.get('Date', er.name if hasattr(er, 'name') else '')))[:10]
                    if act != 0 or est != 0:
                        earnings_hist.append({'quarter': qtr, 'estimate': est,
                                              'actual': act, 'surprise': surp, 'beat': surp > 0})
        except:
            pass

        # ── 8. Insider trading — multiple sources ─────────────
        try:
            ins = data.get('insider')
            if ins is None or (hasattr(ins,'empty') and ins.empty):
                try:
                    _rt2 = yf.Ticker(ticker.replace('BRK.B','BRK-B'))
                    ins  = _rt2.insider_transactions
                except: pass
            if ins is None or (hasattr(ins,'empty') and ins.empty):
                try:
                    _rt2 = yf.Ticker(ticker.replace('BRK.B','BRK-B'))
                    ins  = _rt2.insider_purchases
                except: pass
            if ins is not None and not ins.empty:
                for _, ri in ins.head(5).iterrows():
                    # yfinance field names vary by version — try all known variants
                    shares = int(ri.get('Shares',      ri.get('shares', 0)) or 0)
                    val    = float(ri.get('Value',     ri.get('value', 0)) or 0)
                    text   = str(ri.get('Text',        ri.get('text', '')) or '')
                    trans  = str(ri.get('Transaction', ri.get('transaction', '')) or '')
                    name   = str(ri.get('Insider',     ri.get('filerName', ri.get('insider', ''))) or '')
                    role   = str(ri.get('Position',    ri.get('filerRelation', '')) or '')
                    date_i = str(ri.get('Date',        ri.get('startDate', '')) or '')
                    combined = (text + trans).lower()
                    # Check sell keywords first — sells also have positive share counts
                    is_sell = any(w in combined for w in ('sale', 'sell', 'dispose', 'disposed'))
                    is_buy  = (not is_sell and
                               any(w in combined for w in ('purchase', 'buy', 'acquisition', 'grant', 'award', 'exercise')))
                    if not is_buy and not is_sell:
                        is_buy = shares > 0  # last resort fallback
                    if name.strip():
                        insider_data.append({
                            'name': name[:22], 'role': role[:22],
                            'type': 'BUY' if is_buy else 'SELL',
                            'shares': abs(shares), 'value': abs(val),
                            'date': str(date_i)[:10]
                        })
        except:
            pass

        # ── 9. Volatility ──────────────────────────────────────
        try:
            lr    = np.log(df['Close'] / df['Close'].shift(1)).dropna()
            hv30  = float(lr.tail(30).std() * np.sqrt(252) * 100)
            hv90  = float(lr.tail(90).std() * np.sqrt(252) * 100) if len(lr) >= 90 else hv30
            bb_m  = float(df['Close'].tail(20).mean())
            bb_s  = float(df['Close'].tail(20).std())
            bb_u  = bb_m + 2 * bb_s
            bb_l  = bb_m - 2 * bb_s
            bb_w  = (bb_u - bb_l) / bb_m * 100
            # IV from cached data (fetched in fetch_ticker_data)
            iv_from_info = float(info.get('impliedVolatility', 0) or 0) * 100
            iv = data.get('iv', 0) or iv_from_info
            cnow  = float(df['Close'].iloc[-1])
            bb_p  = (cnow - bb_l) / (bb_u - bb_l) * 100 if bb_u != bb_l else 50
            vol_data = {'hv_30': hv30, 'hv_90': hv90, 'bb_upper': bb_u, 'bb_lower': bb_l,
                        'bb_mid': bb_m, 'bb_width': bb_w, 'bb_pct': bb_p, 'iv': iv,
                        'iv_vs_hv': iv / hv30 if hv30 > 0 else 0}
        except:
            pass

        # ── 10. Earnings date — try multiple sources ───────────
        earn_date_str = analysis.get('earnings_date', 'Unknown') or 'Unknown'
        days_to_earn  = 0
        ned = None

        def parse_earn_date(val):
            """Convert any earnings date value to a clean future Timestamp or None."""
            try:
                if val is None:
                    return None
                # Unix int/float (seconds)
                if isinstance(val, (int, float)) and val > 1e9:
                    ts = pd.Timestamp(val, unit='s')
                else:
                    ts = pd.Timestamp(val)
                if ts.tzinfo is not None:
                    ts = ts.tz_convert(None)
                # Only return if it's a future date
                return ts if ts > pd.Timestamp.now() else None
            except:
                return None

        # Source 1: raw.calendar (most reliable)
        try:
            cal = data.get('calendar')
            if cal is not None:
                if isinstance(cal, dict):
                    # Dict format: {'Earnings Date': [ts1, ts2], ...}
                    ed = cal.get('Earnings Date', cal.get('earningsDate'))
                    if ed is not None:
                        ned = parse_earn_date(ed[0] if isinstance(ed, (list, tuple)) else ed)
                elif hasattr(cal, 'columns') and 'Earnings Date' in cal.columns:
                    # DataFrame format: columns are field names, rows are values
                    ned = parse_earn_date(cal['Earnings Date'].iloc[0])
                elif hasattr(cal, 'index') and 'Earnings Date' in cal.index:
                    # Transposed DataFrame format
                    ned = parse_earn_date(cal.loc['Earnings Date'].iloc[0])
        except:
            pass

        # Source 2: info dict
        if ned is None:
            try:
                for key in ['earningsDate', 'nextEarningsDate', 'earningsTimestamp']:
                    val = info.get(key)
                    if val:
                        # List format
                        if isinstance(val, (list, tuple)):
                            val = val[0]
                        ned = parse_earn_date(val)
                        if ned:
                            break
            except:
                pass

        # Source 3: earnings_dates DataFrame (newest yfinance)
        if ned is None:
            try:
                ed_df = data.get('earn_dates')
                if ed_df is not None and not ed_df.empty:
                    future = ed_df[ed_df.index > pd.Timestamp.now()]
                    if not future.empty:
                        ts = future.index[-1]
                        ned = parse_earn_date(ts)
            except:
                pass

        if ned is not None:
            days_to_earn  = (ned - pd.Timestamp.now()).days
            earn_date_str = ned.strftime("%b %d, %Y")

        # ── Store in session state ─────────────────────────────
        prog.empty()
        st.session_state.analysis      = analysis
        st.session_state.df            = df
        st.session_state.info          = info
        st.session_state.ticker        = ticker
        st.session_state.signals       = signals
        st.session_state.score         = score
        st.session_state.fibs          = fibs
        st.session_state.row           = row
        st.session_state.prev          = prev
        st.session_state.market_ctx    = market_ctx
        st.session_state.analyst_data  = analyst_data
        st.session_state.earnings_hist = earnings_hist
        st.session_state.insider_data  = insider_data
        st.session_state.news_items    = news_items
        st.session_state.vol_data      = vol_data
        st.session_state.earn_date_str = earn_date_str
        st.session_state.days_to_earn  = days_to_earn
        st.rerun()

    except Exception as e:
        prog.empty()
        err_str = str(e)
        if "429" in err_str or "Too Many Requests" in err_str or "rate" in err_str.lower():
            st.error("⏳ Yahoo Finance rate limit hit. Please wait 30 seconds and try again. This is a Yahoo-side limit, not a bug.")
        else:
            import traceback
            st.error(f"Error: {err_str}")
            st.code(traceback.format_exc())


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
    # If company name is still just the ticker symbol, check MULTI_LISTED for known names
    if company == ticker or company == ticker.replace('-','.'):
        for key, opts in MULTI_LISTED.items():
            for opt in opts:
                if opt['ticker'].upper() == ticker.upper():
                    company = opt['name']
                    break
    sector  = info.get('sector', a.get('sector',''))
    exchange = 'TSX' if ticker.endswith('.TO') else 'LSE' if ticker.endswith('.L') else 'NYSE / NASDAQ'

    # Back button
    if st.button("← New ticker"):
        for k in ['analysis','df','info','ticker','signals','score','fibs','row','prev','_prev_ticker_val']:
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
        <div style="margin-top:5px;">{"<span style='background:#0A3020;border:1px solid #00FF88;border-radius:4px;padding:2px 8px;font-size:10px;color:#00FF88;letter-spacing:1px;'>&#x26A1; FMP</span>" if st.secrets.get("FMP_API_KEY","") else "<span style='background:#2A1500;border:1px solid #FACC15;border-radius:4px;padding:2px 8px;font-size:10px;color:#FACC15;letter-spacing:1px;'>&#x26A0; yfinance — add FMP key</span>"}</div>
      </div>
    </div>''', unsafe_allow_html=True)

    # ── ZONE 2: STATUS BAR ───────────────────────────────────
    # Detect user timezone accurately for any visitor worldwide.
    # Flow: JS reads browser tz → sets ?tz= query param → page reloads once → Python reads it.
    # After that one reload the param is always present and accurate.
    import zoneinfo, streamlit.components.v1 as components
    try:
        params  = st.query_params
        user_tz = params.get("tz", "")
        if not user_tz:
            # No tz param yet — inject JS that sets it and reloads the page once
            components.html("""
                <script>
                const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
                const url = new URL(window.parent.location.href);
                if (!url.searchParams.get('tz')) {
                    url.searchParams.set('tz', tz);
                    window.parent.location.replace(url.toString());
                }
                </script>
            """, height=0)
            user_tz = "UTC"  # shown only for the split second before reload
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

    # ── ZONE 3: VERDICT + SCORE + AI SUMMARY ────────────────
    bull_count = sum(1 for k,v in signals.items() if v['bull'])
    score_meaning = ("Strong bullish setup" if score >= 8 else
                     "Moderately bullish"   if score >= 6 else
                     "Mixed signals"        if score >= 4 else
                     "Moderately bearish"   if score >= 2 else
                     "Strong bearish setup")
    bull_names = " · ".join(signals[k]["label"] for k in signals if signals[k]["bull"]) or "None"
    bear_names = " · ".join(signals[k]["label"] for k in signals if not signals[k]["bull"]) or "None"
    c1, c2 = st.columns([1.2, 0.8])
    with c1:
        st.markdown(f"""
        <div class="verdict-card" style="background:{vc['bg']};border-left-color:{vc['border']};">
          <div class="verdict-label" style="color:{vc['color']};">AI Verdict</div>
          <div class="verdict-value" style="color:{vc['color']};">{a.get('verdict','')}</div>
          <div class="verdict-meta">Confidence: {a.get('confidence','')} &nbsp;·&nbsp; Risk: {a.get('risk','')}</div>
          <div class="verdict-note" style="color:{vc['color']};">{a.get('risk_reason','')}</div>
        </div>""", unsafe_allow_html=True)
    with c2:
        st.markdown(f"""
        <div class="score-card">
          <div class="score-label">Signal Score</div>
          <div><span class="score-num" style="color:{score_col};">{score}</span><span class="score-denom">/10</span></div>
          <div class="score-bar-wrap">
            <div class="score-bar-track"></div>
            <div class="score-bar-fill" style="width:{score*10}%;"></div>
          </div>
          <div class="score-markers"><span>AVOID</span><span>NEUTRAL</span><span>STRONG</span></div>
          <div style="font-size:12px;color:{score_col};font-weight:700;margin-top:7px;">{score_meaning}</div>
          <div style="margin-top:6px;padding-top:6px;border-top:1px solid #243348;">
            <div style="font-size:10px;color:#00FF88;margin-bottom:3px;line-height:1.5;">&#9650; {bull_names}</div>
            <div style="font-size:10px;color:#FF6B6B;line-height:1.5;">&#9660; {bear_names}</div>
          </div>
        </div>""", unsafe_allow_html=True)
    # ── ZONE 3b: AI SUMMARY (full width) ────────────────────
    st.markdown(f"""
    <div style="background:#1A2232;border:1px solid #14B8A6;border-top:2px solid #14B8A6;
                border-radius:8px;padding:14px 18px;margin-top:6px;">
      <div style="font-size:10px;color:#5EEAD4;letter-spacing:2px;text-transform:uppercase;
                  margin-bottom:8px;font-weight:600;">AI Summary</div>
      <div style="font-size:13px;color:#E2E8F0;line-height:1.8;">{a.get('summary','')}</div>
    </div>""", unsafe_allow_html=True)

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
        # RSI bar — identical structure to 52W range bar
        rsi_val = float(row['RSI'])
        rsi_col = "#FF6B6B" if rsi_val > 70 else "#00FF88" if rsi_val < 30 else "#FACC15"
        rsi_lbl = "Overbought" if rsi_val > 70 else "Oversold" if rsi_val < 30 else "Neutral"
        rsi_pct = int(rsi_val)
        levels_html += (
            '<div class="data-row" style="flex-direction:column;gap:6px;">'
            '<div style="display:flex;justify-content:space-between;width:100%;font-size:13px;">'
            f'<span class="data-lbl">RSI (14){info_icon("RSI (14)")}</span>'
            f'<span style="color:{rsi_col};font-weight:700;font-family:monospace;">{rsi_val:.1f} — {rsi_lbl}</span>'
            '</div>'
            '<div style="display:flex;align-items:center;gap:8px;width:100%;">'
            '<span style="font-size:11px;color:#00FF88;">0</span>'
            '<div style="flex:1;position:relative;height:6px;background:#243348;border-radius:3px;">'
            '<div style="position:absolute;left:0;top:0;width:100%;height:6px;border-radius:3px;background:linear-gradient(90deg,#00FF88 0%,#FACC15 30%,#FF6B6B 70%,#FF6B6B 100%);"></div>'
            f'<div style="position:absolute;left:{min(max(rsi_pct,2),98)}%;top:-4px;width:12px;height:12px;background:#F1F5F9;border-radius:50%;transform:translateX(-50%);border:2px solid #111827;"></div>'
            '</div>'
            '<span style="font-size:11px;color:#FF6B6B;">100</span>'
            '</div>'
            f'<div style="text-align:center;font-size:11px;color:#94A3B8;">RSI {rsi_val:.1f} · Oversold &lt;30 · Overbought &gt;70</div>'
            '</div>'
        )

        levels_html += '</div>'
        st.markdown(levels_html, unsafe_allow_html=True)

    with c2:
        st.markdown('<div class="section-header">Fundamentals & Growth</div>', unsafe_allow_html=True)
        # ── Fundamentals — comprehensive field mapping ─────────
        # yfinance changes field names across versions — try all known variants

        def _get(keys, default=0):
            """Try multiple field name variants, return first non-zero/None."""
            for k in (keys if isinstance(keys, list) else [keys]):
                v = info.get(k)
                if v is not None and v != 0 and v != '':
                    return v
            return default

        def _pct(keys, claude_key, default=0):
            """Get growth rate as %, handling decimal (0.15) or % (15.0) forms."""
            v = _get(keys, None)
            if v is None:
                v = a.get(claude_key, 0) or 0
                return float(v)  # Claude returns already as %
            v = float(v)
            return v * 100 if abs(v) <= 2 else v  # decimal → %

        pe     = float(_get(['trailingPE', 'trailingEps', 'forwardPE'], 0) or 0)
        # Re-fetch PE specifically
        pe     = float(info.get('trailingPE') or info.get('trailingP/E') or 0)
        fwd_pe = float(info.get('forwardPE')  or info.get('forwardP/E') or 0)
        pb     = float(_get(['priceToBook', 'bookValue'], 0) or a.get('pb_ratio', 0) or 0)
        peg    = float(_get(['pegRatio', 'trailingPegRatio'], 0) or a.get('peg_ratio', 0) or 0)
        mc     = float(_get(['marketCap', 'enterpriseValue'], 0) or 0)
        eps_g  = _pct(['earningsGrowth', 'earningsQuarterlyGrowth'], 'eps_growth_yoy')
        rev_g  = _pct(['revenueGrowth',  'revenueQuarterlyGrowth'], 'rev_growth_yoy')
        op_margin  = float(_get(['operatingMargins', 'operatingMargin'], 0) or 0) * (100 if abs(float(_get(['operatingMargins'], 0) or 0)) <= 1 else 1)
        profit_m   = float(_get(['profitMargins', 'netMargin'], 0) or 0) * (100 if abs(float(_get(['profitMargins'], 0) or 0)) <= 1 else 1)
        roe        = float(_get(['returnOnEquity', 'returnOnAssets'], 0) or 0) * (100 if abs(float(_get(['returnOnEquity'], 0) or 0)) <= 1 else 1)
        debt_eq    = float(_get(['debtToEquity', 'totalDebt'], 0) or 0)
        curr_ratio = float(_get(['currentRatio'], 0) or 0)
        div_yield  = float(_get(['dividendYield', 'trailingAnnualDividendYield'], 0) or 0) * (100 if float(_get(['dividendYield'], 0) or 0) < 1 else 1)
        short_pct  = float(_get(['shortPercentOfFloat', 'shortRatio'], 0) or 0) * (100 if float(_get(['shortPercentOfFloat'], 0) or 0) < 1 else 1)
        float_sh   = float(_get(['floatShares', 'sharesOutstanding'], 0) or 0)

        # Market cap fallback: shares × price
        if mc == 0:
            shares = float(_get(['sharesOutstanding','impliedSharesOutstanding'], 0) or 0)
            if shares > 0:
                mc = shares * close
        ma20_pct = (close/float(row['MA20'])-1)*100
        ma50_pct = (close/float(row['MA50'])-1)*100
        ma200_pct= (close/float(row['MA200'])-1)*100

        # Additional fundamentals already computed above via _get()
        rd_expense = info.get('researchAndDevelopment', 0) or 0

        funds_html = '<div class="panel-body">'
        funds_html += data_row("Market Cap",       fmt_cap(mc) if mc else "—",                    "val-w",  True)
        funds_html += data_row("P/E (Trailing)",   f"{pe:.1f}" if pe else "—",                   "val-r" if pe > 40 else "val-y" if pe > 20 else "val-g" if pe else "val-m", True)
        funds_html += data_row("P/E (Forward)",    f"{fwd_pe:.1f}" if fwd_pe else "—",           "val-r" if fwd_pe > 35 else "val-y" if fwd_pe > 18 else "val-g" if fwd_pe else "val-m", True)
        funds_html += data_row("P/B Ratio",        f"{pb:.1f}" if pb else "—",                   "val-r" if pb > 5 else "val-g" if pb else "val-m", True)
        funds_html += data_row("PEG Ratio",        f"{peg:.2f}" if peg else "—",                 "val-r" if peg > 3 else "val-y" if peg > 1.5 else "val-g" if peg else "val-m", True)
        funds_html += data_row("EPS Growth YoY",   f"{eps_g:+.1f}%" if eps_g else "—",           "val-g" if eps_g > 0 else "val-r", True)
        funds_html += data_row("Rev Growth YoY",   f"{rev_g:+.1f}%" if rev_g else "—",           "val-g" if rev_g > 0 else "val-r", True)
        funds_html += data_row("Operating Margin", f"{op_margin:.1f}%" if op_margin else "—",    "val-g" if op_margin > 15 else "val-y" if op_margin > 0 else "val-r", True)
        funds_html += data_row("Profit Margin",    f"{profit_m:.1f}%" if profit_m else "—",      "val-g" if profit_m > 10 else "val-y" if profit_m > 0 else "val-r", True)
        funds_html += data_row("Return on Equity", f"{roe:.1f}%" if roe else "—",                "val-g" if roe > 15 else "val-y" if roe > 0 else "val-r", True)
        funds_html += data_row("Debt / Equity",    f"{debt_eq:.2f}" if debt_eq else "—",         "val-r" if debt_eq > 2 else "val-y" if debt_eq > 1 else "val-g", True)
        funds_html += data_row("Current Ratio",    f"{curr_ratio:.2f}" if curr_ratio else "—",   "val-g" if curr_ratio > 1.5 else "val-y" if curr_ratio > 1 else "val-r", True)
        funds_html += data_row("Dividend Yield",   f"{div_yield:.2f}%" if div_yield else "None",   "val-g" if div_yield > 2 else "val-m", True)
        funds_html += data_row("Short % Float",    f"{short_pct:.1f}%" if short_pct else "—",    "val-r" if short_pct > 20 else "val-y" if short_pct > 10 else "val-g", True)
        funds_html += data_row("Float Shares",     fmt_cap(float_sh).replace("$","") if float_sh else "—", "val-m", True)
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
    no_options = iv == 0
    iv_label  = ("No options data" if no_options else
                 "IV > HV — big move expected" if iv_vs_hv > 1.3 else
                 "IV < HV — calm expected" if iv_vs_hv < 0.7 else
                 "IV ≈ HV — normal")

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
        # BB position bar — identical structure to 52W range bar
        bb_rows += (
            '<div class="vol-row" style="flex-direction:column;gap:6px;">'
            '<div style="display:flex;justify-content:space-between;width:100%;font-size:13px;">'
            f'<span class="vol-lbl">Price in Band</span>'
            f'<span style="color:{bb_col};font-weight:700;font-family:monospace;">{bb_pct:.0f}% — {"Oversold" if bb_pct < 20 else "Overbought" if bb_pct > 80 else "Neutral"}</span>'
            '</div>'
            '<div style="display:flex;align-items:center;gap:8px;width:100%;">'
            f'<span style="font-size:11px;color:#00FF88;">{cur}{bb_lower:.0f}</span>'
            '<div style="flex:1;position:relative;height:6px;background:#243348;border-radius:3px;">'
            '<div style="position:absolute;left:0;top:0;width:100%;height:6px;border-radius:3px;background:linear-gradient(90deg,#00FF88,#FACC15,#FF6B6B);"></div>'
            f'<div style="position:absolute;left:{min(max(int(bb_pct),2),98)}%;top:-4px;width:12px;height:12px;background:#F1F5F9;border-radius:50%;transform:translateX(-50%);border:2px solid #111827;"></div>'
            '</div>'
            f'<span style="font-size:11px;color:#FF6B6B;">{cur}{bb_upper:.0f}</span>'
            '</div>'
            f'<div style="text-align:center;font-size:11px;color:#94A3B8;">{cur}{cur_close:.2f} · Mid {cur}{bb_mid:.2f} · Width {vol_data.get("bb_width",0):.1f}%</div>'
            '</div>'
        )
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

    # ── ZONE 7b: RISK / REWARD CALCULATOR ───────────────────
    st.markdown('<div class="section-header" style="margin-top:8px;">⚡ Risk / Reward Calculator</div>', unsafe_allow_html=True)

    # All data available for pre-filling
    atr_val  = float(row['ATR'])
    verdict  = a.get('verdict', 'SWING TRADE')
    s1       = float(a.get('support1', 0) or 0)
    s2       = float(a.get('support2', 0) or 0)
    r1       = float(a.get('resistance1', 0) or 0)
    r2       = float(a.get('resistance2', 0) or 0)
    ma200    = float(row.get('MA200', close))
    entry_mid = round((float(a.get('entry_low', close)) + float(a.get('entry_high', close))) / 2, 2)
    if entry_mid == 0: entry_mid = round(close, 2)

    # ── Entry type presets ────────────────────────────────────
    # Day:   tight stop (0.5 ATR), target (1.5 ATR)
    # Swing: support-based stop (1.5 ATR fallback), nearest resistance target
    # Invest: deep stop (200MA or 3 ATR), wide target (2nd resistance or 5 ATR)
    def calc_presets(mode):
        if mode == "Day Trade":
            stp = round(entry_mid - 0.5 * atr_val, 2)
            tgt = round(entry_mid + 1.5 * atr_val, 2)
        elif mode == "Swing Trade":
            stp = round(entry_mid - 1.5 * atr_val, 2)
            if s1 > 0 and s1 < entry_mid and s1 > stp:
                stp = round(s1 - 0.01, 2)
            tgt = r1 if r1 > entry_mid else round(entry_mid + 3 * atr_val, 2)
        else:  # Invest
            stp = round(entry_mid - 3 * atr_val, 2)
            deep = min(ma200, s2 if s2 > 0 else ma200)
            if deep > 0 and deep < entry_mid and deep > stp:
                stp = round(deep - 0.01, 2)
            tgt = r2 if r2 > entry_mid else round(entry_mid + 6 * atr_val, 2)
        return max(0.01, stp), max(entry_mid + 0.01, tgt)

    # Session state for selected mode — default to AI verdict
    verdict_to_mode = {
        'DAY TRADE': 'Day Trade',
        'SWING TRADE': 'Swing Trade',
        'INVEST': 'Invest',
        'MULTI-TIMEFRAME': 'Swing Trade',
        'AVOID': 'Swing Trade',
    }
    default_mode = verdict_to_mode.get(verdict, 'Swing Trade')
    if 'rr_mode' not in st.session_state:
        st.session_state['rr_mode'] = default_mode

    # ── Header panel with AI verdict + mode selector ──────────
    vc_mode = VERDICT_COLORS.get(verdict, VERDICT_COLORS['SWING TRADE'])
    ai_label_html = f'<span style="background:{vc_mode["bg"]};border:1px solid {vc_mode["border"]};border-radius:4px;padding:2px 8px;font-size:10px;color:{vc_mode["color"]};font-weight:700;letter-spacing:1px;">AI: {verdict}</span>'

    st.markdown(f'''
    <div style="background:#1A2232;border:1px solid #243348;border-radius:0 0 8px 8px;
                padding:12px 16px 8px;">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
        <div style="font-size:11px;color:#5EEAD4;letter-spacing:1px;">
          Pre-filled from AI analysis · Adjust any value to recalculate
        </div>
        {ai_label_html}
      </div>
      <div style="font-size:10px;color:#4A6080;">
        ⚠ For educational purposes only. Not financial advice. Always do your own research before placing any trade.
      </div>
    </div>''', unsafe_allow_html=True)

    # ── Mode selector buttons ─────────────────────────────────
    btn1, btn2, btn3, _ = st.columns([1, 1, 1, 3])
    modes = ["Day Trade", "Swing Trade", "Invest"]
    mode_colors = {"Day Trade": "#FACC15", "Swing Trade": "#38BDF8", "Invest": "#00FF88"}
    for col, mode in zip([btn1, btn2, btn3], modes):
        with col:
            is_active   = st.session_state['rr_mode'] == mode
            is_ai_pick  = mode == default_mode
            label       = f"{'▶ ' if is_active else ''}{mode}{'  ← AI' if is_ai_pick else ''}"
            mc          = mode_colors[mode]
            btn_style   = f"border:2px solid {mc};background:{'#0D2020' if is_active else '#111827'};color:{mc};border-radius:6px;padding:6px 0;font-size:11px;font-weight:700;width:100%;cursor:pointer;letter-spacing:0.5px;"
            if st.button(label, key=f"rr_mode_{mode}", use_container_width=True):
                st.session_state['rr_mode'] = mode
                st.rerun()

    selected_mode = st.session_state['rr_mode']
    stop_preset, target_preset = calc_presets(selected_mode)

    # ── Inputs ────────────────────────────────────────────────
    rr_c1, rr_c2, rr_c3, rr_c4, rr_c5 = st.columns(5)
    with rr_c1:
        account_size = st.number_input("Account ($)", min_value=100.0, max_value=10000000.0,
                                        value=10000.0, step=1000.0, key="rr_account")
    with rr_c2:
        risk_pct = st.number_input("Risk (%)", min_value=0.1, max_value=10.0,
                                    value=1.0, step=0.5, key="rr_risk_pct")
    with rr_c3:
        entry_price = st.number_input(f"Entry ({cur})", min_value=0.01,
                                       value=float(entry_mid), step=0.01, key="rr_entry",
                                       format="%.2f")
    with rr_c4:
        stop_price = st.number_input(f"Stop Loss ({cur})", min_value=0.01,
                                      value=float(stop_preset), step=0.01, key=f"rr_stop_{selected_mode}",
                                      format="%.2f")
    with rr_c5:
        target_price = st.number_input(f"Target ({cur})", min_value=0.01,
                                        value=float(target_preset), step=0.01, key=f"rr_target_{selected_mode}",
                                        format="%.2f")

    # ── Calculations ──────────────────────────────────────────
    risk_per_share   = round(abs(entry_price - stop_price), 2)
    reward_per_share = round(abs(target_price - entry_price), 2)
    rr_ratio         = round(reward_per_share / risk_per_share, 2) if risk_per_share > 0 else 0
    dollar_risk      = round(account_size * (risk_pct / 100), 2)
    position_size    = int(dollar_risk / risk_per_share) if risk_per_share > 0 else 0
    position_value   = round(position_size * entry_price, 2)
    max_loss         = round(position_size * risk_per_share, 2)
    max_gain         = round(position_size * reward_per_share, 2)
    stop_pct         = round((risk_per_share / entry_price) * 100, 2) if entry_price > 0 else 0
    target_pct       = round((reward_per_share / entry_price) * 100, 2) if entry_price > 0 else 0
    rr_col           = "#00FF88" if rr_ratio >= 2 else "#FACC15" if rr_ratio >= 1 else "#FF6B6B"
    rr_label         = "Excellent" if rr_ratio >= 3 else "Good" if rr_ratio >= 2 else "Acceptable" if rr_ratio >= 1 else "Poor — avoid"

    # ── Result cards ──────────────────────────────────────────
    rc1, rc2, rc3 = st.columns(3)
    with rc1:
        st.markdown(f'''<div class="earn-bar" style="border-left-color:{rr_col};margin-top:6px;">
          <div class="earn-label">R:R Ratio</div>
          <div class="earn-val" style="color:{rr_col};font-size:26px;letter-spacing:1px;">1 : {rr_ratio}</div>
          <div style="font-size:11px;color:{rr_col};margin-top:3px;font-weight:700;">{rr_label}</div>
        </div>''', unsafe_allow_html=True)
    with rc2:
        st.markdown(f'''<div class="earn-bar" style="border-left-color:#38BDF8;margin-top:6px;">
          <div class="earn-label">Position Size</div>
          <div class="earn-val" style="color:#38BDF8;font-size:22px;">{position_size:,} <span style="font-size:13px;">shares</span></div>
          <div style="font-size:11px;color:#64748B;margin-top:3px;">Value: {cur}{position_value:,.0f}</div>
        </div>''', unsafe_allow_html=True)
    with rc3:
        st.markdown(f'''<div class="earn-bar" style="border-left-color:#818CF8;margin-top:6px;">
          <div class="earn-label">Max Loss &nbsp;/&nbsp; Max Gain</div>
          <div class="earn-val" style="color:#FF6B6B;font-size:18px;">−{cur}{max_loss:,.0f}</div>
          <div style="font-size:16px;color:#00FF88;font-weight:700;font-family:monospace;margin-top:2px;">+{cur}{max_gain:,.0f}</div>
        </div>''', unsafe_allow_html=True)

    # ── Visual Trade Diagram ──────────────────────────────────
    # Build SVG: horizontal price ladder — Stop | Entry | Target
    # with red/green shaded zones and R:R shown large in centre
    try:
        all_prices = sorted([stop_price, entry_price, target_price])
        price_min  = all_prices[0]
        price_max  = all_prices[-1]
        price_range = price_max - price_min if price_max != price_min else 1

        SVG_W, SVG_H = 900, 160
        PAD_L, PAD_R = 110, 110
        BAR_Y, BAR_H = 72, 20
        usable_w = SVG_W - PAD_L - PAD_R

        def px(price):
            return PAD_L + (price - price_min) / price_range * usable_w

        stop_x   = px(stop_price)
        entry_x  = px(entry_price)
        target_x = px(target_price)

        # Loss zone (stop → entry) and gain zone (entry → target)
        loss_x   = min(stop_x, entry_x)
        loss_w   = abs(entry_x - stop_x)
        gain_x   = min(entry_x, target_x)
        gain_w   = abs(target_x - entry_x)

        svg = f'''<svg viewBox="0 0 {SVG_W} {SVG_H}" xmlns="http://www.w3.org/2000/svg"
             style="width:100%;height:auto;background:#0E1828;border-radius:10px;
                    border:1px solid #243348;display:block;margin-top:8px;">

          <!-- Grid lines -->
          <line x1="{PAD_L}" y1="20" x2="{PAD_L}" y2="{SVG_H-20}" stroke="#1A2232" stroke-width="1"/>
          <line x1="{SVG_W-PAD_R}" y1="20" x2="{SVG_W-PAD_R}" y2="{SVG_H-20}" stroke="#1A2232" stroke-width="1"/>

          <!-- Loss zone -->
          <rect x="{loss_x:.1f}" y="{BAR_Y-24}" width="{loss_w:.1f}" height="{BAR_H+48}"
                fill="#FF6B6B" fill-opacity="0.08" rx="4"/>
          <rect x="{loss_x:.1f}" y="{BAR_Y}" width="{loss_w:.1f}" height="{BAR_H}"
                fill="#FF6B6B" fill-opacity="0.25" rx="2"/>

          <!-- Gain zone -->
          <rect x="{gain_x:.1f}" y="{BAR_Y-24}" width="{gain_w:.1f}" height="{BAR_H+48}"
                fill="#00FF88" fill-opacity="0.08" rx="4"/>
          <rect x="{gain_x:.1f}" y="{BAR_Y}" width="{gain_w:.1f}" height="{BAR_H}"
                fill="#00FF88" fill-opacity="0.25" rx="2"/>

          <!-- R:R label in centre -->
          <text x="{(entry_x + target_x)/2:.1f}" y="{BAR_Y - 32}" text-anchor="middle"
                fill="{rr_col}" font-size="11" font-family="monospace" font-weight="700">
            R:R  1:{rr_ratio}  {rr_label}
          </text>

          <!-- STOP line -->
          <line x1="{stop_x:.1f}" y1="{BAR_Y-8}" x2="{stop_x:.1f}" y2="{BAR_Y+BAR_H+8}"
                stroke="#FF6B6B" stroke-width="2" stroke-dasharray="4,3"/>
          <text x="{stop_x:.1f}" y="{BAR_Y-16}" text-anchor="middle"
                fill="#FF6B6B" font-size="10" font-family="monospace" font-weight="700">STOP</text>
          <text x="{stop_x:.1f}" y="{BAR_Y+BAR_H+20}" text-anchor="middle"
                fill="#FF6B6B" font-size="11" font-family="monospace">{cur}{stop_price:.2f}</text>
          <text x="{stop_x:.1f}" y="{BAR_Y+BAR_H+34}" text-anchor="middle"
                fill="#FF6B6B" font-size="10" font-family="monospace" opacity="0.7">−{stop_pct:.1f}%</text>

          <!-- ENTRY line -->
          <line x1="{entry_x:.1f}" y1="{BAR_Y-8}" x2="{entry_x:.1f}" y2="{BAR_Y+BAR_H+8}"
                stroke="#FACC15" stroke-width="2.5"/>
          <text x="{entry_x:.1f}" y="{BAR_Y-16}" text-anchor="middle"
                fill="#FACC15" font-size="10" font-family="monospace" font-weight="700">ENTRY</text>
          <text x="{entry_x:.1f}" y="{BAR_Y+BAR_H+20}" text-anchor="middle"
                fill="#FACC15" font-size="12" font-family="monospace" font-weight="700">{cur}{entry_price:.2f}</text>

          <!-- TARGET line -->
          <line x1="{target_x:.1f}" y1="{BAR_Y-8}" x2="{target_x:.1f}" y2="{BAR_Y+BAR_H+8}"
                stroke="#00FF88" stroke-width="2" stroke-dasharray="4,3"/>
          <text x="{target_x:.1f}" y="{BAR_Y-16}" text-anchor="middle"
                fill="#00FF88" font-size="10" font-family="monospace" font-weight="700">TARGET</text>
          <text x="{target_x:.1f}" y="{BAR_Y+BAR_H+20}" text-anchor="middle"
                fill="#00FF88" font-size="11" font-family="monospace">{cur}{target_price:.2f}</text>
          <text x="{target_x:.1f}" y="{BAR_Y+BAR_H+34}" text-anchor="middle"
                fill="#00FF88" font-size="10" font-family="monospace" opacity="0.7">+{target_pct:.1f}%</text>

          <!-- Loss / Gain labels inside zones -->
          <text x="{(stop_x + entry_x)/2:.1f}" y="{BAR_Y + BAR_H/2 + 4}" text-anchor="middle"
                fill="#FF6B6B" font-size="11" font-family="monospace" font-weight="700">
            −{cur}{risk_per_share:.2f}
          </text>
          <text x="{(entry_x + target_x)/2:.1f}" y="{BAR_Y + BAR_H/2 + 4}" text-anchor="middle"
                fill="#00FF88" font-size="11" font-family="monospace" font-weight="700">
            +{cur}{reward_per_share:.2f}
          </text>
        </svg>'''
        st.markdown(svg, unsafe_allow_html=True)
    except:
        pass

    # ── Summary strip ─────────────────────────────────────────
    st.markdown(f'''
    <div style="background:#111827;border:1px solid #243348;border-radius:8px;
                padding:8px 16px;margin-top:6px;display:flex;gap:20px;flex-wrap:wrap;
                font-size:11px;font-family:'JetBrains Mono',monospace;align-items:center;">
      <span style="color:#64748B;">Mode <span style="color:{mode_colors[selected_mode]};font-weight:700;">{selected_mode}</span></span>
      <span style="color:#64748B;">Entry <span style="color:#FACC15;">{cur}{entry_price:.2f}</span></span>
      <span style="color:#64748B;">Stop <span style="color:#FF6B6B;">{cur}{stop_price:.2f} (−{stop_pct:.1f}%)</span></span>
      <span style="color:#64748B;">Target <span style="color:#00FF88;">{cur}{target_price:.2f} (+{target_pct:.1f}%)</span></span>
      <span style="color:#64748B;">Max risk <span style="color:#FF6B6B;">{cur}{dollar_risk:.0f}</span></span>
      <span style="color:#64748B;">Risking <span style="color:#94A3B8;">{risk_pct:.1f}% of account</span></span>
    </div>''', unsafe_allow_html=True)

    # ── ZONE 8: EARNINGS ─────────────────────────────────────
    # Earnings from session state
    beat_str = a.get('last_earnings_beat', 'Unknown') or 'Unknown'
    if earnings_hist:
        last_e   = earnings_hist[-1]
        s        = last_e.get('surprise', 0) or 0
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
    for mcol, lbl, idx_key_chg, idx_key_sig in [
        (mc1, "S&P 500", "spy_1m",  "spy_signal"),
        (mc2, "NASDAQ",  "qqq_1m",  "qqq_signal"),
        (mc3, "DOW",     "dia_1m",  "dia_signal"),
    ]:
        chg  = mctx.get(idx_key_chg, 0) or 0
        sig  = mctx.get(idx_key_sig, "Unknown")
        vcol = "#00FF88" if chg >= 0 else "#FF6B6B"
        sign = "+" if chg >= 0 else ""
        with mcol:
            st.markdown(f'''<div class="earn-bar" style="border-left-color:{vcol};">
              <div class="earn-label">{lbl}</div>
              <div class="earn-val" style="color:{vcol};font-size:14px;">{sign}{chg:.1f}%</div>
              <div style="font-size:10px;color:#6B7280;margin-top:2px;">Last month · {sig}</div>
            </div>''', unsafe_allow_html=True)
    # Cycle phase and market risk
    for mcol2, lbl2, val2, col2, desc2 in [
        (mc4, "Cycle Phase", cycle,    cycle_col, a.get("cycle_desc","")),
        (mc5, "Market Risk", mkt_risk, risk_col,  a.get("market_risk_desc","")),
    ]:
        with mcol2:
            st.markdown(f'''<div class="earn-bar" style="border-left-color:{col2};">
              <div class="earn-label">{lbl2}</div>
              <div class="earn-val" style="color:{col2};font-size:13px;">{val2 or "—"}</div>
              <div style="font-size:10px;color:#6B7280;margin-top:2px;">{desc2[:60]}</div>
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
                inv_url    = f"https://www.investopedia.com/search?q={pat_name.replace(' ','+')}"
                bias_label = "▲ Bullish" if ptype=="bullish" else "▼ Bearish" if ptype=="bearish" else "↔ Neutral"
                target_html = f'<div class="pat-target" style="color:{pcol};">Target: {p.get("target_pct",0):+.1f}% → {cur}{p.get("target_price",0):.2f}</div>' if p.get('target_price') else ''
                conf_reason  = p.get("confidence_reason", "")
                still_valid  = p.get("still_valid", True)
                validity_note= p.get("validity_note", "")
                valid_col    = "#00FF88" if still_valid else "#FF6B6B"
                valid_label  = "✓ Pattern still valid" if still_valid else "✗ Pattern broken/resolved"
                st.markdown(f"""
                <div class="{pcls}">
                  <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:4px;">
                    <div class="pat-name" style="color:{pcol};">{pat_name}</div>
                    <a href="{inv_url}" target="_blank" style="font-size:10px;color:#4A6080;text-decoration:none;" title="Learn on Investopedia">ⓘ</a>
                  </div>
                  <div style="font-size:11px;font-weight:700;color:{pcol};margin-bottom:6px;">{bias_label}</div>
                  <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">
                    <div style="font-size:10px;color:#6B7280;">Confidence: {conf}%</div>
                    <div style="flex:1;height:3px;background:#243348;border-radius:2px;">
                      <div style="width:{conf}%;height:3px;background:{pcol};border-radius:2px;"></div>
                    </div>
                  </div>
                  {f'<div style="font-size:11px;color:#64748B;font-style:italic;margin-bottom:5px;">{conf_reason}</div>' if conf_reason else ''}
                  <div class="pat-desc" style="margin-bottom:6px;">{p.get("description","")}</div>
                  <div style="font-size:11px;color:{valid_col};font-weight:600;margin-bottom:3px;">{valid_label}</div>
                  {f'<div style="font-size:11px;color:#64748B;">{validity_note}</div>' if validity_note else ''}
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
            inv_c  = f"https://www.investopedia.com/search?q={c.get('name','').replace(' ','+')}"
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




# ── CEO Earnings Call Analyzer ────────────────────────────────
def get_earnings_analysis(transcript, ticker=""):
    """Send earnings call transcript to Claude for deep language analysis."""
    prompt = (
        "You are an expert financial analyst specializing in earnings call language analysis. "
        "Analyze the following earnings call transcript for tone, confidence, hedging, and forward guidance signals.\n\n"
        "Return ONLY raw JSON — no markdown, no backticks.\n\n"
        "TRANSCRIPT:\n"
        + transcript[:12000] +  # cap at ~12k chars to stay within token budget
        "\n\nAnalyze for:\n"
        "1. MANAGEMENT CONFIDENCE — word choice, certainty vs vagueness, use of passive voice\n"
        "2. HEDGING LANGUAGE — phrases like 'subject to', 'we believe', 'may', 'could', 'if conditions permit'\n"
        "3. GUIDANCE TONE — raised/maintained/lowered vs prior quarter, specific vs vague numbers\n"
        "4. TOPIC AVOIDANCE — questions deflected, topics changed, unusually short answers\n"
        "5. SENTIMENT SHIFT — compare early vs late in call, CEO vs CFO tone differences\n"
        "6. KEY QUOTES — exact phrases that are most telling (bullish or bearish)\n\n"
        "Return ONLY this JSON:\n"
        '{"signal":"Bullish|Bearish|Neutral",'
        '"confidence":"Low|Medium|High",'
        '"tone_score":7,'
        '"tone_score_desc":"one sentence explaining the score",'
        '"guidance":"Raised|Maintained|Lowered|Not Given",'
        '"guidance_detail":"one sentence on what specifically was raised/lowered/maintained",'
        '"summary":"2-3 sentence plain English overall read of this call",'
        '"key_findings":['
        '{"quote":"exact words from transcript","signal":"bullish|bearish|neutral","finding":"one sentence — what this reveals about management mindset"},'
        '{"quote":"...","signal":"...","finding":"..."},'
        '{"quote":"...","signal":"...","finding":"..."},'
        '{"quote":"...","signal":"...","finding":"..."},'
        '{"quote":"...","signal":"...","finding":"..."}'
        '],'
        '"red_flags":["specific concern 1","specific concern 2"],'
        '"positives":["specific strength 1","specific strength 2"],'
        '"hedging_phrases":["phrase 1","phrase 2","phrase 3"],'
        '"topic_avoidance":["topic 1 that was deflected or avoided"],'
        '"vs_last_quarter":"one sentence comparing tone to what a typical prior-quarter call sounds like"}'
    )
    try:
        client = anthropic.Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = msg.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())
    except Exception as e:
        return {"error": str(e)}


def render_earnings_analyzer():
    """Full UI for the CEO Earnings Call Language Analyzer."""
    st.markdown("""
    <div style="text-align:center;margin-bottom:6px;">
      <div style="font-size:12px;color:#4A6080;letter-spacing:3px;text-transform:uppercase;margin-bottom:8px;">AI Tool</div>
      <div style="font-size:24px;font-weight:800;color:#F1F5F9;margin-bottom:4px;">🎙️ Earnings Call Analyzer</div>
      <div style="font-size:13px;color:#4A6080;">Paste any earnings call transcript → Claude reads management tone, confidence, and guidance signals</div>
    </div>""", unsafe_allow_html=True)

    col1, col2, col3 = st.columns([1, 4, 1])
    with col2:
        ticker_ec = st.text_input("Ticker (optional)", placeholder="NVDA",
                                   key="ec_ticker", label_visibility="visible")
        transcript = st.text_area(
            "Paste Earnings Call Transcript",
            placeholder="Paste the full transcript here — prepared remarks + Q&A...",
            height=220, key="ec_transcript", label_visibility="visible"
        )
        st.markdown('<div style="font-size:10px;color:#4A6080;margin-top:-8px;margin-bottom:8px;">⚠ For educational purposes only. Not financial advice. Always conduct your own research.</div>', unsafe_allow_html=True)
        analyze_btn = st.button("🔍 Analyze Transcript", type="primary",
                                 use_container_width=True, key="ec_analyze")

    if analyze_btn:
        if not transcript or len(transcript.strip()) < 200:
            st.error("Please paste a transcript of at least 200 characters.")
            return

        with st.spinner("🤖 Claude is reading the transcript... (10-15 sec)"):
            result = get_earnings_analysis(transcript.strip(), ticker_ec.upper())

        if "error" in result:
            st.error(f"Analysis error: {result['error']}")
            return

        # ── Results ───────────────────────────────────────────
        sig     = result.get("signal", "Neutral")
        conf    = result.get("confidence", "Medium")
        tone    = result.get("tone_score", 5)
        guid    = result.get("guidance", "Not Given")
        summary = result.get("summary", "")

        sig_col  = "#00FF88" if sig == "Bullish" else "#FF6B6B" if sig == "Bearish" else "#FACC15"
        tone_col = "#00FF88" if tone >= 7 else "#FACC15" if tone >= 4 else "#FF6B6B"
        guid_col = "#00FF88" if guid == "Raised" else "#FF6B6B" if guid == "Lowered" else "#FACC15"

        # Header row
        h1, h2, h3, h4 = st.columns(4)
        for hcol, lbl, val, col in [
            (h1, "Overall Signal",    sig,  sig_col),
            (h2, "Confidence",        conf, "#94A3B8"),
            (h3, "Tone Score",        f"{tone}/10", tone_col),
            (h4, "Guidance",          guid, guid_col),
        ]:
            with hcol:
                st.markdown(f'''<div class="earn-bar" style="border-left-color:{col};">
                  <div class="earn-label">{lbl}</div>
                  <div class="earn-val" style="color:{col};font-size:18px;">{val}</div>
                </div>''', unsafe_allow_html=True)

        # Summary
        st.markdown(f'''
        <div style="background:#1A2232;border:1px solid #14B8A6;border-top:2px solid #14B8A6;
                    border-radius:8px;padding:14px 18px;margin-top:8px;">
          <div style="font-size:10px;color:#5EEAD4;letter-spacing:2px;text-transform:uppercase;margin-bottom:8px;">Summary</div>
          <div style="font-size:13px;color:#E2E8F0;line-height:1.8;">{summary}</div>
          <div style="font-size:12px;color:#64748B;margin-top:8px;">{result.get("vs_last_quarter","")}</div>
        </div>''', unsafe_allow_html=True)

        # Tone score bar
        st.markdown(f'''
        <div style="background:#1A2232;border:1px solid #243348;border-radius:8px;
                    padding:12px 16px;margin-top:8px;">
          <div style="display:flex;justify-content:space-between;margin-bottom:6px;">
            <span style="font-size:12px;color:#E2E8F0;font-weight:600;">Management Tone</span>
            <span style="font-size:12px;color:{tone_col};font-weight:700;">{tone}/10 — {result.get("tone_score_desc","")}</span>
          </div>
          <div style="position:relative;height:6px;background:#243348;border-radius:3px;">
            <div style="position:absolute;left:0;top:0;width:100%;height:6px;border-radius:3px;
                        background:linear-gradient(90deg,#FF6B6B,#FACC15,#00FF88);"></div>
            <div style="position:absolute;left:{min(max(tone*10,2),98)}%;top:-4px;width:12px;height:12px;
                        background:#F1F5F9;border-radius:50%;transform:translateX(-50%);
                        border:2px solid #111827;"></div>
          </div>
          <div style="display:flex;justify-content:space-between;margin-top:4px;font-size:10px;color:#374151;">
            <span>Defensive</span><span>Neutral</span><span>Confident</span>
          </div>
        </div>''', unsafe_allow_html=True)

        # Key findings
        findings = result.get("key_findings", [])
        if findings:
            st.markdown('<div class="section-header" style="margin-top:8px;">Key Findings — What Management Revealed</div>', unsafe_allow_html=True)
            st.markdown('<div style="background:#1A2232;border:1px solid #243348;border-top:none;border-radius:0 0 8px 8px;">', unsafe_allow_html=True)
            for f in findings:
                fc = "#00FF88" if f.get("signal")=="bullish" else "#FF6B6B" if f.get("signal")=="bearish" else "#FACC15"
                icon = "▲" if f.get("signal")=="bullish" else "▼" if f.get("signal")=="bearish" else "↔"
                st.markdown(f'''
                <div style="padding:10px 16px;border-bottom:1px solid #111827;">
                  <div style="font-size:12px;color:{fc};font-weight:700;margin-bottom:4px;">{icon} {f.get("finding","")}</div>
                  <div style="font-size:12px;color:#94A3B8;font-style:italic;padding-left:8px;
                              border-left:2px solid {fc};">"{f.get("quote","")}"</div>
                </div>''', unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)

        # Positives / Red flags / Hedging / Avoidance
        pa1, pa2 = st.columns(2)
        with pa1:
            positives = result.get("positives", [])
            st.markdown('<div class="section-header" style="margin-top:8px;">✅ Positives</div>', unsafe_allow_html=True)
            html = '<div style="background:#1A2232;border:1px solid #243348;border-top:none;border-radius:0 0 8px 8px;">'
            for p in positives:
                html += f'<div style="padding:8px 14px;border-bottom:1px solid #111827;font-size:12px;color:#86EFAC;">+ {p}</div>'
            if not positives:
                html += '<div style="padding:10px 14px;font-size:12px;color:#4A6080;">None identified</div>'
            st.markdown(html + '</div>', unsafe_allow_html=True)

            hedging = result.get("hedging_phrases", [])
            st.markdown('<div class="section-header" style="margin-top:8px;">🔶 Hedging Language Detected</div>', unsafe_allow_html=True)
            html = '<div style="background:#1A2232;border:1px solid #243348;border-top:none;border-radius:0 0 8px 8px;">'
            for h in hedging:
                html += f'<div style="padding:8px 14px;border-bottom:1px solid #111827;font-size:12px;color:#FACC15;font-style:italic;">"{h}"</div>'
            if not hedging:
                html += '<div style="padding:10px 14px;font-size:12px;color:#4A6080;">No significant hedging detected</div>'
            st.markdown(html + '</div>', unsafe_allow_html=True)

        with pa2:
            red_flags = result.get("red_flags", [])
            st.markdown('<div class="section-header" style="margin-top:8px;">🚩 Red Flags</div>', unsafe_allow_html=True)
            html = '<div style="background:#1A2232;border:1px solid #243348;border-top:none;border-radius:0 0 8px 8px;">'
            for r in red_flags:
                html += f'<div style="padding:8px 14px;border-bottom:1px solid #111827;font-size:12px;color:#FCA5A5;">− {r}</div>'
            if not red_flags:
                html += '<div style="padding:10px 14px;font-size:12px;color:#4A6080;">No red flags detected</div>'
            st.markdown(html + '</div>', unsafe_allow_html=True)

            avoidance = result.get("topic_avoidance", [])
            st.markdown('<div class="section-header" style="margin-top:8px;">🔇 Topics Avoided / Deflected</div>', unsafe_allow_html=True)
            html = '<div style="background:#1A2232;border:1px solid #243348;border-top:none;border-radius:0 0 8px 8px;">'
            for av in avoidance:
                html += f'<div style="padding:8px 14px;border-bottom:1px solid #111827;font-size:12px;color:#94A3B8;">↳ {av}</div>'
            if not avoidance:
                html += '<div style="padding:10px 14px;font-size:12px;color:#4A6080;">No obvious avoidance detected</div>'
            st.markdown(html + '</div>', unsafe_allow_html=True)

        # Guidance detail
        st.markdown(f'''
        <div style="background:#1A2232;border:1px solid #243348;border-left:3px solid {guid_col};
                    border-radius:8px;padding:10px 16px;margin-top:8px;">
          <div style="font-size:10px;color:#4A6080;letter-spacing:1px;text-transform:uppercase;margin-bottom:4px;">Guidance Detail</div>
          <div style="font-size:13px;color:#E2E8F0;">{result.get("guidance_detail","")}</div>
        </div>''', unsafe_allow_html=True)

        st.markdown('<div style="text-align:center;font-size:10px;color:#243348;padding:12px 0;margin-top:8px;">NOT FINANCIAL ADVICE · AI-GENERATED · EDUCATIONAL PURPOSES ONLY</div>', unsafe_allow_html=True)



if __name__ == "__main__":
    main()
