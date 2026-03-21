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

def resolve_all_matches(ticker):
    """Yahoo Finance search — returns ALL exact symbol matches across all exchanges."""
    import requests

    EXCH_MAP = {
        "TOR": (".TO",  "TSX",       "CAD"),
        "TSX": (".TO",  "TSX",       "CAD"),
        "CNQ": (".CN",  "TSXV",      "CAD"),
        "VAN": (".V",   "TSXV",      "CAD"),
        "LSE": (".L",   "LSE",       "GBP"),
        "EPA": (".PA",  "Euronext",  "EUR"),
        "ETR": (".DE",  "XETRA",     "EUR"),
        "AMS": (".AS",  "AEX",       "EUR"),
        "HKG": (".HK",  "HKEX",      "HKD"),
        "ASX": (".AX",  "ASX",       "AUD"),
        "NSE": (".NS",  "NSE",       "INR"),
        "BSE": (".BO",  "BSE",       "INR"),
        "NYQ": ("",    "NYSE",      "USD"),
        "NMS": ("",    "NASDAQ",    "USD"),
        "NCM": ("",    "NASDAQ",    "USD"),
        "NGM": ("",    "NASDAQ",    "USD"),
        "PCX": ("",    "NYSE Arca", "USD"),
        "PNK": ("",    "OTC",       "USD"),
        "OTC": ("",    "OTC",       "USD"),
        "BTS": ("",    "BATS",      "USD"),
        "CBO": ("",    "CBOE",      "USD"),
    }

    try:
        url = (f"https://query1.finance.yahoo.com/v1/finance/search"
               f"?q={ticker}&quotesCount=10&newsCount=0")
        r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return []
        quotes = r.json().get("quotes", [])
        if not quotes:
            return []

        results = []
        seen = set()
        for q in quotes:
            sym_raw  = q.get("symbol", "")
            yexch    = q.get("exchange", "")
            longname = q.get("longname") or q.get("shortname") or ""
            if not longname:
                continue

            base = sym_raw.split(".")[0].upper()
            if base != ticker.upper():
                continue

            suffix, disp_exch, default_curr = EXCH_MAP.get(yexch, ("", yexch, "USD"))
            proper_sym = ticker.upper() + suffix
            currency   = q.get("currency") or default_curr

            key = (proper_sym, disp_exch)
            if key in seen:
                continue
            seen.add(key)

            results.append({
                "sym":      proper_sym,
                "name":     longname[:52],
                "exchange": disp_exch,
                "currency": currency,
            })

        return results

    except:
        return []


def resolve_company_name(ticker):
    """Single-result wrapper around resolve_all_matches for use in fetch_ticker_data name fallback."""
    matches = resolve_all_matches(ticker)
    if not matches:
        return None
    exact = next((m for m in matches if m["sym"].upper() == ticker.upper()), matches[0])
    return {"name": exact["name"], "exchange": exact["exchange"], "currency": exact["currency"]}


def search_ticker_fmp(query, fmp_key=""):
    """Search FMP. Uses session-state cache — never caches empty results."""
    if not fmp_key or not query:
        return []
    cache_key = f"_fmp_search_{query.upper()}"
    cached = st.session_state.get(cache_key)
    if cached:
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
    if result:
        st.session_state[cache_key] = result
    return result

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_ticker_data(ticker, fmp_key="", _v=15):
    """Hybrid: yfinance for price+fundamentals, FMP only for analyst/earnings/insider."""
    import time, requests
    yf_ticker = ticker.replace('BRK.B','BRK-B').replace('BRK.A','BRK-A')
    use_fmp   = bool(fmp_key)

    df = None
    for attempt in range(3):
        try:
            raw = yf.Ticker(yf_ticker)
            df  = raw.history(period="2y")
            if not df.empty: break
        except:
            if attempt < 2: time.sleep(2 ** attempt)
    if df is None: df = pd.DataFrame()

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
        try:
            ln = getattr(fi, 'long_name', None) or getattr(fi, 'longName', None)
            if ln and str(ln).strip() and str(ln).upper() != ticker.upper():
                if 'longName' not in info:
                    info['longName']  = str(ln).strip()
                if 'shortName' not in info:
                    info['shortName'] = str(ln).strip()
        except: pass
    except: pass

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

    try:
        divs = raw.dividends
        if divs is not None and not divs.empty:
            annual_div = float(divs.tail(4).sum())
            price = float(info.get('regularMarketPrice', 0) or 0)
            if price > 0 and annual_div > 0:
                info['dividendYield'] = annual_div / price
    except: pass

    try:
        yf_info = raw.info or {}
        for k in ['sector','industry','longName','shortName','country',
                  'shortPercentOfFloat','floatShares','pegRatio','forwardPE',
                  'targetMeanPrice','targetLowPrice','targetHighPrice',
                  'numberOfAnalystOpinions','recommendationKey','recommendationMean']:
            if yf_info.get(k) and k not in info:
                info[k] = yf_info[k]
    except: pass

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

    earn_hist       = None
    insider         = None
    rec_summary     = None
    analyst_targets = None
    calendar        = None
    earn_dates      = None

    if use_fmp:
        try:
            profile = _fmp_get(f"v3/profile/{ticker}", fmp_key)
            if not profile or not isinstance(profile, list) or not profile:
                alt = ticker.replace('-','.')
                profile = _fmp_get(f"v3/profile/{alt}", fmp_key)
            if profile and isinstance(profile, list) and profile:
                p = profile[0]
                if p.get('companyName',''):
                    info['longName']  = p['companyName']
                    info['shortName'] = p['companyName']
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

        try:
            surp = _fmp_get(f"v3/earnings-surprises/{ticker}", fmp_key)
            if surp and isinstance(surp, list):
                rows = []
                for e in surp[:4]:
                    act_val  = e.get("actualEarningResult",  e.get("actualEps",   e.get("actual",   0)))
                    est_val  = e.get("estimatedEarning",     e.get("estimatedEps",e.get("estimate", 0)))
                    act_val  = float(act_val or 0)
                    est_val  = float(est_val or 0)
                    surp_pct = ((act_val - est_val) / abs(est_val)) if est_val != 0 else 0
                    rows.append({
                        "period":          e.get("date", ""),
                        "epsEstimate":     est_val,
                        "epsActual":       act_val,
                        "surprisePercent": surp_pct,
                    })
                if rows:
                    earn_hist = pd.DataFrame(rows)
        except: pass

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

        try:
            est = _fmp_get(f"v3/analyst-stock-recommendations/{ticker}", fmp_key)
            if est and isinstance(est, list) and est:
                e = est[0]
                sb   = int(e.get("analystRatingsStrongBuy",  e.get("strongBuy",  0)) or 0)
                b    = int(e.get("analystRatingsBuy",         e.get("buy",       0)) or 0)
                h    = int(e.get("analystRatingsHold",        e.get("hold",      0)) or 0)
                s    = int(e.get("analystRatingsSell",        e.get("sell",      0)) or 0)
                ss   = int(e.get("analystRatingsStrongSell",  e.get("strongSell",0)) or 0)
                rec_summary = pd.DataFrame([{
                    "strongBuy": sb, "buy": b,
                    "hold": h, "sell": s, "strongSell": ss
                }])
                total = sb + b + h + s + ss
                if total > 0:
                    info["numberOfAnalystOpinions"] = total
        except: pass

        try:
            from datetime import datetime as _dt, timedelta
            today = _dt.now().strftime("%Y-%m-%d")
            fut   = (_dt.now() + timedelta(days=180)).strftime("%Y-%m-%d")
            cal = _fmp_get(f"v3/earning_calendar?from={today}&to={fut}", fmp_key)
            if cal and isinstance(cal, list):
                matches = [e for e in cal if str(e.get("symbol","")).upper() == ticker.upper()]
                if matches:
                    ned_str = str(matches[0].get("date",""))
                    if ned_str:
                        calendar = {"Earnings Date": ned_str}
                        info["earningsDate"] = ned_str
        except: pass

    try:
        ins = raw.insider_transactions
        if ins is not None and not ins.empty:
            insider = ins
    except: pass
    if insider is None or (hasattr(insider,'empty') and insider.empty):
        try:
            ins = raw.insider_purchases
            if ins is not None and not ins.empty:
                insider = ins
        except: pass
    if insider is not None and hasattr(insider, 'columns'):
        col_map = {}
        for c in insider.columns:
            cl = c.lower().replace(' ','').replace('_','')
            if cl in ('shares','sharesowned','sharesnumber'):          col_map[c] = 'Shares'
            elif cl in ('value','transactionvalue','dollarvalue'):     col_map[c] = 'Value'
            elif cl in ('text','transactiontext','description'):       col_map[c] = 'Text'
            elif cl in ('transaction','transactiontype','type'):       col_map[c] = 'Transaction'
            elif cl in ('insider','name','filername','ownername'):     col_map[c] = 'Insider'
            elif cl in ('position','title','filerrelation','role'):    col_map[c] = 'Position'
            elif cl in ('date','startdate','transactiondate','filingdate'): col_map[c] = 'Date'
        if col_map:
            try: insider = insider.rename(columns=col_map)
            except: pass

    if not calendar:
        try:
            cal_yf = raw.calendar
            if cal_yf is not None:
                calendar = cal_yf if isinstance(cal_yf, dict) else (cal_yf.to_dict() if hasattr(cal_yf,'to_dict') else None)
        except: pass

    iv = 0.0
    try:
        opts = raw.options
        if opts:
            chain = raw.option_chain(opts[0])
            cp    = float(df["Close"].iloc[-1]) if not df.empty else 0
            atm   = chain.calls.iloc[(chain.calls["strike"]-cp).abs().argsort()[:1]]
            iv    = float(atm["impliedVolatility"].values[0]) * 100
    except: pass

    if not info.get('longName') or info.get('longName','').upper() == ticker.upper():
        try:
            import requests as _req
            url = f"https://query1.finance.yahoo.com/v1/finance/search?q={ticker}&quotesCount=5&newsCount=0"
            r = _req.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200:
                quotes = r.json().get("quotes", [])
                match = next((q for q in quotes if q.get("symbol","").upper() == ticker.upper()), None)
                if match:
                    name = match.get("longname") or match.get("shortname") or ""
                    if name:
                        info['longName']  = name
                        info['shortName'] = name
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
    initial_sidebar_state="expanded"
)

VERDICT_COLORS = {
    "SWING TRADE":     {"bg": "#0A1525", "border": "#38BDF8", "color": "#38BDF8"},
    "INVEST":          {"bg": "#0A1E12", "border": "#00FF88", "color": "#00FF88"},
    "DAY TRADE":       {"bg": "#1A1000", "border": "#FACC15", "color": "#FACC15"},
    "AVOID":           {"bg": "#1E0A0A", "border": "#FF6B6B", "color": "#FF6B6B"},
    "WATCH": {"bg": "#0A1E12", "border": "#00FF88", "color": "#00FF88"},
}

# ── CSS ───────────────────────────────────────────────────────
st.markdown("""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;600;700&display=swap');

  html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
  .stApp { background: #111827; }
  .block-container { padding: 3.5rem 2rem 2rem; max-width: 1200px; margin-left: auto; margin-right: auto; transition: all 0.3s ease; }

  /* Hide Streamlit branding — header stays visible for toggle to work */
  #MainMenu { visibility: hidden; }
  footer { visibility: hidden; }
  .stDeployButton { display: none; }
  /* Header: same color as app background — invisible but toggle still works */
  header[data-testid="stHeader"] {
    background-color: #111827 !important;
    border-bottom: none !important;
  }

  /* ── Sidebar styling ── */
  section[data-testid="stSidebar"] {
    background: #0D1525 !important;
    border-right: 1px solid #1E2D42 !important;
    min-width: 300px !important;
    width: 300px !important;
  }
  section[data-testid="stSidebar"] > div {
    padding: 1rem !important;
  }
  .main .block-container {
    transition: margin 0.3s ease;
    margin-left: auto;
    margin-right: auto;
  }
  /* Recenter content when sidebar is collapsed */
  section[data-testid="stSidebar"][aria-expanded="false"] ~ section .block-container,
  section[data-testid="stSidebar"][aria-expanded="false"] ~ .main .block-container {
    margin-left: auto !important;
    margin-right: auto !important;
  }

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

  .status-bar {
    background: linear-gradient(90deg, #0E2218 0%, #0E1C30 100%);
    border-radius: 6px; padding: 7px 16px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px; color: #3D6050;
    margin-bottom: 10px;
  }
  .status-bar span { color: #99F6E4; font-weight: 600; }

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

  .verdict-card { border-radius: 8px; padding: 16px 18px; border-left-width: 3px; border-left-style: solid; }
  .verdict-label { font-size: 11px; letter-spacing: 2px; text-transform: uppercase; opacity: 0.8; margin-bottom: 5px; }
  .verdict-value { font-size: 28px; font-weight: 800; letter-spacing: 1px; }
  .verdict-meta { font-size: 12px; color: #94A3B8; margin-top: 4px; }
  .verdict-note { font-size: 12px; margin-top: 6px; line-height: 1.5; opacity: 0.9; }

  .score-card { background: #1C1A50; border: 1px solid #3730A3; border-left: 3px solid #818CF8; border-radius: 8px; padding: 16px 18px; }
  .score-label { font-size: 11px; color: #818CF8; letter-spacing: 2px; text-transform: uppercase; margin-bottom: 6px; }
  .score-num { font-family: 'JetBrains Mono', monospace; font-size: 52px; font-weight: 800; line-height: 1; }
  .score-denom { font-size: 20px; color: #4A6080; }

  .reason-bull { background: #0D2818; border-left: 2px solid #00FF88; padding: 8px 12px; font-size: 12px; color: #86EFAC; border-radius: 0 6px 6px 0; line-height: 1.5; margin-bottom: 4px; }
  .reason-bear { background: #2D1015; border-left: 2px solid #FF6B6B; padding: 8px 12px; font-size: 12px; color: #FCA5A5; border-radius: 0 6px 6px 0; line-height: 1.5; margin-bottom: 4px; }

  .tf-day   { background: #1A1000; border: 1px solid #FACC1533; border-radius: 8px; padding: 12px 14px; }
  .tf-swing { background: #0D1525; border: 1px solid #38BDF833; border-radius: 8px; padding: 12px 14px; }
  .tf-inv   { background: #0D2010; border: 1px solid #00FF8833; border-radius: 8px; padding: 12px 14px; }
  .tf-label { font-size: 11px; font-weight: 800; letter-spacing: 1.5px; text-transform: uppercase; margin-bottom: 6px; }
  .tf-note  { font-size: 12px; color: #CBD5E1; line-height: 1.6; }

  .earn-bar { background: #1A2232; border: 1px solid #243348; border-left: 3px solid #818CF8; border-radius: 8px; padding: 10px 16px; }
  .earn-label { font-size: 9px; color: #4A6080; letter-spacing: 1px; text-transform: uppercase; margin-bottom: 3px; }
  .earn-val { font-size: 13px; font-weight: 700; font-family: 'JetBrains Mono', monospace; }

  .summary-box { background: #1A2232; border-top: 2px solid #14B8A6; border-radius: 0 0 8px 8px; padding: 14px 16px; }
  .summary-text { font-size: 13px; color: #E2E8F0; line-height: 1.8; }

  .pat-bull { background: #0D2818; border: 1px solid #00FF8830; border-radius: 8px; padding: 12px 14px; }
  .pat-bear { background: #2D0A0A; border: 1px solid #FF6B6B30; border-radius: 8px; padding: 12px 14px; }
  .pat-neut { background: #251800; border: 1px solid #FACC1530; border-radius: 8px; padding: 12px 14px; }
  .pat-name { font-size: 14px; font-weight: 700; margin-bottom: 4px; }
  .pat-desc { font-size: 12px; color: #CBD5E1; line-height: 1.5; margin-top: 5px; }
  .pat-target { font-size: 12px; font-weight: 600; margin-top: 6px; }

  .candle-card { background: #1A2232; border: 1px solid #2A3F5A; border-radius: 8px; padding: 12px 14px; text-align: center; }
  .candle-card-bull { background: #0D2818; border: 1px solid #00FF8830; border-radius: 8px; padding: 12px 14px; text-align: center; }
  .candle-card-bear { background: #2D1015; border: 1px solid #FF6B6B30; border-radius: 8px; padding: 12px 14px; text-align: center; }
  .candle-card-neut { background: #251800; border: 1px solid #FACC1530; border-radius: 8px; padding: 12px 14px; text-align: center; }

  .trend-tile { background: #1A2232; border: 1px solid #2A3F5A; border-radius: 8px; padding: 12px 14px; }
  .trend-tile-label { font-size: 10px; color: #94A3B8; letter-spacing: 1px; text-transform: uppercase; margin-bottom: 5px; }
  .trend-tile-val { font-size: 15px; font-weight: 800; margin-bottom: 4px; }
  .trend-tile-desc { font-size: 12px; color: #CBD5E1; line-height: 1.5; }

  .range-wrap { position: relative; height: 6px; background: #243348; border-radius: 3px; margin: 6px 0; }
  .range-fill { position: absolute; left: 0; top: 0; height: 6px; border-radius: 3px; background: linear-gradient(90deg, #FF6B6B, #FACC15, #00FF88); }
  .range-dot { position: absolute; top: -4px; width: 12px; height: 12px; background: #F1F5F9; border-radius: 50%; transform: translateX(-50%); border: 2px solid #111827; }

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
  /* Main CTA button — only the analyze button, not dev tools */
  [data-testid="stForm"] .stButton button,
  .stTextInput + div .stButton button,
  button[kind="primary"] {
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

  div.screener-btn .stButton button,
  .screener-btn .stButton button {
    background: #0D2818 !important;
    color: #00FF88 !important;
    border: 1px solid #00FF8866 !important;
    font-size: 14px !important;
    font-weight: 700 !important;
    padding: 10px !important;
    letter-spacing: 0.03em !important;
  }
  div.screener-btn .stButton button:hover,
  .screener-btn .stButton button:hover {
    background: #052A14 !important;
    border-color: #00FF88 !important;
    box-shadow: 0 0 16px rgba(0,255,136,0.15) !important;
    opacity: 1 !important;
  }
  .tpl-card {
    border-radius: 8px;
    padding: 14px 16px;
    transition: background 180ms ease, border-color 180ms ease;
  }
  .tpl-wrap-red   .tpl-card:hover { background: #2D1015 !important; border-color: #FF6B6B !important; }
  .tpl-wrap-green .tpl-card:hover { background: #0D2818 !important; border-color: #00FF88 !important; }
  .tpl-wrap-blue  .tpl-card:hover { background: #0A1525 !important; border-color: #38BDF8 !important; }
  .tpl-wrap-gold  .tpl-card:hover { background: #251800 !important; border-color: #FACC15 !important; }

  .tpl-select .stButton button {
    background: transparent !important;
    font-size: 10px !important;
    font-weight: 700 !important;
    padding: 5px 12px !important;
    width: auto !important;
    height: auto !important;
    min-height: 0 !important;
    letter-spacing: 0.06em !important;
    border-radius: 4px !important;
  }
  .tpl-select-red   .stButton button { color: #FF6B6B !important; border: 1px solid #FF6B6B !important; }
  .tpl-select-green .stButton button { color: #00FF88 !important; border: 1px solid #00FF88 !important; }
  .tpl-select-blue  .stButton button { color: #38BDF8 !important; border: 1px solid #38BDF8 !important; }
  .tpl-select-gold  .stButton button { color: #FACC15 !important; border: 1px solid #FACC15 !important; }
  .tpl-select-red   .stButton button:hover { background: #FF6B6B22 !important; opacity: 1 !important; }
  .tpl-select-green .stButton button:hover { background: #00FF8822 !important; opacity: 1 !important; }
  .tpl-select-blue  .stButton button:hover { background: #38BDF822 !important; opacity: 1 !important; }
  .tpl-select-gold  .stButton button:hover { background: #FACC1522 !important; opacity: 1 !important; }
  .reset-btn .stButton button {
    background: #111827 !important;
    color: #64748B !important;
    border: 1px solid #374151 !important;
    font-size: 13px !important;
    font-weight: 600 !important;
    padding: 10px !important;
    letter-spacing: 0 !important;
  }
  .reset-btn .stButton button:hover {
    border-color: #FF6B6B !important;
    color: #FF6B6B !important;
    background: #1E0A0A !important;
    opacity: 1 !important;
  }

  .hud-footer { text-align: center; font-size: 10px; color: #243348; padding: 12px 0; letter-spacing: 1px; }

  .val-b { color: #38BDF8; font-weight: 700; font-family: 'JetBrains Mono', monospace; }

  .data-header { background: #131F32; padding: 6px 14px; font-size: 10px; color: #5EEAD4; letter-spacing: 1.5px; text-transform: uppercase; border-bottom: 1px solid #111827; }

  .vol-panel { background: #1A2232; border-radius: 8px; overflow: hidden; border: 1px solid #243348; margin-bottom: 8px; }
  .vol-row { display: flex; justify-content: space-between; align-items: center; padding: 8px 14px; border-bottom: 1px solid #111827; font-size: 13px; }
  .vol-row:last-child { border-bottom: none; }
  .vol-lbl { color: #E2E8F0; font-weight: 500; }
  .vol-bar-wrap { flex: 1; margin: 0 12px; height: 5px; background: #243348; border-radius: 3px; position: relative; overflow: hidden; }
  .vol-bar-fill { height: 5px; border-radius: 3px; }

  .analyst-panel { background: #1A2232; border-radius: 8px; overflow: hidden; border: 1px solid #243348; margin-bottom: 8px; }
  .analyst-bar-wrap { display: flex; height: 8px; border-radius: 4px; overflow: hidden; margin: 6px 0; }
  .analyst-seg-buy  { background: #00FF88; }
  .analyst-seg-hold { background: #FACC15; }
  .analyst-seg-sell { background: #FF6B6B; }
  .analyst-count { font-size: 11px; font-weight: 700; }

  .earn-hist-row { display: grid; grid-template-columns: 1fr 1fr 1fr 1fr; gap: 5px; padding: 8px 14px; border-bottom: 1px solid #111827; font-size: 12px; }
  .earn-hist-row:last-child { border-bottom: none; }
  .earn-beat { color: #00FF88; font-weight: 700; }
  .earn-miss { color: #FF6B6B; font-weight: 700; }
  .earn-inline { color: #FACC15; font-weight: 700; }

  .insider-row { display: flex; justify-content: space-between; align-items: center; padding: 7px 14px; border-bottom: 1px solid #111827; font-size: 12px; }
  .insider-row:last-child { border-bottom: none; }
  .insider-buy  { color: #00FF88; font-weight: 700; font-size: 11px; }
  .insider-sell { color: #FF6B6B; font-weight: 700; font-size: 11px; }
  .insider-name { color: #E2E8F0; flex: 1; }
  .insider-role { color: #64748B; font-size: 11px; flex: 1; }
  .insider-shares { color: #94A3B8; font-size: 11px; text-align: right; }

  .news-row { padding: 8px 14px; border-bottom: 1px solid #111827; }
  .news-row:last-child { border-bottom: none; }
  .news-headline { font-size: 12px; color: #E2E8F0; line-height: 1.4; margin-bottom: 3px; }
  .news-meta { display: flex; justify-content: space-between; font-size: 10px; color: #4A6080; }
  .news-bull { color: #00FF88; font-weight: 700; }
  .news-bear { color: #FF6B6B; font-weight: 700; }
  .news-neut { color: #FACC15; font-weight: 700; }

  .score-bar-wrap { position: relative; height: 8px; background: #111827; border-radius: 4px; margin: 8px 0; overflow: hidden; }
  .score-bar-track { position: absolute; top: 0; left: 0; width: 100%; height: 8px; background: linear-gradient(90deg, #FF6B6B 0%, #FACC15 45%, #00FF88 100%); opacity: 0.2; border-radius: 4px; }
  .score-bar-fill { position: absolute; top: 0; left: 0; height: 8px; background: linear-gradient(90deg, #FF6B6B 0%, #FACC15 45%, #00FF88 100%); border-radius: 4px; }
  .score-markers { display: flex; justify-content: space-between; font-size: 9px; color: #374151; font-family: 'JetBrains Mono', monospace; }

  div[data-testid="stHorizontalBlock"] { gap: 8px; }

  div[data-testid="stButton"] button[kind="secondary"],
  div[data-testid="stButton"] button {
    background: #1A2232;
    border: 1px solid #243348;
    color: #94A3B8;
    border-radius: 6px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.05em;
    padding: 6px 12px;
    transition: border-color 150ms, color 150ms, background 150ms;
  }
  div[data-testid="stButton"] button:hover {
    border-color: #14B8A6;
    color: #5EEAD4;
    background: #0F3030;
  }

  div[data-testid="stButton"] button[kind="primary"] {
    background: #0D2818;
    border: 1px solid #00FF88;
    color: #00FF88;
    font-weight: 700;
    font-size: 13px;
    letter-spacing: 0.03em;
  }
  div[data-testid="stButton"] button[kind="primary"]:hover {
    background: #052A14;
    border-color: #00FF88;
    color: #86EFAC;
    box-shadow: 0 0 12px rgba(0,255,136,0.2);
  }

  .tpl-active button {
    background: #0A1E12 !important;
    border-color: #00FF88 !important;
    color: #00FF88 !important;
  }

  .btn-reset button {
    background: #111827 !important;
    border-color: #374151 !important;
    color: #64748B !important;
    font-size: 11px !important;
  }
  .btn-reset button:hover {
    border-color: #FF6B6B !important;
    color: #FF6B6B !important;
  }

  .btn-confirm button[kind="primary"] {
    background: #0A1525 !important;
    border-color: #38BDF8 !important;
    color: #38BDF8 !important;
  }
  .btn-confirm button[kind="primary"]:hover {
    background: #0D1B2A !important;
    box-shadow: 0 0 12px rgba(56,189,248,0.2) !important;
  }

  div[data-testid="stTabs"] button[role="tab"] {
    font-size: 12px;
    color: #64748B;
    font-weight: 500;
  }
  div[data-testid="stTabs"] button[role="tab"][aria-selected="true"] {
    color: #E2E8F0;
    font-weight: 700;
  }

  div[data-testid="stSelectbox"] > div > div {
    background: #1A2232 !important;
    border-color: #243348 !important;
    color: #E2E8F0 !important;
  }

  div[data-testid="stTextArea"] textarea {
    background: #0D1B2A !important;
    border-color: #243348 !important;
    color: #E2E8F0 !important;
    font-size: 13px;
  }
  div[data-testid="stTextArea"] textarea:focus {
    border-color: #38BDF8 !important;
    box-shadow: 0 0 0 1px #38BDF840 !important;
  }

  div[data-testid="stNumberInput"] input {
    background: #1A2232 !important;
    border-color: #243348 !important;
    color: #FACC15 !important;
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 14px !important;
  }

  div[data-testid="stProgress"] > div > div {
    background: linear-gradient(90deg, #14B8A6, #00FF88) !important;
  }

  div[data-testid="stAlert"] {
    background: #0D1B2A !important;
    border-color: #243348 !important;
    color: #94A3B8 !important;
  }

  section[data-testid="stSidebar"] .stExpander {
    border: 1px solid #1E2D42 !important;
    border-radius: 8px !important;
    margin-bottom: 8px !important;
    background: #111827 !important;
  }
  section[data-testid="stSidebar"] .stExpander summary {
    font-size: 13px !important;
    font-weight: 600 !important;
    color: #CBD5E1 !important;
  }
  section[data-testid="stSidebar"] .stExpander summary:hover {
    color: #5EEAD4 !important;
  }

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

    delta = close.diff()
    gain  = delta.clip(lower=0).ewm(com=13, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=13, adjust=False).mean()
    df['RSI'] = 100 - (100 / (1 + gain / loss.replace(0, 1e-10)))

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df['MACD']     = ema12 - ema26
    df['MACDSig']  = df['MACD'].ewm(span=9, adjust=False).mean()
    df['MACDHist'] = df['MACD'] - df['MACDSig']

    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs()
    ], axis=1).max(axis=1)
    df['ATR']    = tr.ewm(com=13, adjust=False).mean()
    df['ATRPct'] = df['ATR'] / close

    obv = [0]
    for i in range(1, len(df)):
        if close.iloc[i] > close.iloc[i-1]:
            obv.append(obv[-1] + vol.iloc[i])
        elif close.iloc[i] < close.iloc[i-1]:
            obv.append(obv[-1] - vol.iloc[i])
        else:
            obv.append(obv[-1])
    df['OBV'] = obv

    df['VolMA20']   = vol.rolling(20).mean()
    df['VolTrend']  = vol / df['VolMA20'].replace(0, 1)

    # OBV divergence — linear regression slope comparison (20-day)
    import numpy as _np
    def _slope(series, n=20):
        if len(series) < n: return 0.0
        y = series.tail(n).values.astype(float)
        x = _np.arange(n)
        try: return float(_np.polyfit(x, y, 1)[0])
        except: return 0.0

    price_slope = _slope(close, 20)
    obv_slope   = _slope(df['OBV'], 20)
    price_mean  = float(close.tail(20).mean()) or 1
    obv_mean    = float(df['OBV'].tail(20).abs().mean()) or 1
    p_norm = price_slope / price_mean
    o_norm = obv_slope   / obv_mean
    thresh = 0.001
    df['OBV_div'] = 0  # 1=bullish div, -1=bearish div, 0=none
    if p_norm < -thresh and o_norm > thresh:
        df.loc[df.index[-1], 'OBV_div'] = 1   # price falling, OBV rising = accumulation
    elif p_norm > thresh and o_norm < -thresh:
        df.loc[df.index[-1], 'OBV_div'] = -1  # price rising, OBV falling = distribution

    return df.dropna(subset=['MA20','MA50','RSI','MACD'])


def detect_weinstein_phase(df):
    """
    Weinstein Phase detection — multi-MA + price structure + OBV + elasticity.

    Signals used (from Weinstein & Investopedia source):
    - MA20, MA50, MA150 alignment and slopes
    - Higher highs / higher lows vs lower highs / lower lows
    - Price bar elasticity (Stage 3: bars failing to reach upper half of range)
    - OBV direction vs price direction (accumulation / distribution)
    - Volume character (expanding up vs expanding down)
    - Distance from 200-day peak

    Returns: (phase, label, sublabel, color, conf_score, conf_text, desc)
    """
    import numpy as _np

    if len(df) < 160:
        return (0, 'PHASE ?', 'Unclear', '#94A3B8', 0, '', 'Insufficient data')

    close  = df['Close']
    high   = df['High']
    low    = df['Low']
    volume = df['Volume']

    # ── Moving Averages ───────────────────────────────────────
    ma20  = close.rolling(20).mean()
    ma50  = close.rolling(50).mean()
    ma150 = close.rolling(150).mean()

    c      = float(close.iloc[-1])
    m20    = float(ma20.iloc[-1])
    m50    = float(ma50.iloc[-1])
    m150   = float(ma150.iloc[-1])

    # Slopes (10-day rate of change, normalized)
    def _slope(series, n=10):
        cur  = float(series.iloc[-1])
        prev = float(series.iloc[-(n+1)]) if len(series) > n else cur
        return (cur - prev) / prev if prev != 0 else 0

    slope_20  = _slope(ma20,  10)
    slope_50  = _slope(ma50,  10)
    slope_150 = _slope(ma150, 10)

    # ── Price Structure (20-day windows) ──────────────────────
    recent_high = float(high.tail(20).max())
    prior_high  = float(high.iloc[-40:-20].max()) if len(high) >= 40 else recent_high
    recent_low  = float(low.tail(20).min())
    prior_low   = float(low.iloc[-40:-20].min()) if len(low) >= 40 else recent_low

    higher_highs = recent_high > prior_high * 1.01
    higher_lows  = recent_low  > prior_low  * 1.01
    lower_highs  = recent_high < prior_high * 0.99
    lower_lows   = recent_low  < prior_low  * 0.99

    # ── MA Alignment ─────────────────────────────────────────
    # Stage 2 ideal: price > MA20 > MA50 > MA150, all rising
    # Stage 4 ideal: price < MA20 < MA50 < MA150, all falling
    mas_bullish_aligned = (c > m20 > m50 > m150) and slope_150 > 0
    mas_bearish_aligned = (c < m20 < m50 < m150) and slope_150 < 0
    above_150 = c > m150
    below_150 = c < m150

    # ── Price Bar Elasticity (Weinstein Stage 3 signal) ───────
    # "Mature top: bars failing to reach upper half of range"
    # Measure: how often close is in upper half of day's range (last 15 days)
    recent_bars = df.tail(15)
    bar_range   = (recent_bars['High'] - recent_bars['Low']).replace(0, 0.001)
    close_pct   = (recent_bars['Close'] - recent_bars['Low']) / bar_range
    elasticity  = float(close_pct.mean())  # 0=always at low, 1=always at high
    # < 0.45 = limp bars (Stage 3/4 signal), > 0.55 = strong closes (Stage 2)
    limp_bars   = elasticity < 0.45
    strong_bars = elasticity > 0.55

    # ── OBV vs Price (accumulation / distribution) ────────────
    obv = df.get('OBV', None)
    if obv is not None and len(obv) >= 20:
        obv_slope  = _slope(obv, 20)
        price_slope_20 = _slope(close, 20)
        obv_rising      = obv_slope > 0
        obv_falling     = obv_slope < 0
        # Divergence: OBV going opposite to price
        bullish_div = price_slope_20 < -0.001 and obv_slope > 0.001   # accumulation
        bearish_div = price_slope_20 >  0.001 and obv_slope < -0.001  # distribution
    else:
        obv_rising = obv_falling = bullish_div = bearish_div = False

    # ── Volume Character ─────────────────────────────────────
    # Expanding volume on up days vs down days (last 20 sessions)
    recent_20 = df.tail(20)
    up_days   = recent_20[recent_20['Close'] > recent_20['Open']]
    dn_days   = recent_20[recent_20['Close'] < recent_20['Open']]
    avg_up_vol = float(up_days['Volume'].mean()) if len(up_days) > 0 else 0
    avg_dn_vol = float(dn_days['Volume'].mean()) if len(dn_days) > 0 else 0
    vol_bullish = avg_up_vol > avg_dn_vol * 1.1   # up days on bigger volume
    vol_bearish = avg_dn_vol > avg_up_vol * 1.1   # down days on bigger volume

    # ── Distance from peak ────────────────────────────────────
    peak_200 = float(close.tail(200).max())
    pct_off  = (peak_200 - c) / peak_200 if peak_200 > 0 else 0

    # ── Phase Scoring System ──────────────────────────────────
    # Each phase has a score based on confirming signals
    # Highest score wins, with minimum threshold

    s1 = s2 = s3 = s4 = 0

    # PHASE 1 signals (basing below flat MA)
    if below_150 and abs(slope_150) < 0.002:  s1 += 2  # flat MA below
    if higher_lows and not higher_highs:       s1 += 2  # higher lows = base
    if bullish_div:                            s1 += 2  # OBV leading price
    if obv_rising and not higher_highs:        s1 += 1  # accumulation
    if pct_off > 0.30:                         s1 += 1  # well off peak

    # PHASE 2 signals (uptrend, higher highs + higher lows)
    if mas_bullish_aligned:                    s2 += 3  # all MAs aligned up
    elif above_150 and slope_150 > 0.0005:     s2 += 2  # above rising MA150
    if higher_highs and higher_lows:           s2 += 2  # classic uptrend structure
    elif higher_highs and not lower_lows:      s2 += 1  # highs up at minimum
    if strong_bars and not limp_bars:          s2 += 1  # strong closes = elasticity
    if vol_bullish:                            s2 += 1  # volume confirms
    if obv_rising:                             s2 += 1  # OBV trending up

    # PHASE 3 signals (distribution topping)
    if above_150 and slope_150 < 0.001 and lower_highs:  s3 += 3  # core signal
    if limp_bars:                              s3 += 2  # Weinstein's elasticity
    if bearish_div or (obv_falling and above_150): s3 += 2  # OBV distribution
    if vol_bearish and above_150:             s3 += 1  # heavy down volume
    if lower_highs and not lower_lows:        s3 += 1  # lower highs forming
    if pct_off > 0.08 and above_150:          s3 += 1  # 8%+ off peak above MA

    # PHASE 4 signals (downtrend, lower lows)
    if mas_bearish_aligned:                    s4 += 3  # all MAs aligned down
    elif below_150 and slope_150 < -0.0005:    s4 += 2  # below declining MA
    if lower_highs and lower_lows:             s4 += 2  # classic downtrend
    if vol_bearish:                            s4 += 1  # heavy selling
    if obv_falling:                            s4 += 1  # OBV declining
    if limp_bars:                              s4 += 1  # weak closes
    if pct_off > 0.20:                         s4 += 1  # well off peak

    # ── Phase Decision ────────────────────────────────────────
    scores = {1: s1, 2: s2, 3: s3, 4: s4}
    phase  = max(scores, key=scores.get)
    best   = scores[phase]

    # Minimum threshold — if max score is very low, unclear
    if best < 3:
        phase = 0

    # Confidence = how far ahead of second-best
    sorted_scores = sorted(scores.values(), reverse=True)
    gap   = sorted_scores[0] - sorted_scores[1] if len(sorted_scores) > 1 else 0
    conf  = 3 if gap >= 4 else 2 if gap >= 2 else 1 if gap >= 1 else 0

    # Sub-classification for Phase 2 (early vs late)
    if phase == 2:
        if slope_20 < slope_50 and pct_off < 0.05 and not strong_bars:
            sublabel = "Late Uptrend"
            desc2    = "Momentum slowing, higher highs tightening — early Phase 3 watch"
        else:
            sublabel = "Uptrend"
            desc2    = "Price above rising MA, higher highs/lows — the only phase to buy"
    else:
        sublabel = ""
        desc2    = ""

    PHASE_MAP = {
        0: ("PHASE ?", "Unclear",      "#94A3B8",
            "Mixed signals — not enough confirmation for a clear phase"),
        1: ("PHASE 1", "Basing",       "#38BDF8",
            "Consolidating below flat MA — OBV accumulating, wait for volume breakout"),
        2: ("PHASE 2", sublabel or "Uptrend", "#00FF88",
            desc2 or "Price above rising MA, higher highs/lows — buy zone"),
        3: ("PHASE 3", "Topping",      "#FACC15",
            "Distribution underway — lower highs, limp bars, OBV falling. Tighten stops"),
        4: ("PHASE 4", "Downtrend",    "#FF6B6B",
            "Price below declining MAs, lower lows — avoid all longs"),
    }

    label, sub, color, desc = PHASE_MAP[phase]
    conf_text = ["", "Low confidence", "Moderate confidence", "High confidence"][min(conf, 3)]

    return (phase, label, sub, color, conf, conf_text, desc)


def calc_signals(row):
    close = row['Close']
    sigs = {
        'MA20':  {'bull': close > row['MA20'],         'label': '20 MA',  'subtitle': 'short-term trend',  'val': 'Above' if close > row['MA20']  else 'Below'},
        'MA50':  {'bull': close > row['MA50'],         'label': '50 MA',  'subtitle': 'mid-term trend',     'val': 'Above' if close > row['MA50']  else 'Below'},
        'MA200': {'bull': close > row['MA200'],        'label': '200 MA', 'subtitle': 'long-term trend',    'val': 'Above' if close > row['MA200'] else 'Below'},
        'RSI':   {'bull': 40 < row['RSI'] < 70,       'label': 'RSI',    'subtitle': 'momentum',           'val': f"{row['RSI']:.1f}", 'neut': True},
        'MACD':  {'bull': row['MACD'] > row['MACDSig'],'label': 'MACD',  'subtitle': 'trend crossover',    'val': 'Bullish' if row['MACD'] > row['MACDSig'] else 'Bearish'},
        'OBV':   {'bull': row['OBV'] > 0,             'label': 'OBV',    'subtitle': 'volume flow',        'val': 'Rising' if row['OBV'] > 0 else 'Falling'},
        'Vol':   {'bull': row['VolTrend'] > 0.8,      'label': 'Volume', 'subtitle': 'vs average',         'val': f"{row['VolTrend']:.2f}x"},
        'ATR':   {'bull': row['ATRPct'] < 0.04,       'label': 'ATR',    'subtitle': 'volatility',         'val': f"${row['ATR']:.2f} ({row['ATRPct']*100:.1f}%)"},
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
    row   = df.iloc[-1]
    prev  = df.iloc[-2]
    close = float(row['Close'])
    cur   = "CA$" if ticker.endswith(".TO") else "$"

    last60 = ",".join(str(round(float(x), 2)) for x in df['Close'].tail(60))

    last5_parts = []
    for i in range(-5, 0):
        r = df.iloc[i]
        last5_parts.append(
            f"O:{float(r['Open']):.2f} H:{float(r['High']):.2f} "
            f"L:{float(r['Low']):.2f} C:{float(r['Close']):.2f} V:{float(r['Volume']):.0f}"
        )
    last5 = " | ".join(last5_parts)

    above_below = lambda v, ma: "ABOVE" if close > float(row[ma]) else "BELOW"
    ma_ctx = (f"Price vs MAs: {above_below(close,'MA20')} 20MA | "
              f"{above_below(close,'MA50')} 50MA | "
              f"{above_below(close,'MA200')} 200MA")

    prev_close = float(prev['Close'])
    chg_abs = close - prev_close
    chg_pct = (chg_abs / prev_close * 100) if prev_close else 0

    spy_sig = market_ctx.get('spy_signal', 'Unknown')
    qqq_sig = market_ctx.get('qqq_signal', 'Unknown')
    dia_sig = market_ctx.get('dia_signal', 'Unknown')
    spy_chg = market_ctx.get('spy_1m', 0)
    qqq_chg = market_ctx.get('qqq_1m', 0)
    dia_chg = market_ctx.get('dia_1m', 0)

    if news_items:
        headlines_text = "\n".join(f"- {n.get('title','')}" for n in news_items[:5])
    else:
        headlines_text = "No recent news available"

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
    fib382, fib500, fib618 = fibs

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
        f"Fib 38.2%: {fib382:.2f} | 50%: {fib500:.2f} | 61.8%: {fib618:.2f}\n\n"
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
        '{"verdict":"DAY TRADE|SWING TRADE|INVEST|AVOID|WATCH",'
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

    for ma, color, width in [('MA20','#38BDF8',1.5),('MA50','#F59E0B',1.5),('MA200','#FF6B6B',1.5),('MA100','#A78BFA',1)]:
        if ma in df.columns:
            fig.add_trace(go.Scatter(
                x=df.index, y=df[ma].values,
                name=ma, line=dict(color=color, width=width), opacity=0.85
            ), row=1, col=1)

    fig.add_trace(go.Bar(
        x=df.index,
        y=df['Volume'].values,
        name='Volume',
        marker=dict(color='rgba(56,189,248,0.35)'),
        showlegend=False
    ), row=2, col=1)

    fig.add_trace(go.Scatter(
        x=df.index, y=df['MACD'].values,
        name='MACD', line=dict(color='#38BDF8', width=1.2)
    ), row=3, col=1)
    fig.add_trace(go.Scatter(
        x=df.index, y=df['MACDSig'].values,
        name='Signal', line=dict(color='#F59E0B', width=1.2)
    ), row=3, col=1)

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

def sig_html(label, val, bull, neut=False, subtitle=""):
    cls = "sig-neut" if neut else ("sig-bull" if bull else "sig-bear")
    vcls = "sig-val-y" if neut else ("sig-val-g" if bull else "sig-val-r")
    prefix = "~ " if neut else ("+ " if bull else "− ")
    sub_html = f'<div style="font-size:11px;color:#F1F5F9;margin-top:3px;letter-spacing:0.3px;">{subtitle}</div>' if subtitle else ""
    return f'''<div class="{cls}">
      <div class="sig-label">{label}{info_icon(label)}</div>
      {sub_html}
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


# ── Weinstein Phase Support — module level (cached properly) ─
SECTOR_ETF_MAP = {
    # yfinance sector strings (exact match required)
    "Technology":                  "XLK",
    "Healthcare":                  "XLV",
    "Financials":                  "XLF",
    "Financial Services":          "XLF",
    "Energy":                      "XLE",
    "Consumer Cyclical":           "XLY",
    "Consumer Defensive":          "XLP",
    "Industrials":                 "XLI",
    "Utilities":                   "XLU",
    "Real Estate":                 "XLRE",
    "Basic Materials":             "XLB",
    "Communication Services":      "XLC",
    # FMP sector strings (may differ)
    "Information Technology":      "XLK",
    "Consumer Discretionary":      "XLY",
    "Consumer Staples":            "XLP",
    "Materials":                   "XLB",
    "Health Care":                 "XLV",
    "Telecommunication Services":  "XLC",
    "Electronic Components":       "XLK",
    "Semiconductors":              "XLK",
}

@st.cache_data(ttl=900, show_spinner=False)
def get_market_phase():
    try:
        spy_df = yf.Ticker("SPY").history(period="2y")
        spy_df['MA20']  = spy_df['Close'].rolling(20).mean()
        spy_df['MA50']  = spy_df['Close'].rolling(50).mean()
        spy_df['MA200'] = spy_df['Close'].rolling(200).mean()
        return detect_weinstein_phase(spy_df)
    except:
        return (0, 'PHASE ?', 'Unclear', '#94A3B8', 0, '', '')

@st.cache_data(ttl=900, show_spinner=False)
def get_sector_phase(sector_etf):
    try:
        s_df = yf.Ticker(sector_etf).history(period="2y")
        s_df['MA20']  = s_df['Close'].rolling(20).mean()
        s_df['MA50']  = s_df['Close'].rolling(50).mean()
        s_df['MA200'] = s_df['Close'].rolling(200).mean()
        return detect_weinstein_phase(s_df)
    except:
        return (0, 'PHASE ?', 'Unclear', '#94A3B8', 0, '', '')


# ── Main App ──────────────────────────────────────────────────
def main():
    # ── Sidebar: Reference Panel ─────────────────────────────
    with st.sidebar:

        # ── Header ───────────────────────────────────────────
        st.markdown("""
        <div style="padding:4px 0 16px;border-bottom:1px solid #1E2D42;margin-bottom:12px;">
          <div style="font-size:9px;color:#5EEAD4;letter-spacing:3px;
                      text-transform:uppercase;margin-bottom:5px;
                      font-family:'JetBrains Mono',monospace;">Stock Analysis HUD</div>
          <div style="font-size:16px;font-weight:800;color:#F1F5F9;
                      margin-bottom:2px;">Trading Reference</div>
          <div style="font-size:11px;color:#64748B;">Signal guide · Glossary · Controls</div>
        </div>""", unsafe_allow_html=True)

        # ── Signal Legend ─────────────────────────────────────
        with st.expander("📡  Signal Legend", expanded=False):
            st.markdown("""
            <div style="font-size:10px;color:#5EEAD4;letter-spacing:2px;
                        text-transform:uppercase;font-weight:700;margin-bottom:10px;
                        font-family:'JetBrains Mono',monospace;">8 SIGNALS EXPLAINED</div>
            """, unsafe_allow_html=True)

            signals_data = [
                ("20 MA",  "#38BDF8",
                 "20-Day Moving Average",
                 "Short-term trend. Price above = buyers in control this month. Below = recent weakness."),
                ("50 MA",  "#F59E0B",
                 "50-Day Moving Average",
                 "Mid-term trend. Institutional desks watch this line closely. A hold here = strong stock."),
                ("200 MA", "#FF6B6B",
                 "200-Day Moving Average",
                 "Long-term trend. Above = bull market for this stock. Below = be cautious or avoid."),
                ("RSI",    "#A78BFA",
                 "Relative Strength Index (0–100)",
                 "Momentum meter. Under 30 = oversold (possible bounce). Over 70 = overbought (possible pullback). 40–60 = healthy trend."),
                ("MACD",   "#5EEAD4",
                 "Moving Average Convergence Divergence",
                 "Trend + momentum crossover. MACD line above signal line = bullish. Histogram growing = momentum building."),
                ("OBV",    "#00FF88",
                 "On-Balance Volume",
                 "Tracks whether smart money is buying or selling. OBV rising while price is flat = accumulation — big move may be coming."),
                ("Volume", "#94A3B8",
                 "Volume vs 20-Day Average",
                 "Confirms the move. Breakout on 1.5× average volume = institutional participation. Breakout on low volume = unreliable."),
                ("ATR",    "#FACC15",
                 "Average True Range",
                 "Daily expected price swing. High ATR = volatile, wide stops needed. Low ATR = calm, tight stops work. Use for position sizing."),
            ]

            for abbr, color, full_name, explanation in signals_data:
                st.markdown(f"""
                <div style="background:#111827;border-left:3px solid {color};
                            border-radius:0 6px 6px 0;padding:9px 12px;margin-bottom:7px;">
                  <div style="display:flex;justify-content:space-between;align-items:center;
                              margin-bottom:4px;">
                    <span style="font-family:'JetBrains Mono',monospace;font-size:13px;
                                 font-weight:700;color:{color};">{abbr}</span>
                  </div>
                  <div style="font-size:11px;font-weight:600;color:#CBD5E1;
                              margin-bottom:3px;">{full_name}</div>
                  <div style="font-size:11px;color:#64748B;line-height:1.5;">{explanation}</div>
                </div>""", unsafe_allow_html=True)

            st.markdown("""
            <div style="background:#0A1525;border:1px solid #1E2D42;border-radius:6px;
                        padding:8px 10px;margin-top:4px;">
              <div style="font-size:10px;color:#5EEAD4;font-weight:700;margin-bottom:4px;">
                HOW SIGNALS COMBINE
              </div>
              <div style="font-size:11px;color:#64748B;line-height:1.6;">
                All 8 signals run at once. The number of green signals
                determines the score out of 10. A score of 7+ means at
                least 6 signals are bullish — a strong setup.
              </div>
            </div>""", unsafe_allow_html=True)

        # ── Score Guide ───────────────────────────────────────
        with st.expander("📊  Score Guide", expanded=False):
            st.markdown("""
            <div style="font-size:10px;color:#5EEAD4;letter-spacing:2px;
                        text-transform:uppercase;font-weight:700;margin-bottom:10px;
                        font-family:'JetBrains Mono',monospace;">WHAT EACH SCORE MEANS</div>
            """, unsafe_allow_html=True)

            score_bands = [
                (9, 10, "#00FF88", "0D2818", "Strong Bullish",
                 "Almost all signals aligned. High-conviction setup. Momentum + trend + volume all confirming."),
                (7,  8, "#00E87A", "0A2215", "Moderately Bullish",
                 "Most signals green. Solid setup with a few cautions. Look for clean entry near support."),
                (5,  6, "#FACC15", "251800", "Mixed — Neutral",
                 "Half and half. No clear edge. Wait for a signal to break one way. Patience is the trade here."),
                (3,  4, "#F97316", "2A1200", "Moderately Bearish",
                 "More signals red than green. Consider waiting or sizing down. Risk is elevated."),
                (0,  2, "#FF6B6B", "2D1015", "Strong Bearish",
                 "Most signals negative. Not the time to buy. If already in a position, review your stop loss."),
            ]

            for lo, hi, color, bg, label, desc in score_bands:
                bar_pct = int((hi / 10) * 100)
                lo_str = f"{lo}" if lo == hi else f"{lo}–{hi}"
                st.markdown(f"""
                <div style="background:#{bg};border:1px solid {color}33;
                            border-radius:6px;padding:10px 12px;margin-bottom:7px;">
                  <div style="display:flex;align-items:center;gap:10px;margin-bottom:5px;">
                    <span style="font-family:'JetBrains Mono',monospace;font-size:18px;
                                 font-weight:800;color:{color};min-width:34px;">{lo_str}</span>
                    <div style="flex:1;">
                      <div style="height:5px;background:#243348;border-radius:3px;overflow:hidden;">
                        <div style="width:{bar_pct}%;height:5px;background:{color};
                                    border-radius:3px;"></div>
                      </div>
                    </div>
                    <span style="font-size:11px;font-weight:700;color:{color};
                                 white-space:nowrap;">{label}</span>
                  </div>
                  <div style="font-size:11px;color:#94A3B8;line-height:1.5;">{desc}</div>
                </div>""", unsafe_allow_html=True)

            st.markdown("""
            <div style="background:#0A1525;border:1px solid #1E2D42;border-radius:6px;
                        padding:8px 10px;margin-top:2px;">
              <div style="font-size:10px;color:#5EEAD4;font-weight:700;margin-bottom:4px;">
                IMPORTANT NOTE
              </div>
              <div style="font-size:11px;color:#64748B;line-height:1.6;">
                The score measures technical signal alignment — not a
                buy/sell recommendation. A score of 9 in a bear market
                is still risky. Always check the AI Verdict and
                market context alongside the score.
              </div>
            </div>""", unsafe_allow_html=True)

        # ── Glossary ──────────────────────────────────────────
        with st.expander("📖  Glossary", expanded=False):
            st.markdown("""
            <div style="font-size:10px;color:#5EEAD4;letter-spacing:2px;
                        text-transform:uppercase;font-weight:700;margin-bottom:10px;
                        font-family:'JetBrains Mono',monospace;">KEY TERMS</div>
            """, unsafe_allow_html=True)

            glossary = [
                ("ATR",          "#FACC15",
                 "Average True Range. The average daily price swing over 14 days. "
                 "Used to set stop losses and gauge volatility."),
                ("Bollinger Bands", "#A78BFA",
                 "A price channel drawn 2 standard deviations above and below "
                 "the 20-day MA. Price near upper band = overbought. Near lower = oversold."),
                ("EPS",          "#38BDF8",
                 "Earnings Per Share. Net profit divided by shares outstanding. "
                 "Beat the estimate = stock usually gaps up. Miss = gaps down."),
                ("Fibonacci",    "#00FF88",
                 "Retracement levels (38.2%, 50%, 61.8%) based on a mathematical "
                 "sequence. Traders watch these as potential support/resistance in pullbacks."),
                ("Float",        "#94A3B8",
                 "The number of shares available to trade publicly. Low float "
                 "stocks are more volatile — small buying pressure = big moves."),
                ("MACD",         "#5EEAD4",
                 "Moving Average Convergence Divergence. Tracks trend momentum "
                 "by comparing two exponential moving averages (12 and 26 day)."),
                ("OBV",          "#00FF88",
                 "On-Balance Volume. Adds volume on up days, subtracts on down days. "
                 "Rising OBV with flat price = accumulation = bullish divergence."),
                ("P/E Ratio",    "#38BDF8",
                 "Price-to-Earnings. Stock price divided by annual EPS. "
                 "High P/E = expensive (or high growth expected). Low P/E = cheap (or declining)."),
                ("PEG Ratio",    "#A78BFA",
                 "P/E divided by earnings growth rate. Under 1 = potentially undervalued "
                 "relative to growth. Over 2 = expensive. Better than P/E alone."),
                ("Phase",        "#F97316",
                 "Weinstein Phase. Stocks cycle through 4 phases: "
                 "1=Base, 2=Uptrend (buy zone), 3=Top, 4=Downtrend (avoid). "
                 "Only trade Phase 2."),
                ("R:R Ratio",    "#FACC15",
                 "Risk-to-Reward. How much you can gain vs how much you risk. "
                 "1:2 means you risk $1 to make $2. Never take a trade below 1:1."),
                ("RSI",          "#A78BFA",
                 "Relative Strength Index (0–100). Above 70 = overbought. "
                 "Below 30 = oversold. Most reliable when it diverges from price."),
                ("Short Squeeze", "#FF6B6B",
                 "When a heavily shorted stock rises, forcing short sellers to "
                 "buy to cover losses — which drives the price even higher. Can be explosive."),
                ("Support",      "#00FF88",
                 "A price level where buying tends to outweigh selling — the "
                 "stock has 'bounced' from this level before. Below it = next support."),
                ("Resistance",   "#FF6B6B",
                 "A price level where selling tends to outweigh buying — the "
                 "stock has struggled to break through here before. Above it = breakout."),
                ("VWAP",         "#5EEAD4",
                 "Volume-Weighted Average Price. The average price weighted by "
                 "volume. Day traders use it as a key intraday reference line."),
            ]

            for term, color, definition in glossary:
                st.markdown(f"""
                <div style="padding:8px 0;border-bottom:1px solid #1E2D42;">
                  <div style="font-family:'JetBrains Mono',monospace;font-size:12px;
                               font-weight:700;color:{color};margin-bottom:3px;">{term}</div>
                  <div style="font-size:11px;color:#64748B;line-height:1.55;">{definition}</div>
                </div>""", unsafe_allow_html=True)

            st.markdown("""
            <div style="padding-top:8px;font-size:10px;color:#374151;
                        font-family:'JetBrains Mono',monospace;">
              More at investopedia.com
            </div>""", unsafe_allow_html=True)

        # ── System Controls ───────────────────────────────────
        st.markdown("""
        <div style="font-size:10px;color:#374151;letter-spacing:2px;
                    text-transform:uppercase;margin:16px 0 8px;
                    font-family:'JetBrains Mono',monospace;">SYSTEM</div>
        """, unsafe_allow_html=True)



        fmp_active = bool(st.secrets.get("FMP_API_KEY", ""))
        st.markdown(f"""
        <div style="background:#111827;border:1px solid #1E2D42;border-radius:6px;
                    padding:8px 10px;margin-top:8px;">
          <div style="display:flex;justify-content:space-between;align-items:center;
                      margin-bottom:4px;">
            <span style="font-size:10px;color:#64748B;">Data source</span>
            <span style="font-size:11px;font-weight:700;
                         color:{'#00FF88' if fmp_active else '#FACC15'};">
              {'🟢 FMP + yfinance' if fmp_active else '🟡 yfinance only'}
            </span>
          </div>
          <div style="display:flex;justify-content:space-between;align-items:center;">
            <span style="font-size:10px;color:#64748B;">Cache TTL</span>
            <span style="font-size:11px;color:#94A3B8;">60 min</span>
          </div>
        </div>""", unsafe_allow_html=True)

        st.markdown("""
        <div style="padding:16px 0 4px;font-size:10px;color:#243348;
                    line-height:1.6;text-align:center;">
          Educational only · Not financial advice
        </div>""", unsafe_allow_html=True)

    # ── Rest of main() ────────────────────────────────────────
    if 'analysis' not in st.session_state:
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            st.markdown("<br>", unsafe_allow_html=True)
            st.markdown('<div style="text-align:center;font-size:12px;color:#4A6080;letter-spacing:3px;text-transform:uppercase;margin-bottom:16px;">Stock Analysis · AI HUD</div>', unsafe_allow_html=True)

            tab1, tab2, tab3 = st.tabs(["📊 Stock Analysis", "🎙️ Earnings Call Analyzer", "📈 Screener"])

            with tab1:
                st.markdown('<div style="text-align:center;font-size:24px;font-weight:800;color:#F1F5F9;margin-bottom:6px;">Enter a ticker</div>', unsafe_allow_html=True)
                st.markdown('<div style="text-align:center;font-size:13px;color:#4A6080;margin-bottom:16px;">Type any symbol — a dropdown will guide you</div>', unsafe_allow_html=True)

                fmp_key_lp = st.secrets.get("FMP_API_KEY", "")

                ticker_in = st.text_input("", placeholder="NVDA",
                                          key="ticker_input",
                                          label_visibility="collapsed")
                ticker_upper = ticker_in.strip().upper() if ticker_in else ""

                prev_val = st.session_state.get('_prev_ticker_val', '')
                st.session_state['_prev_ticker_val'] = ticker_upper
                enter_pressed = (ticker_upper != '' and ticker_upper == prev_val)

                selected_ticker = None

                def render_identity_card(sym, name, exch, curr, name_found=True):
                    border = "#14B8A6" if name_found else "#FACC15"
                    glow   = "0 0 20px rgba(20,184,166,0.15)" if name_found else "0 0 20px rgba(250,204,21,0.15)"
                    badge_bg   = "#0A1E1C" if name_found else "#1A1000"
                    badge_col  = "#14B8A6" if name_found else "#FACC15"
                    badge_text = "✓ Confirmed" if name_found else "⚠ Verify"
                    name_col   = "#F1F5F9" if name_found else "#FACC15"
                    name_text  = name if name_found else "Name not found — verify this symbol"
                    exch_text  = f"{exch} &nbsp;·&nbsp; {curr}" if exch else "Exchange unknown"
                    st.markdown(f"""
                    <div style="background:linear-gradient(135deg,#0A1E2C 0%,#0D1525 100%);
                                border:1px solid {border};border-radius:10px;
                                padding:14px 18px;margin:8px 0 6px;
                                box-shadow:{glow};
                                display:flex;align-items:center;gap:16px;">
                      <div style="font-family:'JetBrains Mono',monospace;font-size:24px;
                                  font-weight:800;color:#00FF88;letter-spacing:3px;
                                  min-width:70px;text-shadow:0 0 12px #00FF8840;">{sym}</div>
                      <div style="flex:1;min-width:0;">
                        <div style="font-size:15px;font-weight:700;color:{name_col};
                                    margin-bottom:3px;white-space:nowrap;overflow:hidden;
                                    text-overflow:ellipsis;">{name_text}</div>
                        <div style="font-size:11px;color:#5EEAD4;letter-spacing:0.5px;">{exch_text}</div>
                      </div>
                      <div style="background:{badge_bg};border:1px solid {border};
                                  border-radius:20px;padding:3px 10px;
                                  font-size:10px;font-weight:700;color:{badge_col};
                                  letter-spacing:0.5px;white-space:nowrap;">{badge_text}</div>
                    </div>""", unsafe_allow_html=True)

                def render_dropdown(rows):
                    unique_names = list({r["name"] for r in rows if r["name"]})
                    if len(unique_names) > 1:
                        st.markdown("""
                        <div style="background:linear-gradient(135deg,#1A1000 0%,#2D1500 100%);
                                    border:1px solid #FACC15;border-radius:8px;
                                    padding:10px 16px;margin:8px 0 4px;
                                    display:flex;align-items:center;gap:10px;">
                          <span style="font-size:16px;">⚠️</span>
                          <div>
                            <div style="font-size:12px;font-weight:700;color:#FACC15;
                                        margin-bottom:2px;">Same symbol — different companies</div>
                            <div style="font-size:11px;color:#CBD5E1;">
                              This ticker symbol is used by different companies on different
                              exchanges. Read the full name carefully before selecting.
                            </div>
                          </div>
                        </div>""", unsafe_allow_html=True)

                    st.markdown("""
                    <div style="background:#071420;border:1px solid #14B8A6;border-radius:10px;
                                overflow:hidden;margin-top:4px;">
                      <div style="padding:6px 16px;font-size:10px;color:#5EEAD4;
                                  letter-spacing:2px;text-transform:uppercase;font-weight:700;
                                  border-bottom:1px solid #0D2030;">
                        Select exchange or share class
                      </div>""", unsafe_allow_html=True)

                    for i, row in enumerate(rows):
                        border_b = "" if i == len(rows)-1 else "border-bottom:1px solid #0D2030;"
                        st.markdown(f"""
                        <div style="padding:10px 16px;{border_b}display:flex;
                                    align-items:center;gap:12px;transition:background 150ms;">
                          <div style="font-family:'JetBrains Mono',monospace;font-weight:800;
                                      color:#00FF88;font-size:15px;min-width:80px;">{row['sym']}</div>
                          <div style="flex:1;">
                            <div style="font-size:13px;font-weight:600;color:#E2E8F0;
                                        margin-bottom:2px;">{row['name']}</div>
                            <div style="font-size:11px;color:#5EEAD4;">{row['exch']} · {row['curr']}</div>
                          </div>
                        </div>""", unsafe_allow_html=True)
                        if st.button(f"▶ Analyze {row['sym']}", key=row["key"],
                                     use_container_width=True):
                            st.session_state["_resolved_name"] = row["name"]
                            st.session_state["_resolved_exch"] = row["exch"]
                            st.session_state["_resolved_curr"] = row["curr"]
                            return row["sym"]

                    st.markdown("</div>", unsafe_allow_html=True)
                    return None

                if ticker_upper:
                    if ticker_upper in MULTI_LISTED:
                        rows = [{"sym": o["ticker"], "name": o["name"],
                                 "exch": o["exchange"], "curr": o["currency"],
                                 "key": f'ml_{o["ticker"]}'}
                                for o in MULTI_LISTED[ticker_upper]]
                        result = render_dropdown(rows)
                        if result:
                            selected_ticker = result

                    elif fmp_key_lp:
                        results = search_ticker_fmp(ticker_upper, fmp_key_lp)
                        if results:
                            exact = next((r for r in results
                                         if r.get("symbol","").upper() == ticker_upper), None)

                            if exact and len({r.get("symbol","").upper()
                                              for r in results
                                              if r.get("symbol","").upper() == ticker_upper}) == 1:
                                name = exact.get("name","")[:52]
                                exch = exact.get("exchangeShortName","")
                                curr = exact.get("currency","USD")
                                render_identity_card(ticker_upper, name, exch, curr)
                                st.session_state["_resolved_name"] = name
                                st.session_state["_resolved_exch"] = exch
                                st.session_state["_resolved_curr"] = curr
                                btn_lbl = f"Analyze {name} →" if name else "Analyze →"
                                if st.button(btn_lbl, type="primary",
                                             use_container_width=True, key="analyze_exact"):
                                    selected_ticker = ticker_upper
                            else:
                                rows = [{"sym":  r.get("symbol",""),
                                         "name": r.get("name","")[:45],
                                         "exch": r.get("exchangeShortName",""),
                                         "curr": r.get("currency","USD"),
                                         "key":  f'fmp_{r.get("symbol","")}_{r.get("exchangeShortName","")}'}
                                        for r in results[:10] if r.get("symbol","")]
                                result = render_dropdown(rows)
                                if result:
                                    selected_ticker = result

                        else:
                            cache_key = f"_yf_{ticker_upper}"
                            if cache_key not in st.session_state:
                                matches = resolve_all_matches(ticker_upper)
                                st.session_state[cache_key] = matches

                            matches = st.session_state.get(cache_key, [])

                            if len(matches) > 1:
                                rows = [{"sym":  m["sym"],
                                         "name": m["name"],
                                         "exch": m["exchange"],
                                         "curr": m["currency"],
                                         "key":  f'yf_{m["sym"]}_{m["exchange"]}'}
                                        for m in matches]
                                result = render_dropdown(rows)
                                if result:
                                    selected_ticker = result

                            elif len(matches) == 1:
                                m     = matches[0]
                                rname = m["name"]
                                rexch = m["exchange"]
                                rcurr = m["currency"]
                                rsym  = m["sym"]
                                render_identity_card(rsym, rname, rexch, rcurr, name_found=True)
                                st.session_state["_resolved_name"] = rname
                                st.session_state["_resolved_exch"] = rexch
                                st.session_state["_resolved_curr"] = rcurr
                                c1, c2 = st.columns([3, 1])
                                with c1:
                                    if st.button(f"Analyze {rname} →", type="primary",
                                                 use_container_width=True, key="analyze_yf"):
                                        selected_ticker = rsym
                                with c2:
                                    if st.button("↩ Reset", use_container_width=True,
                                                 key="analyze_reset"):
                                        st.session_state.pop(cache_key, None)
                                        st.rerun()

                            else:
                                render_identity_card(ticker_upper, "", "", "", name_found=False)
                                c1, c2 = st.columns([3, 1])
                                with c1:
                                    if st.button(f"Analyze {ticker_upper} →", type="primary",
                                                 use_container_width=True, key="analyze_yf_unknown"):
                                        selected_ticker = ticker_upper
                                with c2:
                                    if st.button("↩ Reset", use_container_width=True,
                                                 key="analyze_reset_unknown"):
                                        st.session_state.pop(cache_key, None)
                                        st.rerun()

                    else:
                        if st.button("Analyze →", type="primary",
                                     use_container_width=True, key="analyze_direct"):
                            selected_ticker = ticker_upper

                if selected_ticker:
                    run_analysis(selected_ticker)

                st.markdown('<div style="text-align:center;font-size:11px;color:#243348;margin-top:20px;">US · TSX · LSE · Euronext · HKEX · ASX — all major exchanges supported</div>', unsafe_allow_html=True)

                render_disclaimer()

            with tab2:
                render_earnings_analyzer()

            with tab3:
                render_screener()

        return

    render_hud()


# ── Disclaimer ────────────────────────────────────────────────
def render_disclaimer():
    st.markdown("""
    <div style="background:#1A1000;border:1px solid #FACC1544;border-radius:8px;
                padding:12px 18px;margin-top:24px;margin-bottom:8px;">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">
        <span style="font-size:15px;">&#9888;&#65039;</span>
        <span style="font-size:12px;color:#FACC15;font-weight:700;letter-spacing:0.03em;">
          Educational tool only - not financial advice
        </span>
      </div>
      <div style="font-size:11px;color:#CBD5E1;line-height:1.7;">
        AI-generated analysis does not guarantee any outcome. Always conduct your own
        research before making any investment decision. Never risk more than you can
        afford to lose. This tool is not registered with the AMF or any other
        securities regulator.
      </div>
    </div>""", unsafe_allow_html=True)

    col1, col2 = st.columns(2)
    with col1:
        with st.expander("Terms of Use"):
            st.markdown(
                "**Last updated: March 2026**\n\n"
                "**1. Educational Purpose Only** - This tool provides AI-generated stock "
                "analysis for educational purposes only. Nothing constitutes financial, "
                "investment, or trading advice.\n\n"
                "**2. No Warranty** - Provided as-is without warranty. AI analysis may be "
                "inaccurate, incomplete, or out of date. Market data is from third parties "
                "and may contain errors.\n\n"
                "**3. Limitation of Liability** - The operator shall not be liable for any "
                "financial losses arising from use of this platform. You trade at your own risk.\n\n"
                "**4. No Fiduciary Relationship** - Use of this tool does not create an "
                "advisory or professional relationship of any kind.\n\n"
                "**5. Regulatory Notice (Canada)** - This tool is not registered with the "
                "Autorite des marches financiers (AMF) or any other securities regulator.\n\n"
                "**6. Third-Party Services** - This platform uses Anthropic Claude API, "
                "yfinance, and Financial Modeling Prep (FMP). Your queries are processed "
                "by these services under their own terms.\n\n"
                "**7. Changes** - Terms may be updated at any time. Continued use constitutes acceptance."
            )
    with col2:
        with st.expander("Privacy Policy"):
            st.markdown(
                "**Last updated: March 2026**\n\n"
                "**What we collect** - We do not collect, store, or sell any personal "
                "information. No account creation or login required.\n\n"
                "**Third-party logging:**\n"
                "- Streamlit Cloud may log usage metadata\n"
                "- Anthropic processes ticker queries via Claude API\n"
                "- Financial Modeling Prep provides market data\n\n"
                "**Your queries** - Ticker symbols you submit are sent to Anthropic's API "
                "to generate AI analysis. Do not enter personally identifying information.\n\n"
                "**Cookies** - We do not set cookies. Streamlit may use session cookies "
                "for technical operation.\n\n"
                "**Contact** - Since we store no personal data, there is nothing to access "
                "or delete on our end."
            )

    st.markdown(
        '<div style="text-align:center;font-size:10px;color:#374151;'
        'padding:8px 0;letter-spacing:1px;">'
        'AI-GENERATED - NOT FINANCIAL ADVICE - EDUCATIONAL PURPOSES ONLY'
        '</div>',
        unsafe_allow_html=True
    )


def run_analysis(ticker):
    prog = st.empty()
    cache_key = f"_ticker_cache_{ticker.upper()}"
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
        prog.info(f"⏳ Fetching data for {ticker}...")
        fmp_key = st.secrets.get("FMP_API_KEY", "")
        data  = fetch_ticker_data(ticker, fmp_key, _v=15)
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
        # Weinstein phase
        phase_result = detect_weinstein_phase(df)
        st.session_state.phase_result = phase_result

        h52  = float(info.get('fiftyTwoWeekHigh', df['High'].tail(252).max()))
        l52  = float(info.get('fiftyTwoWeekLow',  df['Low'].tail(252).min()))
        rng  = h52 - l52
        fibs = [h52 - rng*0.382, h52 - rng*0.500, h52 - rng*0.618]

        prog.info("⏳ Fetching market context...")
        market_ctx = fetch_market_context()

        prog.info(f"⏳ Processing news for {ticker}...")
        try:
            for item in (data['news'] or [])[:5]:
                try:
                    title = (item.get('title') or item.get('content', {}).get('title', ''))
                    pub   = (item.get('publisher') or item.get('content', {}).get('provider', {}).get('displayName', ''))
                    link  = (item.get('link') or item.get('content', {}).get('canonicalUrl', {}).get('url', ''))
                    if title:
                        news_items.append({'title': str(title), 'publisher': str(pub), 'link': str(link)})
                except: pass
        except: pass

        prog.info(f"🤖 Running AI analysis for {ticker}... (10-15 sec)")
        analysis = get_claude_analysis(ticker, info, df, signals, score, fibs, news_items, market_ctx)
        if 'error' in analysis:
            prog.empty()
            st.error(f"Claude API error: {analysis['error']}")
            return

        prog.info("⏳ Processing analyst & earnings data...")
        target_mean = target_low = target_high = 0.0
        num_ana = 0
        rec_mean = 0.0
        rec_key = ''
        buy_cnt = hold_cnt = sell_cnt = 0

        try:
            at = data.get('analyst_targets') or {}
            if at and isinstance(at, dict):
                target_mean = float(at.get('mean') or 0)
                target_high = float(at.get('high') or 0)
                target_low  = float(at.get('low')  or 0)
        except: pass

        try:
            rs = data.get('rec_summary')
            if rs is not None and not (hasattr(rs,'empty') and rs.empty):
                r = rs.iloc[0]
                buy_cnt  = int((r.get('strongBuy',  0) or 0) + (r.get('buy',  0) or 0))
                hold_cnt = int(r.get('hold', 0) or 0)
                sell_cnt = int((r.get('strongSell', 0) or 0) + (r.get('sell', 0) or 0))
                num_ana  = buy_cnt + hold_cnt + sell_cnt
        except: pass

        if target_mean == 0:
            target_mean = float(info.get('targetMeanPrice') or info.get('targetPrice') or 0)
            target_low  = float(info.get('targetLowPrice')  or 0)
            target_high = float(info.get('targetHighPrice') or 0)
        if num_ana == 0:
            num_ana = int(info.get('numberOfAnalystOpinions') or info.get('numAnalystOpinions') or 0)

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
                    _ed = _ed[_ed.index <= pd.Timestamp.now()]
                    if not _ed.empty:
                        eh = _ed
            except: pass
        try:
            if eh is not None and not eh.empty:
                for _, er in eh.head(4).iterrows():
                    est = float(er.get('epsEstimate',  er.get('EPS Estimate',  er.get('estimate', 0))) or 0)
                    act = float(er.get('epsActual',    er.get('Reported EPS',  er.get('actual',   0))) or 0)
                    surp_raw = er.get('surprisePercent', er.get('Surprise(%)', er.get('surprise', None)))
                    if surp_raw is not None:
                        sv = float(surp_raw or 0)
                        surp = sv * 100 if abs(sv) <= 2 else sv
                    else:
                        surp = ((act - est) / abs(est) * 100) if est != 0 else 0
                    qtr = str(er.get('period', er.get('Date', er.name if hasattr(er, 'name') else '')))[:10]
                    if act != 0 or est != 0:
                        earnings_hist.append({'quarter': qtr, 'estimate': est,
                                              'actual': act, 'surprise': surp, 'beat': surp > 0})
        except: pass

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
                    shares = int(ri.get('Shares',      ri.get('shares', 0)) or 0)
                    val    = float(ri.get('Value',     ri.get('value', 0)) or 0)
                    text   = str(ri.get('Text',        ri.get('text', '')) or '')
                    trans  = str(ri.get('Transaction', ri.get('transaction', '')) or '')
                    name   = str(ri.get('Insider',     ri.get('filerName', ri.get('insider', ''))) or '')
                    role   = str(ri.get('Position',    ri.get('filerRelation', '')) or '')
                    date_i = str(ri.get('Date',        ri.get('startDate', '')) or '')
                    combined = (text + trans).lower()
                    is_sell = any(w in combined for w in ('sale', 'sell', 'dispose', 'disposed'))
                    is_buy  = (not is_sell and
                               any(w in combined for w in ('purchase', 'buy', 'acquisition', 'grant', 'award', 'exercise')))
                    if not is_buy and not is_sell:
                        is_buy = shares > 0
                    if name.strip():
                        insider_data.append({
                            'name': name[:22], 'role': role[:22],
                            'type': 'BUY' if is_buy else 'SELL',
                            'shares': abs(shares), 'value': abs(val),
                            'date': str(date_i)[:10]
                        })
        except: pass

        try:
            lr    = np.log(df['Close'] / df['Close'].shift(1)).dropna()
            hv30  = float(lr.tail(30).std() * np.sqrt(252) * 100)
            hv90  = float(lr.tail(90).std() * np.sqrt(252) * 100) if len(lr) >= 90 else hv30
            bb_m  = float(df['Close'].tail(20).mean())
            bb_s  = float(df['Close'].tail(20).std())
            bb_u  = bb_m + 2 * bb_s
            bb_l  = bb_m - 2 * bb_s
            bb_w  = (bb_u - bb_l) / bb_m * 100
            iv_from_info = float(info.get('impliedVolatility', 0) or 0) * 100
            iv = data.get('iv', 0) or iv_from_info
            cnow  = float(df['Close'].iloc[-1])
            bb_p  = (cnow - bb_l) / (bb_u - bb_l) * 100 if bb_u != bb_l else 50
            vol_data = {'hv_30': hv30, 'hv_90': hv90, 'bb_upper': bb_u, 'bb_lower': bb_l,
                        'bb_mid': bb_m, 'bb_width': bb_w, 'bb_pct': bb_p, 'iv': iv,
                        'iv_vs_hv': iv / hv30 if hv30 > 0 else 0}
        except: pass

        earn_date_str = analysis.get('earnings_date', 'Unknown') or 'Unknown'
        days_to_earn  = 0
        ned = None

        def parse_earn_date(val):
            try:
                if val is None: return None
                if isinstance(val, (int, float)) and val > 1e9:
                    ts = pd.Timestamp(val, unit='s')
                else:
                    ts = pd.Timestamp(val)
                if ts.tzinfo is not None:
                    ts = ts.tz_convert(None)
                return ts if ts > pd.Timestamp.now() else None
            except: return None

        try:
            cal = data.get('calendar')
            if cal is not None:
                if isinstance(cal, dict):
                    ed = cal.get('Earnings Date', cal.get('earningsDate'))
                    if ed is not None:
                        ned = parse_earn_date(ed[0] if isinstance(ed, (list, tuple)) else ed)
                elif hasattr(cal, 'columns') and 'Earnings Date' in cal.columns:
                    ned = parse_earn_date(cal['Earnings Date'].iloc[0])
                elif hasattr(cal, 'index') and 'Earnings Date' in cal.index:
                    ned = parse_earn_date(cal.loc['Earnings Date'].iloc[0])
        except: pass

        if ned is None:
            try:
                for key in ['earningsDate', 'nextEarningsDate', 'earningsTimestamp']:
                    val = info.get(key)
                    if val:
                        if isinstance(val, (list, tuple)): val = val[0]
                        ned = parse_earn_date(val)
                        if ned: break
            except: pass

        if ned is None:
            try:
                ed_df = data.get('earn_dates')
                if ed_df is not None and not ed_df.empty:
                    future = ed_df[ed_df.index > pd.Timestamp.now()]
                    if not future.empty:
                        ned = parse_earn_date(future.index[-1])
            except: pass

        if ned is not None:
            days_to_earn  = (ned - pd.Timestamp.now()).days
            earn_date_str = ned.strftime("%b %d, %Y")

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
            st.error("⏳ Yahoo Finance rate limit hit. Please wait 30 seconds and try again.")
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
    phase_result  = st.session_state.get('phase_result', (0, 'PHASE ?', 'Unclear', '#94A3B8', 0, '', ''))

    close    = float(row['Close'])
    prev_c   = float(prev['Close'])
    chg      = close - prev_c
    chg_pct  = (chg / prev_c) * 100 if prev_c else 0
    if ticker.endswith('.TO') or ticker.endswith('.CN'):
        cur = "CA$"; cur_code = "CAD"
    elif ticker.endswith('.L'):
        cur = "£"; cur_code = "GBP"
    elif ticker.endswith('.PA') or ticker.endswith('.DE') or ticker.endswith('.AS'):
        cur = "€"; cur_code = "EUR"
    elif ticker.endswith('.HK'):
        cur = "HK$"; cur_code = "HKD"
    else:
        cur = "$"; cur_code = "USD"
    sign     = "+" if chg >= 0 else ""
    vc       = VERDICT_COLORS.get(a.get('verdict','SWING TRADE'), VERDICT_COLORS['SWING TRADE'])
    score_col= "#00FF88" if score >= 7 else "#FACC15" if score >= 4 else "#FF6B6B"

    h52  = float(info.get('fiftyTwoWeekHigh', df['High'].tail(252).max()))
    l52  = float(info.get('fiftyTwoWeekLow',  df['Low'].tail(252).min()))
    vol  = float(row['Volume'])
    atr_pct = float(row['ATRPct'])

    company = info.get('longName', info.get('shortName', ticker))
    if company == ticker or company == ticker.replace('-','.'):
        for key, opts in MULTI_LISTED.items():
            for opt in opts:
                if opt['ticker'].upper() == ticker.upper():
                    company = opt['name']
                    break
    if not company or company == ticker or company == ticker.replace('-','.'):
        resolved = st.session_state.get("_resolved_name", "")
        if resolved:
            company = resolved
    sector  = info.get('sector', a.get('sector',''))
    exchange = 'TSX' if ticker.endswith('.TO') else 'LSE' if ticker.endswith('.L') else 'NYSE / NASDAQ'

    if st.button("← New ticker"):
        for k in ['analysis','df','info','ticker','signals','score','fibs','row','prev',
                  '_prev_ticker_val','rr_mode','_rr_ticker','_resolved_name',
                  '_resolved_exch','_resolved_curr','_verify_pending','_verify_ticker']:
            if k in st.session_state: del st.session_state[k]
        st.rerun()

    with st.expander("🔍 Data Sources Debug — click to inspect what was fetched", expanded=False):
        # ── Dev tools ──
        col_dev1, col_dev2 = st.columns(2)
        with col_dev1:
            if st.button("🔄 Clear Cache & Refresh", use_container_width=True):
                st.cache_data.clear()
                for k in list(st.session_state.keys()):
                    del st.session_state[k]
                st.rerun()
        with col_dev2:
            st.caption("Dev only — clears all cached data and session state")
        st.divider()
        d = st.session_state
        fmp_ok = bool(st.secrets.get("FMP_API_KEY",""))
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("**Company Name**")
            st.code(f"longName:  {info.get('longName','❌ missing')}\nshortName: {info.get('shortName','❌ missing')}")
            st.markdown("**Earnings Date**")
            st.code(f"earn_date_str: {d.get('earn_date_str','❌')}\ndays_to_earn:  {d.get('days_to_earn',0)}\nearningsDate in info: {info.get('earningsDate','❌')}")
            st.markdown("**Earnings History**")
            eh = d.get('earnings_hist',[])
            st.code(f"{len(eh)} quarters loaded\n{eh[:2] if eh else 'EMPTY — check FMP earnings-surprises'}")
        with col_b:
            st.markdown("**Analyst Data**")
            ad = d.get('analyst_data',{})
            st.code(f"target: {ad.get('target',0)}\nbuy: {ad.get('buy',0)} hold: {ad.get('hold',0)} sell: {ad.get('sell',0)}\nnum_analysts: {ad.get('num_analysts',0)}\nrec_key: {ad.get('rec_key','❌')}")
            st.markdown("**Insider Transactions**")
            ins = d.get('insider_data',[])
            st.code(f"{len(ins)} transactions loaded\n{ins[:1] if ins else 'EMPTY'}")
            st.markdown(f"**FMP key active:** {'✅ Yes' if fmp_ok else '❌ No — add FMP_API_KEY to secrets'}")

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
        <div style="margin-top:5px;display:flex;gap:6px;justify-content:flex-end;flex-wrap:wrap;">
          {"<span style='background:#0A3020;border:1px solid #00FF88;border-radius:4px;padding:2px 8px;font-size:10px;color:#00FF88;letter-spacing:1px;'>&#x26A1; FMP</span>" if st.secrets.get("FMP_API_KEY","") else "<span style='background:#2A1500;border:1px solid #FACC15;border-radius:4px;padding:2px 8px;font-size:10px;color:#FACC15;letter-spacing:1px;'>&#x26A0; yfinance</span>"}
          <span style="background:{phase_result[3]}18;border:1px solid {phase_result[3]};border-radius:4px;padding:2px 10px;font-size:10px;color:{phase_result[3]};letter-spacing:1px;font-weight:800;font-family:'JetBrains Mono',monospace;">{phase_result[1]} · {phase_result[2]}</span>
        </div>
      </div>
    </div>''', unsafe_allow_html=True)

    import zoneinfo, streamlit.components.v1 as components
    try:
        params  = st.query_params
        user_tz = params.get("tz", "")
        if not user_tz:
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
            user_tz = "UTC"
        try:
            tz_obj   = zoneinfo.ZoneInfo(user_tz)
            analyzed = datetime.now(tz_obj).strftime("%b %d · %I:%M %p")
        except:
            analyzed = datetime.now().strftime("%b %d · %I:%M %p")
    except:
        analyzed = datetime.now().strftime("%b %d · %I:%M %p")
    st.markdown(f'''
    <div class="status-bar" style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:4px;">
      <div style="display:flex;gap:16px;align-items:flex-end;flex-wrap:wrap;">
        <div style="text-align:center;"><div style="color:#99F6E4;font-weight:700;">{row['Open']:.2f}</div><div style="font-size:12px;color:#F1F5F9;letter-spacing:1px;text-transform:uppercase;">Open</div></div>
        <div style="text-align:center;"><div style="color:#99F6E4;font-weight:700;">{row['High']:.2f}</div><div style="font-size:12px;color:#F1F5F9;letter-spacing:1px;text-transform:uppercase;">High</div></div>
        <div style="text-align:center;"><div style="color:#99F6E4;font-weight:700;">{row['Low']:.2f}</div><div style="font-size:12px;color:#F1F5F9;letter-spacing:1px;text-transform:uppercase;">Low</div></div>
        <div style="text-align:center;"><div style="color:#99F6E4;font-weight:700;">{fmt_vol(vol)}</div><div style="font-size:12px;color:#F1F5F9;letter-spacing:1px;text-transform:uppercase;">Volume</div></div>
        <div style="text-align:center;"><div style="color:#99F6E4;font-weight:700;">{row['VolTrend']:.2f}x</div><div style="font-size:12px;color:#F1F5F9;letter-spacing:1px;text-transform:uppercase;">Avg Vol</div></div>
        <div style="text-align:center;"><div style="color:#99F6E4;font-weight:700;">{cur}{float(row["ATR"]):.2f} ({atr_pct*100:.1f}%)</div><div style="font-size:12px;color:#F1F5F9;letter-spacing:1px;text-transform:uppercase;">Daily Range</div></div>
      </div>
      <div style="color:#5EEAD4;font-size:11px;">{analyzed}</div>
    </div>''', unsafe_allow_html=True)

    # ── Volume Breakout Flag ─────────────────────────────────
    vol_ratio    = float(row['VolTrend'])
    price_20d_high = float(df['Close'].rolling(20).max().iloc[-2])  # yesterday's 20d max
    price_break  = close > price_20d_high
    vol_confirm  = vol_ratio >= 1.5
    vol_surge    = vol_ratio >= 2.0

    if price_break and vol_confirm:
        st.markdown(f'''<div style="background:#052A14;border:1px solid #00FF88;border-radius:8px;
            padding:8px 16px;margin:6px 0;display:flex;align-items:center;gap:10px;">
          <span style="font-size:16px;">⚡</span>
          <div>
            <span style="color:#00FF88;font-weight:800;font-size:13px;font-family:'JetBrains Mono',monospace;">
              BREAKOUT CONFIRMED</span>
            <span style="color:#86EFAC;font-size:12px;margin-left:10px;">
              Price broke 20-day high on {vol_ratio:.1f}× average volume — institutional participation confirmed</span>
          </div></div>''', unsafe_allow_html=True)
    elif price_break and not vol_confirm:
        st.markdown(f'''<div style="background:#251800;border:1px solid #FACC15;border-radius:8px;
            padding:8px 16px;margin:6px 0;display:flex;align-items:center;gap:10px;">
          <span style="font-size:16px;">⚠️</span>
          <div>
            <span style="color:#FACC15;font-weight:800;font-size:13px;font-family:'JetBrains Mono',monospace;">
              BREAKOUT UNCONFIRMED</span>
            <span style="color:#FDE68A;font-size:12px;margin-left:10px;">
              Price broke 20-day high but volume only {vol_ratio:.1f}× average — wait for volume confirmation</span>
          </div></div>''', unsafe_allow_html=True)
    elif vol_surge and not price_break:
        st.markdown(f'''<div style="background:#0A1525;border:1px solid #38BDF8;border-radius:8px;
            padding:8px 16px;margin:6px 0;display:flex;align-items:center;gap:10px;">
          <span style="font-size:16px;">📊</span>
          <div>
            <span style="color:#38BDF8;font-weight:800;font-size:13px;font-family:'JetBrains Mono',monospace;">
              VOLUME SURGE</span>
            <span style="color:#BAE6FD;font-size:12px;margin-left:10px;">
              {vol_ratio:.1f}× average volume — unusual activity, watch for a price move</span>
          </div></div>''', unsafe_allow_html=True)

    # ── OBV Divergence Flag ──────────────────────────────────
    obv_div = int(df['OBV_div'].iloc[-1]) if 'OBV_div' in df.columns else 0
    if obv_div == 1:
        st.markdown('''<div style="background:#052A14;border:1px solid #00FF88;border-left:4px solid #00FF88;
            border-radius:8px;padding:8px 16px;margin:6px 0;display:flex;align-items:center;gap:10px;">
          <span style="font-size:16px;">📈</span>
          <div>
            <span style="color:#00FF88;font-weight:800;font-size:13px;font-family:'JetBrains Mono',monospace;">
              BULLISH OBV DIVERGENCE</span>
            <span style="color:#86EFAC;font-size:12px;margin-left:10px;">
              Price declining but OBV rising — institutions accumulating quietly. Phase 2 breakout may be loading.</span>
          </div></div>''', unsafe_allow_html=True)
    elif obv_div == -1:
        st.markdown('''<div style="background:#2D1015;border:1px solid #FF6B6B;border-left:4px solid #FF6B6B;
            border-radius:8px;padding:8px 16px;margin:6px 0;display:flex;align-items:center;gap:10px;">
          <span style="font-size:16px;">📉</span>
          <div>
            <span style="color:#FF6B6B;font-weight:800;font-size:13px;font-family:'JetBrains Mono',monospace;">
              BEARISH OBV DIVERGENCE</span>
            <span style="color:#FCA5A5;font-size:12px;margin-left:10px;">
              Price rising but OBV falling — smart money distributing. Phase 3 topping signal — tighten stops.</span>
          </div></div>''', unsafe_allow_html=True)

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
    st.markdown(f"""
    <div style="background:#1A2232;border:1px solid #14B8A6;border-top:2px solid #14B8A6;
                border-radius:8px;padding:14px 18px;margin-top:6px;">
      <div style="font-size:10px;color:#5EEAD4;letter-spacing:2px;text-transform:uppercase;
                  margin-bottom:8px;font-weight:600;">AI Summary</div>
      <div style="font-size:13px;color:#E2E8F0;line-height:1.8;">{a.get('summary','')}</div>
    </div>""", unsafe_allow_html=True)

    # ── WEINSTEIN PHASE + THREE TAILWINDS ────────────────────
    ph_num, ph_label, ph_sub, ph_col, ph_conf, ph_conf_text, ph_desc = phase_result

    sector_name = info.get('sector', '')
    sector_etf  = SECTOR_ETF_MAP.get(sector_name, '')

    mkt_phase    = get_market_phase()
    sec_phase    = get_sector_phase(sector_etf) if sector_etf else (0,'N/A','No ETF','#94A3B8',0,'','')

    # Three Tailwinds score
    tw_market = 1 if mkt_phase[0] == 2 else 0
    tw_sector = 1 if sec_phase[0] == 2 else 0
    tw_stock  = 1 if ph_num == 2 else 0
    tw_score  = tw_market + tw_sector + tw_stock
    tw_col    = "#00FF88" if tw_score == 3 else "#FACC15" if tw_score == 2 else "#F97316" if tw_score == 1 else "#FF6B6B"
    tw_label  = {3: "All tailwinds aligned ✓", 2: "2 of 3 aligned", 1: "1 of 3 aligned", 0: "No tailwinds"}[tw_score]

    phase_conf_colors = ["#94A3B8", "#F97316", "#FACC15", "#00FF88"]
    ph_conf_col = phase_conf_colors[min(ph_conf, 3)]

    st.markdown(f'''
    <div style="background:#0D1525;border:1px solid #1E2D42;border-radius:10px;
                padding:14px 18px;margin:8px 0;display:grid;
                grid-template-columns:1fr 1fr 1fr 1fr;gap:12px;">

      <div style="border-right:1px solid #1E2D42;padding-right:12px;">
        <div style="font-size:9px;color:#5EEAD4;letter-spacing:2px;text-transform:uppercase;
                    font-family:'JetBrains Mono',monospace;margin-bottom:6px;">MARKET · SPY</div>
        <div style="font-size:16px;font-weight:800;color:{mkt_phase[3]};
                    font-family:'JetBrains Mono',monospace;">{mkt_phase[1]}</div>
        <div style="font-size:11px;color:{mkt_phase[3]};margin-top:2px;">{mkt_phase[2]}</div>
        <div style="font-size:10px;color:#64748B;margin-top:4px;line-height:1.4;">{mkt_phase[6][:55]}</div>
      </div>

      <div style="border-right:1px solid #1E2D42;padding-right:12px;">
        <div style="font-size:9px;color:#5EEAD4;letter-spacing:2px;text-transform:uppercase;
                    font-family:'JetBrains Mono',monospace;margin-bottom:6px;">SECTOR · {sector_etf or "N/A"}</div>
        <div style="font-size:16px;font-weight:800;color:{sec_phase[3]};
                    font-family:'JetBrains Mono',monospace;">{sec_phase[1]}</div>
        <div style="font-size:11px;color:{sec_phase[3]};margin-top:2px;">{sec_phase[2]}</div>
        <div style="font-size:10px;color:#64748B;margin-top:4px;line-height:1.4;">{sec_phase[6][:55]}</div>
      </div>

      <div style="border-right:1px solid #1E2D42;padding-right:12px;">
        <div style="font-size:9px;color:#5EEAD4;letter-spacing:2px;text-transform:uppercase;
                    font-family:'JetBrains Mono',monospace;margin-bottom:6px;">STOCK · {ticker}</div>
        <div style="font-size:16px;font-weight:800;color:{ph_col};
                    font-family:'JetBrains Mono',monospace;">{ph_label}</div>
        <div style="font-size:11px;color:{ph_col};margin-top:2px;">{ph_sub}</div>
        <div style="font-size:10px;color:{ph_conf_col};margin-top:4px;">{ph_conf_text}</div>
        <div style="font-size:10px;color:#64748B;margin-top:2px;line-height:1.4;">{ph_desc[:55]}</div>
      </div>

      <div>
        <div style="font-size:9px;color:#5EEAD4;letter-spacing:2px;text-transform:uppercase;
                    font-family:'JetBrains Mono',monospace;margin-bottom:6px;">THREE TAILWINDS</div>
        <div style="font-size:36px;font-weight:800;color:{tw_col};
                    font-family:'JetBrains Mono',monospace;line-height:1;">{tw_score}<span style="font-size:18px;color:#4A6080;">/3</span></div>
        <div style="font-size:11px;color:{tw_col};margin-top:4px;font-weight:700;">{tw_label}</div>
        <div style="display:flex;gap:4px;margin-top:8px;">
          <div style="width:28px;height:6px;border-radius:3px;background:{"#00FF88" if tw_market else "#243348"};"></div>
          <div style="width:28px;height:6px;border-radius:3px;background:{"#00FF88" if tw_sector else "#243348"};"></div>
          <div style="width:28px;height:6px;border-radius:3px;background:{"#00FF88" if tw_stock else "#243348"};"></div>
        </div>
        <div style="font-size:9px;color:#374151;margin-top:4px;">Market · Sector · Stock</div>
      </div>

    </div>''', unsafe_allow_html=True)

    sig_keys = ['MA20','MA50','MA200','RSI','MACD','OBV','Vol','ATR']
    cols = st.columns(8)
    for i, k in enumerate(sig_keys):
        s = signals[k]
        with cols[i]:
            st.markdown(sig_html(s['label'], s['val'], s['bull'], s.get('neut', False), s.get('subtitle', '')), unsafe_allow_html=True)

    vwap    = float(a.get('vwap', close))
    ema100  = float(a.get('ema100', float(row['MA100'])))
    fib382, fib500, fib618 = fibs

    c1, c2 = st.columns(2)
    with c1:
        st.markdown('<div class="section-header">Key Levels & Technical Indicators</div>', unsafe_allow_html=True)
        levels_html = '<div class="panel-body">'
        atr_dollar = float(row['ATR'])
        atr_low    = round(close - atr_dollar, 2)
        atr_high   = round(close + atr_dollar, 2)
        levels_html += data_row("Entry zone", f"{cur}{a.get('entry_low',0):.2f} – {cur}{a.get('entry_high',0):.2f}", "val-y")
        # ADR% — Average Daily Range as % of price (ebook: selection filter)
        adr_pct = (atr_dollar / close) * 100
        if adr_pct < 1.5:
            adr_label = "Too slow"; adr_cls = "val-r"
        elif adr_pct <= 4.0:
            adr_label = "Sweet spot ✓"; adr_cls = "val-g"
        elif adr_pct <= 6.0:
            adr_label = "High momentum"; adr_cls = "val-y"
        else:
            adr_label = "Dangerous"; adr_cls = "val-r"
        levels_html += data_row("ATR (14)",   f"{cur}{atr_dollar:.2f}  →  range {cur}{atr_low:.2f} – {cur}{atr_high:.2f}", "val-b", True)
        levels_html += data_row("ADR %",      f"{adr_pct:.1f}%  —  {adr_label}", adr_cls)
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
        def _get(keys, default=0):
            for k in (keys if isinstance(keys, list) else [keys]):
                v = info.get(k)
                if v is not None and v != 0 and v != '':
                    return v
            return default
        def _pct(keys, claude_key, default=0):
            v = _get(keys, None)
            if v is None:
                v = a.get(claude_key, 0) or 0
                return float(v)
            v = float(v)
            return v * 100 if abs(v) <= 2 else v
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
        if mc == 0:
            shares = float(_get(['sharesOutstanding','impliedSharesOutstanding'], 0) or 0)
            if shares > 0: mc = shares * close
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

    # ── VOLATILITY ───────────────────────────────────────────
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
        hv_rows += f'<div class="vol-row"><span class="vol-lbl">Volatility 30d <a href="https://www.investopedia.com/terms/h/historicalvolatility.asp" target="_blank" style="color:#4A6080;text-decoration:none;font-size:10px;">ⓘ</a></span><span style="color:{hv_col};font-weight:700;font-family:monospace;">{hv_30:.1f}%</span></div>'
        hv_rows += f'<div class="vol-row"><span class="vol-lbl">Volatility 90d</span><span style="color:{hv_col};font-weight:700;font-family:monospace;">{hv_90:.1f}%</span></div>'
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

    # ── ANALYST RATINGS ──────────────────────────────────────
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

    # ── REASONS ───────────────────────────────────────────────
    bulls = a.get('reasons_bull', [])
    bears = a.get('reasons_bear', [])
    c1, c2 = st.columns(2)
    with c1:
        for b in bulls:
            st.markdown(f'<div class="reason-bull">+ &nbsp;{b}</div>', unsafe_allow_html=True)
    with c2:
        for b in bears:
            st.markdown(f'<div class="reason-bear">− &nbsp;{b}</div>', unsafe_allow_html=True)

    # ── TIMEFRAMES ────────────────────────────────────────────
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(f'<div class="tf-day"><div class="tf-label" style="color:#FACC15;">Day Trade</div><div class="tf-note">{a.get("day_trade_note","")}</div></div>', unsafe_allow_html=True)
    with c2:
        st.markdown(f'<div class="tf-swing"><div class="tf-label" style="color:#38BDF8;">Swing Trade</div><div class="tf-note">{a.get("swing_note","")}</div></div>', unsafe_allow_html=True)
    with c3:
        st.markdown(f'<div class="tf-inv"><div class="tf-label" style="color:#00FF88;">Invest</div><div class="tf-note">{a.get("invest_note","")}</div></div>', unsafe_allow_html=True)

    # ── R/R CALCULATOR ────────────────────────────────────────
    st.markdown('<div class="section-header" style="margin-top:8px;">⚡ Risk / Reward Calculator</div>', unsafe_allow_html=True)
    st.markdown("""
    <div style="background:#1A1000;border:1px solid #FACC1544;border-radius:0;border-top:none;
                padding:7px 14px;margin-bottom:4px;display:flex;align-items:center;gap:8px;">
      <span style="font-size:13px;">⚠️</span>
      <span style="font-size:11px;color:#FACC15;font-weight:700;">NOT FINANCIAL ADVICE</span>
      <span style="font-size:11px;color:#CBD5E1;">These numbers are for educational position-sizing practice only.</span>
    </div>""", unsafe_allow_html=True)

    atr_val  = float(row['ATR'])
    verdict  = a.get('verdict', 'SWING TRADE')
    s1       = float(a.get('support1', 0) or 0)
    s2       = float(a.get('support2', 0) or 0)
    r1       = float(a.get('resistance1', 0) or 0)
    r2       = float(a.get('resistance2', 0) or 0)
    ma200    = float(row.get('MA200', close))
    entry_mid = round((float(a.get('entry_low', close)) + float(a.get('entry_high', close))) / 2, 2)
    if entry_mid == 0: entry_mid = round(close, 2)

    def calc_presets(mode):
        if mode == "Day Trade":
            stp = round(entry_mid - 0.5 * atr_val, 2)
            tgt = round(entry_mid + 1.5 * atr_val, 2)
        elif mode == "Swing Trade":
            stp = round(entry_mid - 1.5 * atr_val, 2)
            if s1 > 0 and s1 < entry_mid and s1 > stp:
                stp = round(s1 - 0.01, 2)
            tgt = r1 if r1 > entry_mid else round(entry_mid + 3 * atr_val, 2)
        else:
            stp = round(entry_mid - 3 * atr_val, 2)
            deep = min(ma200, s2 if s2 > 0 else ma200)
            if deep > 0 and deep < entry_mid and deep > stp:
                stp = round(deep - 0.01, 2)
            tgt = r2 if r2 > entry_mid else round(entry_mid + 6 * atr_val, 2)
        return max(0.01, stp), max(entry_mid + 0.01, tgt)

    verdict_to_mode = {'DAY TRADE':'Day Trade','SWING TRADE':'Swing Trade','INVEST':'Invest','WATCH':'Swing Trade','AVOID':'Swing Trade'}
    default_mode = verdict_to_mode.get(verdict, 'Swing Trade')

    if st.session_state.get('_rr_ticker') != ticker:
        st.session_state['rr_mode']    = default_mode
        st.session_state['_rr_ticker'] = ticker
    elif 'rr_mode' not in st.session_state:
        st.session_state['rr_mode'] = default_mode

    vc_mode = VERDICT_COLORS.get(verdict, VERDICT_COLORS['SWING TRADE'])
    ai_label_html = f'<span style="background:{vc_mode["bg"]};border:1px solid {vc_mode["border"]};border-radius:4px;padding:2px 8px;font-size:10px;color:{vc_mode["color"]};font-weight:700;letter-spacing:1px;">AI: {verdict}</span>'
    cur_badge = f'<span style="background:#1C2A3A;border:1px solid #38BDF8;border-radius:4px;padding:2px 8px;font-size:10px;color:#38BDF8;font-weight:700;letter-spacing:1px;margin-left:6px;">💱 {cur_code}</span>'

    st.markdown(f'''
    <div style="background:#1A2232;border:1px solid #243348;border-radius:0 0 8px 8px;padding:12px 16px 10px;">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
        <div>Pre-filled from AI analysis · Adjust any value to recalculate</div>
        <div>{ai_label_html}{cur_badge}</div>
      </div>
    </div>''', unsafe_allow_html=True)

    selected_mode = st.session_state['rr_mode']
    mode_colors   = {"Day Trade": "#FACC15", "Swing Trade": "#38BDF8", "Invest": "#00FF88"}

    btn1, btn2, btn3, _ = st.columns([1.2, 1.2, 1.2, 2.4])
    for col, mode in zip([btn1, btn2, btn3], ["Day Trade", "Swing Trade", "Invest"]):
        with col:
            is_active  = selected_mode == mode
            is_ai_pick = mode == default_mode
            mc         = mode_colors[mode]
            label      = f"{'▶ ' if is_active else ''}{mode}{'  ← AI' if is_ai_pick else ''}"
            if is_active:
                st.markdown(f'<div style="border:2px solid {mc};border-radius:8px;margin-bottom:2px;">', unsafe_allow_html=True)
            if st.button(label, key=f"rr_mode_{mode}", use_container_width=True):
                st.session_state['rr_mode'] = mode
                st.rerun()
            if is_active:
                st.markdown('</div>', unsafe_allow_html=True)

    stop_preset, target_preset = calc_presets(selected_mode)
    rr_c1, rr_c2, rr_c3, rr_c4, rr_c5 = st.columns(5)
    with rr_c1:
        position_size_input = st.number_input(f"Position Size ({cur})", min_value=1.0, max_value=10000000.0, value=5000.0, step=500.0, key="rr_position")
    with rr_c2:
        risk_pct = st.number_input("Risk (%)", min_value=0.1, max_value=100.0, value=5.0, step=0.5, key="rr_risk_pct")
    with rr_c3:
        entry_price = st.number_input(f"Entry ({cur})", min_value=0.01, value=float(entry_mid), step=0.01, key="rr_entry", format="%.2f")

    shares_derived = int(position_size_input / entry_price) if entry_price > 0 else 0
    dollar_risk    = round(position_size_input * (risk_pct / 100), 2)
    derived_stop   = round(entry_price - (dollar_risk / shares_derived), 2) if shares_derived > 0 else stop_preset
    derived_stop   = max(0.01, derived_stop)

    with rr_c4:
        stop_price = st.number_input(f"Stop Loss ({cur})", min_value=0.01, value=float(derived_stop), step=0.01, key=f"rr_stop_{selected_mode}_{round(derived_stop,2)}", format="%.2f")
    with rr_c5:
        target_price = st.number_input(f"Target ({cur})", min_value=0.01, value=float(target_preset), step=0.01, key=f"rr_target_{selected_mode}", format="%.2f")

    risk_per_share   = round(abs(entry_price - stop_price), 2)
    reward_per_share = round(abs(target_price - entry_price), 2)
    rr_ratio         = round(reward_per_share / risk_per_share, 2) if risk_per_share > 0 else 0
    position_size    = shares_derived
    actual_loss      = round(position_size * risk_per_share, 2)
    actual_gain      = round(position_size * reward_per_share, 2)
    loss_pct         = round((actual_loss / position_size_input) * 100, 1) if position_size_input > 0 else 0
    stop_pct         = round((risk_per_share / entry_price) * 100, 2) if entry_price > 0 else 0
    target_pct       = round((reward_per_share / entry_price) * 100, 2) if entry_price > 0 else 0
    rr_col           = "#00FF88" if rr_ratio >= 2 else "#FACC15" if rr_ratio >= 1 else "#FF6B6B"
    rr_label         = "Excellent" if rr_ratio >= 3 else "Good" if rr_ratio >= 2 else "Acceptable" if rr_ratio >= 1 else "Poor — avoid"

    rc1, rc2, rc3 = st.columns(3)
    with rc1:
        st.markdown(f'''<div class="earn-bar" style="border-left-color:{rr_col};margin-top:6px;">
          <div class="earn-label">Risk/Reward Ratio</div>
          <div class="earn-val" style="color:{rr_col};font-size:26px;letter-spacing:1px;">1 : {rr_ratio}</div>
          <div style="font-size:11px;color:{rr_col};margin-top:3px;font-weight:700;">{rr_label}</div>
        </div>''', unsafe_allow_html=True)
    with rc2:
        st.markdown(f'''<div class="earn-bar" style="border-left-color:#38BDF8;margin-top:6px;">
          <div class="earn-label">Shares to Buy</div>
          <div class="earn-val" style="color:#38BDF8;font-size:22px;">{position_size:,} <span style="font-size:13px;">shares</span></div>
          <div style="font-size:11px;color:#64748B;margin-top:3px;">{cur}{position_size_input:,.0f} position · {position_size:,} × {cur}{entry_price:.2f}</div>
        </div>''', unsafe_allow_html=True)
    with rc3:
        st.markdown(f'''<div class="earn-bar" style="border-left-color:#818CF8;margin-top:6px;">
          <div class="earn-label">Worst Case &nbsp;/&nbsp; Best Case</div>
          <div class="earn-val" style="color:#FF6B6B;font-size:18px;">−{cur}{actual_loss:,.0f} <span style="font-size:10px;color:#FF6B6B88;">({loss_pct:.1f}% of position)</span></div>
          <div style="font-size:16px;color:#00FF88;font-weight:700;font-family:monospace;margin-top:4px;">+{cur}{actual_gain:,.0f} <span style="font-size:10px;color:#00FF8888;">if target hit</span></div>
        </div>''', unsafe_allow_html=True)

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
        loss_x   = min(stop_x, entry_x)
        loss_w   = abs(entry_x - stop_x)
        gain_x   = min(entry_x, target_x)
        gain_w   = abs(target_x - entry_x)
        svg = f'''<svg viewBox="0 0 {SVG_W} {SVG_H}" xmlns="http://www.w3.org/2000/svg"
             style="width:100%;height:auto;background:#0E1828;border-radius:10px;
                    border:1px solid #243348;display:block;margin-top:8px;">
          <rect x="{loss_x:.1f}" y="{BAR_Y}" width="{loss_w:.1f}" height="{BAR_H}" fill="#FF6B6B" fill-opacity="0.25" rx="2"/>
          <rect x="{gain_x:.1f}" y="{BAR_Y}" width="{gain_w:.1f}" height="{BAR_H}" fill="#00FF88" fill-opacity="0.25" rx="2"/>
          <text x="{(entry_x + target_x)/2:.1f}" y="{BAR_Y - 32}" text-anchor="middle" fill="{rr_col}" font-size="11" font-family="monospace" font-weight="700">R:R  1:{rr_ratio}  {rr_label}</text>
          <line x1="{stop_x:.1f}" y1="{BAR_Y-8}" x2="{stop_x:.1f}" y2="{BAR_Y+BAR_H+8}" stroke="#FF6B6B" stroke-width="2" stroke-dasharray="4,3"/>
          <text x="{stop_x:.1f}" y="{BAR_Y-16}" text-anchor="middle" fill="#FF6B6B" font-size="10" font-family="monospace" font-weight="700">STOP</text>
          <text x="{stop_x:.1f}" y="{BAR_Y+BAR_H+20}" text-anchor="middle" fill="#FF6B6B" font-size="11" font-family="monospace">{cur}{stop_price:.2f}</text>
          <text x="{stop_x:.1f}" y="{BAR_Y+BAR_H+34}" text-anchor="middle" fill="#FF6B6B" font-size="10" font-family="monospace" opacity="0.7">−{stop_pct:.1f}%</text>
          <line x1="{entry_x:.1f}" y1="{BAR_Y-8}" x2="{entry_x:.1f}" y2="{BAR_Y+BAR_H+8}" stroke="#FACC15" stroke-width="2.5"/>
          <text x="{entry_x:.1f}" y="{BAR_Y-16}" text-anchor="middle" fill="#FACC15" font-size="10" font-family="monospace" font-weight="700">ENTRY</text>
          <text x="{entry_x:.1f}" y="{BAR_Y+BAR_H+20}" text-anchor="middle" fill="#FACC15" font-size="12" font-family="monospace" font-weight="700">{cur}{entry_price:.2f}</text>
          <line x1="{target_x:.1f}" y1="{BAR_Y-8}" x2="{target_x:.1f}" y2="{BAR_Y+BAR_H+8}" stroke="#00FF88" stroke-width="2" stroke-dasharray="4,3"/>
          <text x="{target_x:.1f}" y="{BAR_Y-16}" text-anchor="middle" fill="#00FF88" font-size="10" font-family="monospace" font-weight="700">TARGET</text>
          <text x="{target_x:.1f}" y="{BAR_Y+BAR_H+20}" text-anchor="middle" fill="#00FF88" font-size="11" font-family="monospace">{cur}{target_price:.2f}</text>
          <text x="{target_x:.1f}" y="{BAR_Y+BAR_H+34}" text-anchor="middle" fill="#00FF88" font-size="10" font-family="monospace" opacity="0.7">+{target_pct:.1f}%</text>
          <text x="{(stop_x + entry_x)/2:.1f}" y="{BAR_Y + BAR_H/2 + 4}" text-anchor="middle" fill="#FF6B6B" font-size="11" font-family="monospace" font-weight="700">−{cur}{risk_per_share:.2f}</text>
          <text x="{(entry_x + target_x)/2:.1f}" y="{BAR_Y + BAR_H/2 + 4}" text-anchor="middle" fill="#00FF88" font-size="11" font-family="monospace" font-weight="700">+{cur}{reward_per_share:.2f}</text>
        </svg>'''
        import streamlit.components.v1 as components
        components.html(f'<div style="background:#0E1828;border-radius:10px;border:1px solid #243348;padding:4px;margin-top:8px;">{svg}</div>', height=180)
    except: pass

    st.markdown(f'''
    <div style="background:#111827;border:1px solid #243348;border-radius:8px;
                padding:8px 16px;margin-top:6px;display:flex;gap:20px;flex-wrap:wrap;
                font-size:11px;font-family:'JetBrains Mono',monospace;align-items:center;">
      <span style="color:#64748B;">Mode <span style="color:{mode_colors[selected_mode]};font-weight:700;">{selected_mode}</span></span>
      <span style="color:#64748B;">Position <span style="color:#38BDF8;">{cur}{position_size_input:,.0f}</span></span>
      <span style="color:#64748B;">Entry <span style="color:#FACC15;">{cur}{entry_price:.2f}</span></span>
      <span style="color:#64748B;">Stop <span style="color:#FF6B6B;">{cur}{stop_price:.2f} (−{stop_pct:.1f}%)</span></span>
      <span style="color:#64748B;">Target <span style="color:#00FF88;">{cur}{target_price:.2f} (+{target_pct:.1f}%)</span></span>
      <span style="color:#64748B;">Max loss <span style="color:#FF6B6B;">{cur}{actual_loss:,.0f} ({loss_pct:.1f}% of position)</span></span>
    </div>''', unsafe_allow_html=True)

    # ── EARNINGS ──────────────────────────────────────────────
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

    # ── EARNINGS HISTORY ──────────────────────────────────────
    st.markdown('<div class="section-header">Earnings History — Last 4 Quarters</div>', unsafe_allow_html=True)
    if not earnings_hist:
        st.markdown('<div class="panel-body"><div style="padding:12px 14px;font-size:12px;color:#4A6080;">No earnings history available</div></div>', unsafe_allow_html=True)
    else:
        eh_html = '<div style="background:#1A2232;border:1px solid #243348;border-radius:0 0 8px 8px;">'
        eh_html += '<div class="earn-hist-row" style="background:#131F32;font-size:11px;color:#64748B;"><span>Quarter</span><span>EPS Estimate</span><span>EPS Actual</span><span>Surprise</span></div>'
        for e in reversed(earnings_hist):
            beat_cls = "earn-beat" if e["beat"] else "earn-miss"
            icon     = "▲" if e["beat"] else "▼"
            surp_str = f'{icon} {e["surprise"]:+.1f}%'
            eh_html += f'<div class="earn-hist-row"><span style="color:#E2E8F0;">{e["quarter"]}</span><span style="color:#94A3B8;font-family:monospace;">{cur}{e["estimate"]:.2f}</span><span style="color:#E2E8F0;font-family:monospace;">{cur}{e["actual"]:.2f}</span><span class="{beat_cls};">{surp_str}</span></div>'
        st.markdown(eh_html + '</div>', unsafe_allow_html=True)

    # ── INSIDER TRADING ───────────────────────────────────────
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

    # ── NEWS SENTIMENT ────────────────────────────────────────
    news_sentiment = a.get('news_sentiment', [])
    st.markdown('<div class="section-header">News & Sentiment</div>', unsafe_allow_html=True)
    if not news_items:
        st.markdown('<div style="background:#1A2232;border:1px solid #243348;border-radius:0 0 8px 8px;padding:12px 14px;font-size:12px;color:#4A6080;">No recent news available</div>', unsafe_allow_html=True)
    else:
        news_html = '<div style="background:#1A2232;border:1px solid #243348;border-radius:0 0 8px 8px;">'
        for i, news in enumerate(news_items):
            title = news.get('title','')
            pub   = news.get('publisher','')
            link  = news.get('link','')
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

    # ── MARKET CONTEXT ────────────────────────────────────────
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

    # ── LIVE CHART ────────────────────────────────────────────
    st.markdown('<div class="section-header" style="margin-top:12px;">Live Chart · Daily Candles · 1 Year</div>', unsafe_allow_html=True)
    chart_df = df.tail(252).copy()
    st.plotly_chart(build_chart(chart_df, ticker), use_container_width=True, config={'displayModeBar': True})

    # ── CHART PATTERNS ────────────────────────────────────────
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

    st.markdown('<div class="section-header">Trend Context</div>', unsafe_allow_html=True)
    trend_items = [
        ("Short-term trend (5 days)",   a.get('trend_short','N/A'),  a.get('trend_short_desc','')),
        ("Medium-term trend (20 days)", a.get('trend_medium','N/A'), a.get('trend_medium_desc','')),
        ("Long-term trend (200 days)",  a.get('trend_long','N/A'),   a.get('trend_long_desc','')),
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

    render_disclaimer()


def render_earnings_analyzer():
    st.markdown('<div style="text-align:center;font-size:24px;font-weight:800;color:#F1F5F9;margin-bottom:6px;">Earnings Call Analyzer</div>', unsafe_allow_html=True)
    st.markdown('<div style="text-align:center;font-size:13px;color:#4A6080;margin-bottom:16px;">Paste any earnings call transcript → get an instant AI teardown</div>', unsafe_allow_html=True)
    transcript = st.text_area("Paste transcript here", height=280, placeholder="Q3 2024 Earnings Call Transcript...\n\nOperator: Good morning and welcome...", key="ea_transcript")
    col1, col2 = st.columns([2, 1])
    with col1:
        ticker_ea = st.text_input("Ticker (optional)", placeholder="NVDA", key="ea_ticker")
    with col2:
        quarter_ea = st.selectbox("Quarter", ["Q1","Q2","Q3","Q4"], key="ea_quarter")

    if st.button("🎙️ Analyze Transcript", type="primary", use_container_width=True, key="ea_analyze"):
        if not transcript or len(transcript.strip()) < 200:
            st.warning("Please paste a full transcript (at least 200 characters)")
        else:
            with st.spinner("Analyzing earnings call..."):
                try:
                    client = anthropic.Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])
                    msg = client.messages.create(
                        model="claude-sonnet-4-20250514",
                        max_tokens=1000,
                        messages=[{
                            "role": "user",
                            "content": f"Analyze this earnings call transcript for {ticker_ea or 'the company'}.\n\nReturn ONLY raw JSON:\n"
                                       f'{{\"tone\":\"Bullish|Neutral|Bearish\",\"management_confidence\":\"High|Medium|Low\",\"key_wins\":[\"w1\",\"w2\",\"w3\"],\"key_risks\":[\"r1\",\"r2\"],\"guidance\":\"\",\"analyst_reception\":\"\",\"surprise_factors\":[\"s1\"],\"verdict\":\"\",\"summary\":\"\"}}\n\nTRANSCRIPT:\n{transcript[:6000]}'
                        }]
                    )
                    raw = msg.content[0].text.strip()
                    if raw.startswith("```"):
                        raw = raw.split("```")[1]
                        if raw.startswith("json"): raw = raw[4:]
                    ea = json.loads(raw.strip())
                    tone_col = "#00FF88" if ea.get("tone")=="Bullish" else "#FF6B6B" if ea.get("tone")=="Bearish" else "#FACC15"
                    conf_col = "#00FF88" if ea.get("management_confidence")=="High" else "#FF6B6B" if ea.get("management_confidence")=="Low" else "#FACC15"
                    c1,c2,c3 = st.columns(3)
                    c1.markdown(f'<div class="earn-bar" style="border-left-color:{tone_col};"><div class="earn-label">Overall Tone</div><div class="earn-val" style="color:{tone_col};">{ea.get("tone","")}</div></div>', unsafe_allow_html=True)
                    c2.markdown(f'<div class="earn-bar" style="border-left-color:{conf_col};"><div class="earn-label">Mgmt Confidence</div><div class="earn-val" style="color:{conf_col};">{ea.get("management_confidence","")}</div></div>', unsafe_allow_html=True)
                    c3.markdown(f'<div class="earn-bar" style="border-left-color:#818CF8;"><div class="earn-label">Analyst Reception</div><div class="earn-val" style="color:#818CF8;font-size:12px;">{ea.get("analyst_reception","")[:60]}</div></div>', unsafe_allow_html=True)
                    st.markdown(f'<div style="background:#1A2232;border:1px solid #14B8A6;border-radius:8px;padding:14px 18px;margin:8px 0;"><div style="font-size:10px;color:#5EEAD4;margin-bottom:6px;">VERDICT</div><div style="font-size:14px;color:#E2E8F0;line-height:1.6;">{ea.get("verdict","")}</div></div>', unsafe_allow_html=True)
                    c1,c2 = st.columns(2)
                    with c1:
                        st.markdown('<div class="section-header">Key Wins</div>', unsafe_allow_html=True)
                        for w in ea.get("key_wins",[]):
                            st.markdown(f'<div class="reason-bull">+ {w}</div>', unsafe_allow_html=True)
                    with c2:
                        st.markdown('<div class="section-header">Key Risks</div>', unsafe_allow_html=True)
                        for r in ea.get("key_risks",[]):
                            st.markdown(f'<div class="reason-bear">- {r}</div>', unsafe_allow_html=True)
                    if ea.get("guidance"):
                        st.markdown(f'<div style="background:#251800;border-left:3px solid #FACC15;border-radius:0 6px 6px 0;padding:10px 14px;font-size:13px;color:#E2E8F0;"><b style="color:#FACC15;">Guidance:</b> {ea["guidance"]}</div>', unsafe_allow_html=True)
                    st.markdown(f'<div class="summary-box"><div style="font-size:10px;color:#5EEAD4;margin-bottom:6px;">FULL ANALYSIS</div><div class="summary-text">{ea.get("summary","")}</div></div>', unsafe_allow_html=True)
                except Exception as e:
                    st.error(f"Analysis error: {e}")


SNS_UNIVERSES = {
    "S&P 500 Core":   ["AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","BRK-B","AVGO","JPM","V","UNH","LLY","XOM","MA","JNJ","PG","HD","MRK","ABBV","CVX","PEP","KO","COST","ADBE","WMT","BAC","TMO","MCD","NFLX"],
    "High-Growth Tech":["NVDA","SMCI","META","MSFT","PLTR","CRWD","DDOG","SNOW","NET","ZS","OKTA","HUBS","MDB","TTD","BILL","SQ","SHOP","RBLX","COIN","IOT"],
    "Dividend Kings": ["KO","PG","JNJ","MMM","CLX","GPC","ABT","MCD","WMT","CINF","LOW","SWK","LANC","NWN","CBSH","SCL","MSEX","YORW","AWR","ARTNA"],
    "Biotech/Health": ["LLY","UNH","ABBV","JNJ","MRK","TMO","AMGN","GILD","BIIB","REGN","VRTX","BMY","ISRG","MDT","BSX","DXCM","MRNA","BNTX","SGEN","ILMN"],
    "Energy":         ["XOM","CVX","COP","SLB","MPC","PSX","EOG","PXD","OXY","DVN","FANG","HAL","BKR","VLO","HES","APA","LNG","AR","NOG","CIVI"],
    "TSX Canada":     ["RY.TO","TD.TO","BNS.TO","BMO.TO","CM.TO","CNR.TO","ENB.TO","TRP.TO","SU.TO","CP.TO","T.TO","BCE.TO","MFC.TO","SLF.TO","POW.TO","ATD.TO","NTR.TO","TRI.TO","WSP.TO","BAM.TO"],
    "Small Cap Momentum": ["IONQ","ARQT","ACHR","JOBY","RKLB","ASTS","LUNR","RDW","SPIR","TPVG","BWMN","IIPR","SMAR","CEVA","SONO"],
}

SNS_TEMPLATES = {
    "🔥 Breakout Hunters":    {"score_min": 7, "rsi_min": 50, "rsi_max": 70,  "vol_min": 1.2, "chg_min": 0.5,  "chg_max": 10,  "desc": "RSI 50-70 · volume surge · positive momentum"},
    "💰 Deep Value":          {"score_min": 5, "pe_max": 15,  "pb_max": 1.5,  "eps_pos": True,"chg_max": 100, "desc": "Low P/E & P/B · positive earnings · undervalued"},
    "📈 Trend Following":     {"score_min": 7, "above_200": True, "above_50": True, "vol_min": 0.8, "desc": "Above 50 & 200 MA · confirmed uptrend"},
    "⚡ Momentum Surge":      {"score_min": 8, "rsi_min": 55, "vol_min": 1.5, "chg_min": 1.0, "chg_max": 15,  "desc": "Score 8+ · RSI 55+ · volume 1.5x+"},
    "🛡️ Dividend + Growth":   {"score_min": 5, "div_min": 1.5,"eps_pos": True, "above_50": True,"desc": "Dividend yield 1.5%+ · EPS positive · uptrend"},
    "🌙 Oversold Bounces":    {"score_min": 3, "rsi_min": 20, "rsi_max": 35, "above_200": True, "desc": "RSI oversold · long-term uptrend · potential bounce"},
}

def translate_theme_to_filter(theme: str) -> dict:
    theme_lower = theme.lower().strip()
    if any(k in theme_lower for k in ("breakout","break out","momentum","surge","squeeze")):
        return SNS_TEMPLATES["⚡ Momentum Surge"]
    if any(k in theme_lower for k in ("value","cheap","undervalued","discount","low pe")):
        return SNS_TEMPLATES["💰 Deep Value"]
    if any(k in theme_lower for k in ("trend","uptrend","moving average","ma200","above 200")):
        return SNS_TEMPLATES["📈 Trend Following"]
    if any(k in theme_lower for k in ("dividend","yield","income","payout","distribution")):
        return SNS_TEMPLATES["🛡️ Dividend + Growth"]
    if any(k in theme_lower for k in ("oversold","bounce","reversal","bottom","dip")):
        return SNS_TEMPLATES["🌙 Oversold Bounces"]
    return SNS_TEMPLATES["🔥 Breakout Hunters"]

def compute_composite_score(info, df, row) -> int:
    close = float(row.get('Close', 0))
    if close == 0: return 0
    score = 0
    if close > float(row.get('MA20', close)): score += 1
    if close > float(row.get('MA50', close)): score += 2
    if close > float(row.get('MA200', close)): score += 2
    rsi = float(row.get('RSI', 50))
    if 40 < rsi < 70: score += 1
    macd = float(row.get('MACD', 0)); sig = float(row.get('MACDSig', 0))
    if macd > sig: score += 1
    if float(row.get('OBV', 0)) > 0: score += 1
    if float(row.get('VolTrend', 1)) > 0.8: score += 1
    if float(row.get('ATRPct', 0.05)) < 0.04: score += 1
    return min(score, 10)

def passes_filter(info, df, row, flt: dict) -> bool:
    close   = float(row.get('Close', 0))
    if close == 0: return False
    score   = compute_composite_score(info, df, row)
    rsi     = float(row.get('RSI', 50))
    vol_t   = float(row.get('VolTrend', 1))
    ma20    = float(row.get('MA20', close))
    ma50    = float(row.get('MA50', close))
    ma200   = float(row.get('MA200', close))
    prev_close = float(df['Close'].iloc[-2]) if len(df) > 1 else close
    chg_pct = ((close - prev_close) / prev_close * 100) if prev_close > 0 else 0
    pe      = float(info.get('trailingPE') or 0)
    pb      = float(info.get('priceToBook') or 0)
    eps_g   = float(info.get('earningsGrowth') or 0)
    div_y   = float(info.get('dividendYield') or 0) * (100 if float(info.get('dividendYield') or 0) < 1 else 1)
    if score < flt.get("score_min", 0): return False
    if rsi < flt.get("rsi_min", 0):    return False
    if rsi > flt.get("rsi_max", 100):  return False
    if vol_t < flt.get("vol_min", 0):  return False
    if chg_pct < flt.get("chg_min", -999): return False
    if chg_pct > flt.get("chg_max",  999): return False
    if flt.get("above_200") and close < ma200: return False
    if flt.get("above_50")  and close < ma50:  return False
    if flt.get("eps_pos")   and eps_g <= 0:    return False
    if pe  > 0 and pe  > flt.get("pe_max",  9999): return False
    if pb  > 0 and pb  > flt.get("pb_max",  9999): return False
    if div_y > 0 and div_y < flt.get("div_min", 0): return False
    if flt.get("div_min", 0) > 0 and div_y <= 0:    return False
    return True

def sns_one_liner(ticker, score, row, flt) -> str:
    close     = float(row.get('Close', 0))
    rsi       = float(row.get('RSI', 50))
    vol_t     = float(row.get('VolTrend', 1))
    prev_close = float(0)
    chg_pct   = 0
    lines = []
    if score >= 8: lines.append(f"Score {score}/10")
    if rsi < 35:   lines.append(f"RSI oversold {rsi:.0f}")
    elif rsi > 65: lines.append(f"RSI hot {rsi:.0f}")
    if vol_t > 1.5: lines.append(f"{vol_t:.1f}x vol surge")
    return " · ".join(lines[:3]) if lines else f"Score {score}/10 · RSI {rsi:.0f}"

def render_screener():
    fmp_key_sc = st.secrets.get("FMP_API_KEY", "")
    tab1, tab2 = st.tabs(["🔍 AI Theme Screener", "⭐ Watchlist"])

    with tab1:
        st.markdown('<div style="text-align:center;font-size:24px;font-weight:800;color:#F1F5F9;margin-bottom:6px;">Signal & Score Screener</div>', unsafe_allow_html=True)
        st.markdown('<div style="text-align:center;font-size:13px;color:#4A6080;margin-bottom:16px;">Select a universe, pick a theme or describe one — get ranked results instantly</div>', unsafe_allow_html=True)

        c1, c2 = st.columns([1, 2])
        with c1:
            selected_universe = st.selectbox("Universe", list(SNS_UNIVERSES.keys()), key="sns_universe")
        with c2:
            ai_theme = st.text_input("Theme (AI)", placeholder="e.g. oversold small caps with high volume surge", key="sns_theme")

        st.markdown('<div style="font-size:11px;color:#4A6080;margin-bottom:8px;letter-spacing:1px;text-transform:uppercase;">Quick Templates</div>', unsafe_allow_html=True)
        tpl_cols = st.columns(len(SNS_TEMPLATES))
        chosen_filter = None

        template_colors = {
            "🔥 Breakout Hunters":   ("red",   "#2D1015", "#FF6B6B"),
            "💰 Deep Value":         ("gold",  "#251800", "#FACC15"),
            "📈 Trend Following":    ("green", "#0D2818", "#00FF88"),
            "⚡ Momentum Surge":     ("green", "#0D2818", "#00FF88"),
            "🛡️ Dividend + Growth":  ("blue",  "#0A1525", "#38BDF8"),
            "🌙 Oversold Bounces":   ("blue",  "#0A1525", "#A78BFA"),
        }

        for i, (tname, tdata) in enumerate(SNS_TEMPLATES.items()):
            with tpl_cols[i]:
                color_key, bg_col, txt_col = template_colors.get(tname, ("blue", "#0A1525", "#38BDF8"))
                st.markdown(f'''
                <div class="tpl-wrap-{color_key}">
                  <div class="tpl-card" style="background:{bg_col};border:1px solid {txt_col}44;cursor:pointer;">
                    <div style="font-size:11px;font-weight:800;color:{txt_col};margin-bottom:3px;">{tname}</div>
                    <div style="font-size:10px;color:#64748B;line-height:1.5;">{tdata["desc"]}</div>
                  </div>
                </div>''', unsafe_allow_html=True)
                st.markdown(f'<div class="tpl-select tpl-select-{color_key}">', unsafe_allow_html=True)
                if st.button("Select", key=f"tpl_{i}", use_container_width=True):
                    st.session_state['sns_chosen_filter'] = tdata
                    st.session_state['sns_chosen_name']   = tname
                st.markdown('</div>', unsafe_allow_html=True)

        if 'sns_chosen_filter' in st.session_state:
            chosen_filter = st.session_state['sns_chosen_filter']

        if ai_theme:
            chosen_filter = translate_theme_to_filter(ai_theme)
            st.session_state['sns_chosen_filter'] = chosen_filter
            st.session_state['sns_chosen_name']   = f'AI: "{ai_theme}"'

        if chosen_filter:
            chosen_name = st.session_state.get('sns_chosen_name', '')
            st.markdown(f'''
            <div style="background:#0A1E2C;border:1px solid #38BDF8;border-radius:8px;
                        padding:8px 16px;margin:8px 0;display:flex;align-items:center;
                        justify-content:space-between;">
              <div style="font-size:12px;color:#38BDF8;font-weight:700;">{chosen_name}</div>
              <div style="font-size:11px;color:#64748B;">{chosen_filter.get("desc","")}</div>
              <div style="font-size:11px;color:#4A6080;">Universe: <span style="color:#5EEAD4;font-weight:600;">{selected_universe} ({len(SNS_UNIVERSES[selected_universe])} stocks)</span></div>
            </div>''', unsafe_allow_html=True)

            if st.button("▶ Run Screener", type="primary", use_container_width=True, key="sns_run"):
                tickers = SNS_UNIVERSES[selected_universe]
                results = []
                prog = st.progress(0, text="Scanning universe...")
                for i, sym in enumerate(tickers):
                    try:
                        d  = fetch_ticker_data(sym, fmp_key_sc, _v=15)
                        df = d['df']
                        if df.empty or len(df) < 50: continue
                        df = calculate_indicators(df)
                        if df.empty: continue
                        row = df.iloc[-1]
                        inf = d['info']
                        sc  = compute_composite_score(inf, df, row)
                        if passes_filter(inf, df, row, chosen_filter):
                            close     = float(row['Close'])
                            prev_c    = float(df['Close'].iloc[-2]) if len(df)>1 else close
                            chg_pct   = (close - prev_c) / prev_c * 100 if prev_c else 0
                            results.append({
                                "ticker": sym,
                                "name":   inf.get('longName', inf.get('shortName', sym))[:28],
                                "score":  sc,
                                "rsi":    float(row.get('RSI', 0)),
                                "close":  close,
                                "chg":    chg_pct,
                                "vol_t":  float(row.get('VolTrend', 1)),
                                "one_liner": sns_one_liner(sym, sc, row, chosen_filter),
                            })
                        prog.progress((i+1)/len(tickers), text=f"Scanning {sym}...")
                    except: continue
                prog.empty()
                results.sort(key=lambda x: x["score"], reverse=True)
                st.session_state['sns_results'] = results

        if 'sns_results' in st.session_state:
            results = st.session_state['sns_results']
            if not results:
                st.warning("No stocks matched the filter in this universe. Try a different template or universe.")
            else:
                st.markdown(f'<div style="font-size:12px;color:#5EEAD4;font-weight:700;margin:8px 0;">{len(results)} stocks passed the filter</div>', unsafe_allow_html=True)
                for r in results[:15]:
                    sc_col  = "#00FF88" if r["score"]>=7 else "#FACC15" if r["score"]>=4 else "#FF6B6B"
                    chg_col = "#00FF88" if r["chg"] >= 0 else "#FF6B6B"
                    sign    = "+" if r["chg"] >= 0 else ""
                    vol_col = "#FACC15" if r["vol_t"] >= 1.5 else "#94A3B8"
                    cur_sym = "CA$" if r["ticker"].endswith(".TO") else "$"
                    st.markdown(f'''
                    <div style="background:#1A2232;border:1px solid #243348;border-radius:8px;
                                padding:10px 16px;margin-bottom:6px;display:flex;
                                align-items:center;gap:16px;">
                      <div style="font-family:'JetBrains Mono',monospace;font-size:16px;
                                  font-weight:800;color:#00FF88;min-width:80px;">{r["ticker"]}</div>
                      <div style="flex:1;min-width:0;">
                        <div style="font-size:12px;font-weight:600;color:#E2E8F0;
                                    white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{r["name"]}</div>
                        <div style="font-size:11px;color:#64748B;margin-top:2px;">{r["one_liner"]}</div>
                      </div>
                      <div style="text-align:center;min-width:50px;">
                        <div style="font-size:18px;font-weight:800;color:{sc_col};
                                    font-family:'JetBrains Mono',monospace;">{r["score"]}</div>
                        <div style="font-size:9px;color:#64748B;">SCORE</div>
                      </div>
                      <div style="text-align:right;min-width:90px;">
                        <div style="font-family:'JetBrains Mono',monospace;font-size:15px;
                                    font-weight:700;color:#F1F5F9;">{cur_sym}{r["close"]:.2f}</div>
                        <div style="font-size:12px;color:{chg_col};font-weight:600;">{sign}{r["chg"]:.2f}%</div>
                      </div>
                      <div style="text-align:center;min-width:60px;">
                        <div style="font-size:13px;color:#A78BFA;font-family:monospace;
                                    font-weight:700;">{r["rsi"]:.0f}</div>
                        <div style="font-size:9px;color:#64748B;">RSI</div>
                      </div>
                      <div style="text-align:center;min-width:50px;">
                        <div style="font-size:13px;color:{vol_col};font-family:monospace;
                                    font-weight:700;">{r["vol_t"]:.1f}x</div>
                        <div style="font-size:9px;color:#64748B;">VOL</div>
                      </div>
                    </div>''', unsafe_allow_html=True)
                    if st.button(f"📊 Full Analysis → {r['ticker']}", key=f"sns_analyze_{r['ticker']}", use_container_width=False):
                        run_analysis(r['ticker'])

        with st.expander("ℹ️ How the screener works"):
            st.markdown("""
            **Signal & Score Screener** scans every stock in the selected universe using the same 8 technical signals the HUD uses:
            20MA, 50MA, 200MA, RSI, MACD, OBV, Volume, and ATR.

            Each stock gets a **score from 0 to 10** based on how many signals are bullish.
            The template filters then narrow the results to stocks matching specific criteria:
            RSI range, volume surge, moving average position, earnings growth, dividend yield, and more.

            The AI Theme input uses Claude to map your plain-English description to the closest technical filter.
            For best results, describe price behavior: "RSI oversold + high volume" or "above 200 MA + growing earnings".
            """)

    with tab2:
        st.markdown('<div style="text-align:center;font-size:24px;font-weight:800;color:#F1F5F9;margin-bottom:6px;">Watchlist</div>', unsafe_allow_html=True)
        st.markdown('<div style="text-align:center;font-size:13px;color:#4A6080;margin-bottom:16px;">Track your tickers — scores and signals refresh every 60 min</div>', unsafe_allow_html=True)

        wl_raw = st.text_input("Add tickers (comma separated)", placeholder="AAPL, MSFT, NVDA, RY.TO", key="wl_input")
        if st.button("Add to Watchlist", key="wl_add"):
            new = [t.strip().upper() for t in wl_raw.split(",") if t.strip()]
            current = st.session_state.get('watchlist', [])
            combined = list(dict.fromkeys(current + new))
            st.session_state['watchlist'] = combined
            st.rerun()

        watchlist = st.session_state.get('watchlist', [])
        if watchlist:
            if st.button("🔄 Refresh Scores", key="wl_refresh"):
                for k in list(st.session_state.keys()):
                    if k.startswith("_ticker_cache_"):
                        del st.session_state[k]
                st.rerun()

            for sym in watchlist:
                try:
                    d   = fetch_ticker_data(sym, fmp_key_sc, _v=15)
                    df  = d['df']
                    if df.empty or len(df) < 50: continue
                    df  = calculate_indicators(df)
                    row = df.iloc[-1]
                    inf = d['info']
                    sc  = compute_composite_score(inf, df, row)
                    close   = float(row['Close'])
                    prev_c  = float(df['Close'].iloc[-2]) if len(df) > 1 else close
                    chg_pct = (close - prev_c) / prev_c * 100 if prev_c else 0
                    sc_col  = "#00FF88" if sc >= 7 else "#FACC15" if sc >= 4 else "#FF6B6B"
                    chg_col = "#00FF88" if chg_pct >= 0 else "#FF6B6B"
                    sign    = "+" if chg_pct >= 0 else ""
                    cur_w   = "CA$" if sym.endswith(".TO") else "$"
                    cname   = inf.get('longName', inf.get('shortName', sym))[:30]
                    c1, c2 = st.columns([5, 1])
                    with c1:
                        st.markdown(f'''
                        <div style="background:#1A2232;border:1px solid #243348;border-radius:8px;
                                    padding:10px 16px;margin-bottom:6px;display:flex;
                                    align-items:center;gap:16px;">
                          <div style="font-family:'JetBrains Mono',monospace;font-size:15px;
                                      font-weight:800;color:#00FF88;min-width:72px;">{sym}</div>
                          <div style="flex:1;font-size:12px;color:#94A3B8;">{cname}</div>
                          <div style="font-size:19px;font-weight:800;color:{sc_col};
                                      font-family:monospace;min-width:30px;">{sc}</div>
                          <div style="font-family:monospace;font-size:15px;
                                      font-weight:700;color:#F1F5F9;">{cur_w}{close:.2f}</div>
                          <div style="font-size:13px;color:{chg_col};font-weight:700;">{sign}{chg_pct:.2f}%</div>
                          <div style="font-size:13px;color:#A78BFA;font-family:monospace;">{float(row["RSI"]):.0f} RSI</div>
                        </div>''', unsafe_allow_html=True)
                    with c2:
                        if st.button(f"Analyze", key=f"wl_an_{sym}"):
                            run_analysis(sym)
                        if st.button(f"✕", key=f"wl_rm_{sym}"):
                            st.session_state['watchlist'] = [x for x in watchlist if x != sym]
                            st.rerun()
                except: continue
        else:
            st.markdown('<div style="text-align:center;font-size:13px;color:#4A6080;padding:32px 0;">No tickers in watchlist yet. Add some above.</div>', unsafe_allow_html=True)


if __name__ == '__main__':
    main()
