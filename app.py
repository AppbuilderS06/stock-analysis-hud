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
def _fmp_get(endpoint, api_key, params=""):
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
            if isinstance(data, dict) and ("Error Message" in data or "message" in data):
                return None
            return data
        return None
    except:
        return None

def resolve_all_matches(ticker):
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
    matches = resolve_all_matches(ticker)
    if not matches:
        return None
    exact = next((m for m in matches if m["sym"].upper() == ticker.upper()), matches[0])
    return {"name": exact["name"], "exchange": exact["exchange"], "currency": exact["currency"]}


def search_ticker_fmp(query, fmp_key=""):
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

# ── PATCH: _v=17 — added _ocf_raw, _net_income_raw, _gross_margin_prev ──
@st.cache_data(ttl=3600, show_spinner=False)
def fetch_ticker_data(ticker, fmp_key="", _v=17):
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
            op_inc       = _row(inc, 'Operating Income')
            gross        = _row(inc, 'Gross Profit')
            interest_exp = _row(inc, 'Interest Expense')
            cogs_val     = _row(inc, 'Cost Of Revenue', 'Cost of Goods')
            if cogs_val == 0 and gross > 0 and rev > 0:
                cogs_val = rev - gross
            if rev > 0:      info['_rev_raw']   = rev
            if op_inc != 0:  info['_op_inc_raw'] = op_inc
            if cogs_val > 0: info['_cogs_raw']   = cogs_val
            if interest_exp != 0 and op_inc != 0:
                info['interestCoverage'] = round(op_inc / abs(interest_exp), 2)
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
            # PATCH: store net income raw for EQR
            if net != 0:
                info['_net_income_raw'] = net
            # PATCH: store prior year gross margin for direction signal
            if inc.shape[1] > 1:
                rev_p   = _row(inc.iloc[:,1:2], 'Total Revenue')
                gross_p = _row(inc.iloc[:,1:2], 'Gross Profit')
                if rev_p > 0 and gross_p > 0:
                    info['_gross_margin_prev'] = gross_p / rev_p
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
            equity       = _b('Stockholders Equity','Total Equity','Common Stock Equity')
            debt         = _b('Total Debt','Long Term Debt')
            c_assets     = _b('Current Assets')
            c_liab       = _b('Current Liabilities')
            total_assets = _b('Total Assets')
            total_liab   = _b('Total Liabilities Net Minority Interest','Total Liabilities')
            cash_bs      = _b('Cash And Cash Equivalents','Cash Cash Equivalents And Short Term Investments','Cash')
            receivables  = _b('Net Receivables','Accounts Receivable','Receivables')
            st_invest    = _b('Short Term Investments','Other Short Term Investments')
            inventory_bs = _b('Inventory','Inventories')
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
                quick_assets = cash_bs + st_invest + receivables
                if quick_assets > 0:
                    info['quickRatio'] = round(quick_assets / abs(c_liab), 2)
            if total_assets > 0 and total_liab != 0:
                info['debtToAssets'] = round(abs(total_liab) / total_assets, 2)
            rev_raw = float(info.get('_rev_raw', 0) or 0)
            if total_assets > 0 and rev_raw > 0:
                info['assetTurnover'] = round(rev_raw / total_assets, 2)
            cogs_raw = float(info.get('_cogs_raw', 0) or 0)
            if inventory_bs > 0 and cogs_raw > 0:
                info['inventoryTurnover'] = round(cogs_raw / inventory_bs, 2)
    except: pass

    # PATCH: extract Operating Cash Flow
    try:
        cf = raw.cashflow
        if cf is not None and not cf.empty:
            def _cf(*names):
                for n in names:
                    for idx in cf.index:
                        if n.lower() in str(idx).lower():
                            try:
                                v = float(cf.loc[idx].iloc[0] or 0)
                                if v != 0: return v
                            except: pass
                return 0
            ocf = _cf('Operating Cash Flow', 'Cash From Operations',
                      'Net Cash Provided By Operating Activities',
                      'Total Cash From Operating Activities')
            if ocf != 0:
                info['_ocf_raw'] = ocf
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

# ── PATCH: TECHNICAL SETUP added, SWING TRADE kept as fallback ──
VERDICT_COLORS = {
    "TECHNICAL SETUP": {"bg": "#0A1525", "border": "#38BDF8", "color": "#38BDF8"},
    "SWING TRADE":     {"bg": "#0A1525", "border": "#38BDF8", "color": "#38BDF8"},
    "INVEST":          {"bg": "#0A1E12", "border": "#00FF88", "color": "#00FF88"},
    "DAY TRADE":       {"bg": "#1A1000", "border": "#FACC15", "color": "#FACC15"},
    "AVOID":           {"bg": "#1E0A0A", "border": "#FF6B6B", "color": "#FF6B6B"},
    "WATCH":           {"bg": "#0A1E12", "border": "#00FF88", "color": "#00FF88"},
}

# ── CSS ───────────────────────────────────────────────────────
st.markdown("""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;600;700&display=swap');

  html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
  .stApp { background: #111827; }
  .block-container { padding: 3.5rem 2rem 2rem; max-width: 1200px; margin-left: auto; margin-right: auto; transition: all 0.3s ease; }

  #MainMenu { visibility: hidden; }
  footer { visibility: hidden; }
  .stDeployButton { display: none; }
  header[data-testid="stHeader"] {
    background-color: #111827 !important;
    border-bottom: none !important;
  }

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

  .analyst-panel { background: #1A2232; border-radius: 8px; overflow: hidden; border: 1px solid #243348; margin-bottom: 8px; }
  .analyst-bar-wrap { display: flex; height: 8px; border-radius: 4px; overflow: hidden; margin: 6px 0; }
  .analyst-seg-buy  { background: #00FF88; }
  .analyst-seg-hold { background: #FACC15; }
  .analyst-seg-sell { background: #FF6B6B; }

  .earn-hist-row { display: grid; grid-template-columns: 1fr 1fr 1fr 1fr; gap: 5px; padding: 8px 14px; border-bottom: 1px solid #111827; font-size: 12px; }
  .earn-hist-row:last-child { border-bottom: none; }
  .earn-beat { color: #00FF88; font-weight: 700; }
  .earn-miss { color: #FF6B6B; font-weight: 700; }

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
    df['OBV_div'] = 0
    if p_norm < -thresh and o_norm > thresh:
        df.loc[df.index[-1], 'OBV_div'] = 1
    elif p_norm > thresh and o_norm < -thresh:
        df.loc[df.index[-1], 'OBV_div'] = -1

    return df.dropna(subset=['MA20','MA50','RSI','MACD'])


def detect_weinstein_phase(df):
    import numpy as _np
    if len(df) < 160:
        return (0, 'PHASE ?', 'Unclear', '#94A3B8', 0, '', 'Insufficient data')

    close  = df['Close']
    high   = df['High']
    low    = df['Low']
    volume = df['Volume']

    ma20  = close.rolling(20).mean()
    ma50  = close.rolling(50).mean()
    ma150 = close.rolling(150).mean()

    c      = float(close.iloc[-1])
    m20    = float(ma20.iloc[-1])
    m50    = float(ma50.iloc[-1])
    m150   = float(ma150.iloc[-1])

    def _slope(series, n=10):
        cur  = float(series.iloc[-1])
        prev = float(series.iloc[-(n+1)]) if len(series) > n else cur
        return (cur - prev) / prev if prev != 0 else 0

    slope_20  = _slope(ma20,  10)
    slope_50  = _slope(ma50,  10)
    slope_150 = _slope(ma150, 10)

    recent_high = float(high.tail(20).max())
    prior_high  = float(high.iloc[-40:-20].max()) if len(high) >= 40 else recent_high
    recent_low  = float(low.tail(20).min())
    prior_low   = float(low.iloc[-40:-20].min()) if len(low) >= 40 else recent_low

    higher_highs = recent_high > prior_high * 1.01
    higher_lows  = recent_low  > prior_low  * 1.01
    lower_highs  = recent_high < prior_high * 0.99
    lower_lows   = recent_low  < prior_low  * 0.99

    mas_bullish_aligned = (c > m20 > m50 > m150) and slope_150 > 0
    mas_bearish_aligned = (c < m20 < m50 < m150) and slope_150 < 0
    above_150 = c > m150
    below_150 = c < m150

    recent_bars = df.tail(15)
    bar_range   = (recent_bars['High'] - recent_bars['Low']).replace(0, 0.001)
    close_pct   = (recent_bars['Close'] - recent_bars['Low']) / bar_range
    elasticity  = float(close_pct.mean())
    limp_bars   = elasticity < 0.45
    strong_bars = elasticity > 0.55

    obv = df.get('OBV', None)
    if obv is not None and len(obv) >= 20:
        obv_slope  = _slope(obv, 20)
        price_slope_20 = _slope(close, 20)
        obv_rising      = obv_slope > 0
        obv_falling     = obv_slope < 0
        bullish_div = price_slope_20 < -0.001 and obv_slope > 0.001
        bearish_div = price_slope_20 >  0.001 and obv_slope < -0.001
    else:
        obv_rising = obv_falling = bullish_div = bearish_div = False

    recent_20 = df.tail(20)
    up_days   = recent_20[recent_20['Close'] > recent_20['Open']]
    dn_days   = recent_20[recent_20['Close'] < recent_20['Open']]
    avg_up_vol = float(up_days['Volume'].mean()) if len(up_days) > 0 else 0
    avg_dn_vol = float(dn_days['Volume'].mean()) if len(dn_days) > 0 else 0
    vol_bullish = avg_up_vol > avg_dn_vol * 1.1
    vol_bearish = avg_dn_vol > avg_up_vol * 1.1

    peak_200 = float(close.tail(200).max())
    pct_off  = (peak_200 - c) / peak_200 if peak_200 > 0 else 0

    s1 = s2 = s3 = s4 = 0

    if below_150 and abs(slope_150) < 0.002:  s1 += 2
    if higher_lows and not higher_highs:       s1 += 2
    if bullish_div:                            s1 += 2
    if obv_rising and not higher_highs:        s1 += 1
    if pct_off > 0.30:                         s1 += 1

    if mas_bullish_aligned:                    s2 += 3
    elif above_150 and slope_150 > 0.0005:     s2 += 2
    if higher_highs and higher_lows:           s2 += 2
    elif higher_highs and not lower_lows:      s2 += 1
    if strong_bars and not limp_bars:          s2 += 1
    if vol_bullish:                            s2 += 1
    if obv_rising:                             s2 += 1

    if above_150 and slope_150 < 0.001 and lower_highs:  s3 += 3
    if limp_bars:                              s3 += 2
    if bearish_div or (obv_falling and above_150): s3 += 2
    if vol_bearish and above_150:             s3 += 1
    if lower_highs and not lower_lows:        s3 += 1
    if pct_off > 0.08 and above_150:          s3 += 1

    if mas_bearish_aligned:                    s4 += 3
    elif below_150 and slope_150 < -0.0005:    s4 += 2
    if lower_highs and lower_lows:             s4 += 2
    if vol_bearish:                            s4 += 1
    if obv_falling:                            s4 += 1
    if limp_bars:                              s4 += 1
    if pct_off > 0.20:                         s4 += 1

    scores = {1: s1, 2: s2, 3: s3, 4: s4}
    phase  = max(scores, key=scores.get)
    best   = scores[phase]

    if best < 3:
        phase = 0

    sorted_scores = sorted(scores.values(), reverse=True)
    gap   = sorted_scores[0] - sorted_scores[1] if len(sorted_scores) > 1 else 0
    conf  = 3 if gap >= 4 else 2 if gap >= 2 else 1 if gap >= 1 else 0

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


# ── PATCH: Sector ETF Map ─────────────────────────────────────
SECTOR_ETF_MAP = {
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
    "Information Technology":      "XLK",
    "Consumer Discretionary":      "XLY",
    "Consumer Staples":            "XLP",
    "Materials":                   "XLB",
    "Health Care":                 "XLV",
    "Telecommunication Services":  "XLC",
    "Electronic Components":       "XLK",
    "Semiconductors":              "XLK",
}


# ── PATCH: Fundamental Screen — sector-aware scoring engine ──
def fundamental_screen(info, verdict):
    """
    Sector-aware fundamental scoring engine.
    Returns dict: verdict_text, verdict_color, score_pct, bucket, detail
    Describes mechanical screening results only. Not financial advice.
    """
    sector   = str(info.get('sector',   '') or '').strip()
    industry = str(info.get('industry', '') or '').strip()

    TECH_SECTORS       = {'Technology','Information Technology','Communication Services',
                          'Telecommunication Services','Electronic Components','Semiconductors'}
    INDUSTRIAL_SECTORS = {'Industrials','Materials','Energy',
                          'Consumer Cyclical','Consumer Discretionary'}
    FINANCIAL_SECTORS  = {'Financials','Financial Services','Banks','Insurance'}
    DEFENSIVE_SECTORS  = {'Consumer Defensive','Consumer Staples','Utilities'}
    HEALTHCARE_SECTORS = {'Healthcare','Health Care'}
    REALESTATE_SECTORS = {'Real Estate'}
    SKIP_DE_SECTORS    = FINANCIAL_SECTORS | REALESTATE_SECTORS | {'Utilities'}

    ind_low = industry.lower()
    if sector in TECH_SECTORS:
        bucket = 'Asset-Light / Tech'
    elif sector in INDUSTRIAL_SECTORS:
        bucket = 'Capital Intensive'
    elif sector in FINANCIAL_SECTORS or 'bank' in ind_low or 'insur' in ind_low:
        bucket = 'Financial Services'
    elif sector in DEFENSIVE_SECTORS:
        bucket = 'Defensive / Stable'
    elif sector in HEALTHCARE_SECTORS:
        bucket = 'Healthcare'
    elif sector in REALESTATE_SECTORS or 'reit' in ind_low:
        bucket = 'Real Estate'
    else:
        bucket = 'General'

    def _pct(key):
        v = info.get(key)
        if v is None: return None
        v = float(v)
        return v * 100 if abs(v) <= 2 else v

    def _raw(key):
        v = info.get(key)
        if v is None: return None
        return float(v)

    # ── Hard stops ───────────────────────────────────────────
    pe  = _raw('trailingPE')
    ocf = _raw('_ocf_raw')
    rev = _raw('_rev_raw') or 0

    # Pre-revenue biotech
    if bucket == 'Healthcare' and rev < 1_000_000:
        return {'verdict_text': 'Fundamental screens not applicable',
                'verdict_color': '#FACC15', 'score_pct': None,
                'bucket': bucket,
                'detail': 'Pre-revenue stage — pipeline metrics not available'}

    # P/E hard stop (skip for REITs where depreciation distorts)
    if bucket != 'Real Estate':
        if pe is not None and pe <= 0:
            return {'verdict_text': 'Technical setup only',
                    'verdict_color': '#64748B', 'score_pct': 0,
                    'bucket': bucket, 'detail': 'Hard stop: unprofitable (P/E ≤ 0)'}

    if ocf is not None and ocf <= 0:
        return {'verdict_text': 'Technical setup only',
                'verdict_color': '#64748B', 'score_pct': 0,
                'bucket': bucket, 'detail': 'Hard stop: negative operating cash flow'}

    if verdict == 'AVOID':
        return {'verdict_text': 'Technical setup only',
                'verdict_color': '#64748B', 'score_pct': 0,
                'bucket': bucket, 'detail': 'Hard stop: AI verdict is AVOID'}

    # ── Weighted partial scoring ─────────────────────────────
    earned    = 0.0
    available = 0.0
    detail_lines = []

    def score(name, pts, value, full_t, half_t, higher=True, skip=False):
        nonlocal earned, available
        if skip or value is None:
            return
        available += pts
        if higher:
            if value >= full_t:
                earned += pts
                detail_lines.append(f"✅ {name}")
            elif value >= half_t:
                earned += pts * 0.5
                detail_lines.append(f"🟡 {name}")
            else:
                detail_lines.append(f"❌ {name}")
        else:
            if value <= full_t:
                earned += pts
                detail_lines.append(f"✅ {name}")
            elif value <= half_t:
                earned += pts * 0.5
                detail_lines.append(f"🟡 {name}")
            else:
                detail_lines.append(f"❌ {name}")

    # Tier 1 — Growth (27 pts)
    rev_growth = _pct('revenueGrowth')
    rev_full   = 3.0 if bucket == 'Defensive / Stable' else 10.0
    score('Revenue Growth', 15, rev_growth, rev_full, 0.0)

    net_inc = _raw('_net_income_raw')
    if ocf is not None and net_inc is not None and net_inc > 0:
        eqr = ocf / net_inc
        score('Earnings Quality (OCF/NI)', 12, eqr, 1.0, 0.5)

    # Tier 2 — Quality (32 pts)
    gm_now  = _pct('grossMargins')
    gm_prev = _raw('_gross_margin_prev')
    gm_prev_pct = None
    if gm_prev is not None:
        gm_prev_pct = gm_prev * 100 if abs(gm_prev) <= 2 else gm_prev

    if gm_now is not None and gm_prev_pct is not None:
        gm_delta = gm_now - gm_prev_pct
        available += 12
        if gm_delta > 1.0:
            earned += 12
            detail_lines.append(f"✅ Gross Margin expanding +{gm_delta:.1f}pp")
        elif gm_delta >= -1.0:
            earned += 6
            detail_lines.append(f"🟡 Gross Margin stable {gm_delta:+.1f}pp")
        else:
            detail_lines.append(f"❌ Gross Margin compressing {gm_delta:+.1f}pp")
    elif gm_now is not None:
        gm_floor = 40.0 if bucket == 'Asset-Light / Tech' else 20.0
        score('Gross Margin', 12, gm_now, gm_floor + 20, gm_floor)

    roe = _pct('returnOnEquity')
    score('Return on Equity', 11, roe, 15.0, 10.0)

    pm_full = 15.0 if bucket == 'Asset-Light / Tech' else 3.0 if bucket == 'Capital Intensive' else 5.0
    pm_half = 8.0  if bucket == 'Asset-Light / Tech' else 1.0 if bucket == 'Capital Intensive' else 3.0
    profit_m = _pct('profitMargins')
    score('Profit Margin', 9, profit_m, pm_full, pm_half)

    # Tier 3 — Solvency (23 pts)
    skip_de = (sector in SKIP_DE_SECTORS or
               'bank' in ind_low or 'insur' in ind_low or 'reit' in ind_low)

    de = _raw('debtToEquity')
    score('Debt / Equity', 9, de, 0.5, 2.0, higher=False, skip=skip_de)

    ic = _raw('interestCoverage')
    score('Interest Coverage', 8, ic, 5.0, 3.0, skip=skip_de)

    cr = _raw('currentRatio')
    skip_cr = False
    if bucket == 'Asset-Light / Tech' and ocf is not None and rev > 0:
        if (ocf / rev) > 0.25:
            skip_cr = True
    score('Current Ratio', 6, cr, 1.5, 1.0, skip=skip_cr)

    # Tier 4 — Valuation & Risk (18 pts)
    peg = _raw('pegRatio') or _raw('trailingPegRatio')
    skip_peg = bucket in ('Capital Intensive', 'Real Estate')
    if peg is not None and peg < 0:
        skip_peg = True
    score('PEG Ratio', 11, peg, 1.0, 3.0, higher=False, skip=skip_peg)

    short_f = _pct('shortPercentOfFloat')
    score('Short % Float', 7, short_f, 5.0, 20.0, higher=False)

    # Minimum data check
    if len(detail_lines) < 5:
        return {'verdict_text': 'Insufficient data to screen',
                'verdict_color': '#94A3B8', 'score_pct': None,
                'bucket': bucket,
                'detail': 'Fewer than 5 metrics available'}

    pct = (earned / available * 100) if available > 0 else 0.0

    if pct >= 65:
        vtext = 'Passes fundamental screens'
        vcol  = '#00FF88'
    elif pct >= 45:
        vtext = 'Mixed fundamental picture'
        vcol  = '#FACC15'
    else:
        vtext = 'Technical setup only'
        vcol  = '#64748B'

    return {'verdict_text': vtext, 'verdict_color': vcol,
            'score_pct': round(pct, 1), 'bucket': bucket,
            'detail': ' · '.join(detail_lines[:6])}


# ── PATCH: MA-alignment fallback for broad ETFs ───────────────
def _ma_phase_fallback(df):
    try:
        if len(df) < 50:
            return (0, 'PHASE ?', 'Unclear', '#94A3B8', 0, '', '')
        c    = float(df['Close'].iloc[-1])
        m20  = float(df['Close'].rolling(20).mean().iloc[-1])
        m50  = float(df['Close'].rolling(50).mean().iloc[-1])
        m200 = float(df['Close'].rolling(200).mean().iloc[-1]) if len(df) >= 200 else m50
        if c > m200 and m20 > m50:
            return (2, 'PHASE 2', 'Uptrend',   '#00FF88', 2, 'Moderate confidence',
                    'Price above 200MA · 20MA > 50MA — uptrend intact')
        elif c > m200 and m20 < m50:
            return (3, 'PHASE 3', 'Topping',   '#FACC15', 2, 'Moderate confidence',
                    'Above 200MA but 20MA crossed below 50MA — momentum fading')
        elif c < m200 and m20 < m50:
            return (4, 'PHASE 4', 'Downtrend', '#FF6B6B', 2, 'Moderate confidence',
                    'Price below 200MA · 20MA < 50MA — downtrend confirmed')
        else:
            return (1, 'PHASE 1', 'Basing',    '#38BDF8', 1, 'Low confidence',
                    'Price below 200MA but 20MA rising — possible base forming')
    except:
        return (0, 'PHASE ?', 'Unclear', '#94A3B8', 0, '', '')


@st.cache_data(ttl=900, show_spinner=False)
def get_market_phase():
    try:
        spy_df = yf.Ticker("SPY").history(period="2y")
        result = detect_weinstein_phase(spy_df)
        if result[0] == 0:
            result = _ma_phase_fallback(spy_df)
        return result
    except:
        return (0, 'PHASE ?', 'Unclear', '#94A3B8', 0, '', '')


@st.cache_data(ttl=900, show_spinner=False)
def get_sector_phase(sector_etf):
    try:
        s_df = yf.Ticker(sector_etf).history(period="2y")
        result = detect_weinstein_phase(s_df)
        if result[0] == 0:
            result = _ma_phase_fallback(s_df)
        return result
    except:
        return (0, 'PHASE ?', 'Unclear', '#94A3B8', 0, '', '')



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

    # PATCH: SWING TRADE replaced with TECHNICAL SETUP in verdict options
    # PATCH: summary now requires specific HUD data references
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
        f"LAST 60 DAILY CLOSES (oldest to newest):\n{last60}\n\n"
        f"LAST 5 SESSIONS OHLCV:\n{last5}\n\n"
        f"MARKET CONTEXT:\n"
        f"S&P500: {spy_sig} ({spy_chg:+.1f}% last month)\n"
        f"NASDAQ: {qqq_sig} ({qqq_chg:+.1f}% last month)\n"
        f"DOW:    {dia_sig} ({dia_chg:+.1f}% last month)\n\n"
        f"RECENT NEWS:\n{headlines_text}\n\n"
        f"FUNDAMENTALS:\n"
        f"Market Cap: {fmt_cap(mc)} | P/E: {pe} | Fwd P/E: {fpe}\n"
        f"EPS: {eps} | P/B: {pb}\n"
        f"Revenue Growth: {rev_g} | Earnings Growth: {eps_g}\n"
        f"Next Earnings: {earn_date} | Sector: {sector}\n\n"
        "INSTRUCTIONS:\n"
        "CHART PATTERNS — analyze the 60 closes carefully:\n"
        "- Identify: Cup&Handle, Head&Shoulders, Flags, Triangles, Wedges, Double Top/Bottom\n"
        "- description: mention actual price levels\n"
        "- confidence_reason: explain WHY that score\n"
        "- still_valid: true if price still within pattern\n"
        "- Only include patterns with confidence >40%\n\n"
        "CANDLESTICK PATTERNS — use last 5 OHLCV\n\n"
        "SUMMARY — write 3-4 sentences. Reference specific HUD values: name the actual RSI "
        "value, which MAs price is above or below, whether OBV is rising or falling, and "
        "what the entry zone or key support level to watch is. Be specific, not vague.\n\n"
        "OTHER:\n"
        "- Classify each news headline as bullish/bearish/neutral\n"
        "- ALWAYS return trend_short/medium/long — NEVER return N/A\n"
        "- Use market context for business cycle phase\n\n"
        "Return ONLY this JSON:\n"
        '{"verdict":"DAY TRADE|TECHNICAL SETUP|INVEST|AVOID|WATCH",'
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
        '"summary":"3-4 sentence specific analysis referencing actual RSI value, MA positions, OBV direction, and key levels",'
        '"day_trade_note":"one sentence",'
        '"swing_note":"one sentence",'
        '"invest_note":"one sentence",'
        '"pb_ratio":0,"peg_ratio":0,'
        '"eps_growth_yoy":0,"rev_growth_yoy":0,'
        '"earnings_date":"MMM DD YYYY","earnings_days":0,'
        '"last_earnings_beat":"Beat +X% or Missed X%",'
        '"sector":"sector name",'
        '"chart_patterns":[{"name":"pattern name","type":"bullish|bearish|neutral","confidence":70,'
        '"description":"what you see","confidence_reason":"why this score","still_valid":true,'
        '"validity_note":"is price still within pattern","target_pct":10,"target_price":0}],'
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
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True,
                        vertical_spacing=0.03, row_heights=[0.6, 0.2, 0.2])
    fig.add_trace(go.Candlestick(
        x=df.index, open=df['Open'].values, high=df['High'].values,
        low=df['Low'].values, close=df['Close'].values,
        increasing_line_color='#00FF88', decreasing_line_color='#FF6B6B',
        increasing_fillcolor='#00FF88', decreasing_fillcolor='#FF6B6B',
        name=ticker, line_width=1), row=1, col=1)
    for ma, color, width in [('MA20','#38BDF8',1.5),('MA50','#F59E0B',1.5),
                               ('MA200','#FF6B6B',1.5),('MA100','#A78BFA',1)]:
        if ma in df.columns:
            fig.add_trace(go.Scatter(x=df.index, y=df[ma].values, name=ma,
                                     line=dict(color=color, width=width), opacity=0.85), row=1, col=1)
    fig.add_trace(go.Bar(x=df.index, y=df['Volume'].values, name='Volume',
                         marker=dict(color='rgba(56,189,248,0.35)'), showlegend=False), row=2, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['MACD'].values, name='MACD',
                             line=dict(color='#38BDF8', width=1.2)), row=3, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['MACDSig'].values, name='Signal',
                             line=dict(color='#F59E0B', width=1.2)), row=3, col=1)
    hist_vals = df['MACDHist'].values
    fig.add_trace(go.Bar(x=df.index, y=hist_vals, name='Hist',
                         marker=dict(color=['rgba(0,255,136,0.5)' if v >= 0 else 'rgba(255,107,107,0.5)'
                                            for v in hist_vals]), showlegend=False), row=3, col=1)
    fig.update_layout(height=540, paper_bgcolor='#0E1828', plot_bgcolor='#0E1828',
                      font=dict(color='#94A3B8', family='JetBrains Mono', size=11),
                      xaxis_rangeslider_visible=False,
                      legend=dict(bgcolor='#0E1828', bordercolor='#243348', borderwidth=1,
                                  font=dict(size=10), orientation='h', y=1.02),
                      margin=dict(l=50, r=20, t=10, b=10), hovermode='x unified')
    for i in range(1, 4):
        fig.update_xaxes(showgrid=True, gridcolor='#1A2232', gridwidth=1, row=i, col=1)
        fig.update_yaxes(showgrid=True, gridcolor='#1A2232', gridwidth=1, row=i, col=1)
    fig.update_yaxes(title_text='Price', row=1, col=1)
    fig.update_yaxes(title_text='Vol',   row=2, col=1)
    fig.update_yaxes(title_text='MACD',  row=3, col=1)
    return fig


# ── Render helpers ────────────────────────────────────────────
MULTI_LISTED = {
    'TSM':  [{'ticker':'TSM',     'name':'Taiwan Semiconductor (US ADR)', 'exchange':'NYSE',   'currency':'USD'}],
    'TSMC': [{'ticker':'TSM',     'name':'Taiwan Semiconductor (US ADR)', 'exchange':'NYSE',   'currency':'USD'}],
    'RY':   [{'ticker':'RY',      'name':'Royal Bank of Canada (US)',      'exchange':'NYSE',   'currency':'USD'},
             {'ticker':'RY.TO',   'name':'Royal Bank of Canada (TSX)',     'exchange':'TSX',    'currency':'CAD'}],
    'TD':   [{'ticker':'TD',      'name':'TD Bank (US)',                   'exchange':'NYSE',   'currency':'USD'},
             {'ticker':'TD.TO',   'name':'TD Bank (TSX)',                  'exchange':'TSX',    'currency':'CAD'}],
    'SHOP': [{'ticker':'SHOP',    'name':'Shopify (NYSE)',                 'exchange':'NYSE',   'currency':'USD'},
             {'ticker':'SHOP.TO', 'name':'Shopify (TSX)',                  'exchange':'TSX',    'currency':'CAD'}],
    'BRK':  [{'ticker':'BRK-B',   'name':'Berkshire Hathaway Class B',    'exchange':'NYSE',   'currency':'USD'},
             {'ticker':'BRK-A',   'name':'Berkshire Hathaway Class A',    'exchange':'NYSE',   'currency':'USD'}],
    'BABA': [{'ticker':'BABA',    'name':'Alibaba Group (US ADR)',         'exchange':'NYSE',   'currency':'USD'}],
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
    "Quick Ratio":      "https://www.investopedia.com/terms/q/quickratio.asp",
    "Debt-to-Assets":   "https://www.investopedia.com/terms/d/debt-to-total-assets-ratio.asp",
    "Interest Coverage":"https://www.investopedia.com/terms/i/interestcoverageratio.asp",
    "Asset Turnover":   "https://www.investopedia.com/terms/a/assetturnover.asp",
    "Inventory Turnover":"https://www.investopedia.com/terms/i/inventoryturnover.asp",
    "Dividend Yield":   "https://www.investopedia.com/terms/d/dividendyield.asp",
    "Short % Float":    "https://www.investopedia.com/terms/s/shortinterest.asp",
    "Float Shares":     "https://www.investopedia.com/terms/f/floating-stock.asp",
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



# ── Main App ──────────────────────────────────────────────────
def main():
    with st.sidebar:
        st.markdown("""
        <div style="padding:4px 0 16px;border-bottom:1px solid #1E2D42;margin-bottom:12px;">
          <div style="font-size:9px;color:#5EEAD4;letter-spacing:3px;text-transform:uppercase;
                      margin-bottom:5px;font-family:'JetBrains Mono',monospace;">Stock Analysis HUD</div>
          <div style="font-size:16px;font-weight:800;color:#F1F5F9;margin-bottom:2px;">Trading Reference</div>
          <div style="font-size:11px;color:#64748B;">Signal guide · Glossary · Controls</div>
        </div>""", unsafe_allow_html=True)

        with st.expander("📡  Signal Legend", expanded=False):
            st.markdown("""<div style="font-size:10px;color:#5EEAD4;letter-spacing:2px;text-transform:uppercase;
                        font-weight:700;margin-bottom:10px;font-family:'JetBrains Mono',monospace;">8 SIGNALS EXPLAINED</div>""",
                        unsafe_allow_html=True)
            signals_data = [
                ("20 MA","#38BDF8","20-Day Moving Average",
                 "Short-term trend. Price above = buyers in control this month. Below = recent weakness."),
                ("50 MA","#F59E0B","50-Day Moving Average",
                 "Mid-term trend. Institutional desks watch this line closely. A hold here = strong stock."),
                ("200 MA","#FF6B6B","200-Day Moving Average",
                 "Long-term trend. Above = bull market for this stock. Below = be cautious or avoid."),
                ("RSI","#A78BFA","Relative Strength Index (0–100)",
                 "Momentum meter. Under 30 = oversold (possible bounce). Over 70 = overbought (possible pullback). 40–60 = healthy trend."),
                ("MACD","#5EEAD4","Moving Average Convergence Divergence",
                 "Trend + momentum crossover. MACD line above signal line = bullish. Histogram growing = momentum building."),
                ("OBV","#00FF88","On-Balance Volume",
                 "Tracks whether smart money is buying or selling. OBV rising while price is flat = accumulation."),
                ("Volume","#94A3B8","Volume vs 20-Day Average",
                 "Confirms the move. Breakout on 1.5× average volume = institutional participation."),
                ("ATR","#FACC15","Average True Range",
                 "Daily expected price swing. High ATR = volatile, wide stops needed. Use for position sizing."),
            ]
            for abbr, color, full_name, explanation in signals_data:
                st.markdown(f"""
                <div style="background:#111827;border-left:3px solid {color};border-radius:0 6px 6px 0;
                            padding:9px 12px;margin-bottom:7px;">
                  <div style="font-family:'JetBrains Mono',monospace;font-size:13px;font-weight:700;
                              color:{color};margin-bottom:4px;">{abbr}</div>
                  <div style="font-size:11px;font-weight:600;color:#CBD5E1;margin-bottom:3px;">{full_name}</div>
                  <div style="font-size:11px;color:#64748B;line-height:1.5;">{explanation}</div>
                </div>""", unsafe_allow_html=True)

        with st.expander("📊  Score Guide", expanded=False):
            st.markdown("""<div style="font-size:10px;color:#5EEAD4;letter-spacing:2px;text-transform:uppercase;
                        font-weight:700;margin-bottom:10px;font-family:'JetBrains Mono',monospace;">WHAT EACH SCORE MEANS</div>""",
                        unsafe_allow_html=True)
            score_bands = [
                (9,10,"#00FF88","0D2818","Strong Bullish",
                 "Almost all signals aligned. High-conviction setup. Momentum + trend + volume all confirming."),
                (7,8,"#00E87A","0A2215","Moderately Bullish",
                 "Most signals green. Solid setup with a few cautions. Look for clean entry near support."),
                (5,6,"#FACC15","251800","Mixed — Neutral",
                 "Half and half. No clear edge. Wait for a signal to break one way."),
                (3,4,"#F97316","2A1200","Moderately Bearish",
                 "More signals red than green. Consider waiting or sizing down."),
                (0,2,"#FF6B6B","2D1015","Strong Bearish",
                 "Most signals negative. Not the time to buy. Review your stop loss."),
            ]
            for lo, hi, color, bg, label, desc in score_bands:
                bar_pct = int((hi / 10) * 100)
                lo_str = f"{lo}" if lo == hi else f"{lo}–{hi}"
                st.markdown(f"""
                <div style="background:#{bg};border:1px solid {color}33;border-radius:6px;
                            padding:10px 12px;margin-bottom:7px;">
                  <div style="display:flex;align-items:center;gap:10px;margin-bottom:5px;">
                    <span style="font-family:'JetBrains Mono',monospace;font-size:18px;font-weight:800;
                                 color:{color};min-width:34px;">{lo_str}</span>
                    <div style="flex:1;">
                      <div style="height:5px;background:#243348;border-radius:3px;overflow:hidden;">
                        <div style="width:{bar_pct}%;height:5px;background:{color};border-radius:3px;"></div>
                      </div>
                    </div>
                    <span style="font-size:11px;font-weight:700;color:{color};white-space:nowrap;">{label}</span>
                  </div>
                  <div style="font-size:11px;color:#94A3B8;line-height:1.5;">{desc}</div>
                </div>""", unsafe_allow_html=True)

        with st.expander("📖  Glossary", expanded=False):
            st.markdown("""<div style="font-size:10px;color:#5EEAD4;letter-spacing:2px;text-transform:uppercase;
                        font-weight:700;margin-bottom:10px;font-family:'JetBrains Mono',monospace;">KEY TERMS</div>""",
                        unsafe_allow_html=True)
            glossary = [
                ("ATR","#FACC15","Average True Range. The average daily price swing over 14 days. Used to set stop losses and gauge volatility."),
                ("EPS","#38BDF8","Earnings Per Share. Net profit divided by shares outstanding. Beat the estimate = stock usually gaps up."),
                ("Fibonacci","#00FF88","Retracement levels (38.2%, 50%, 61.8%). Traders watch these as potential support/resistance in pullbacks."),
                ("Float","#94A3B8","The number of shares available to trade publicly. Low float stocks are more volatile."),
                ("MACD","#5EEAD4","Moving Average Convergence Divergence. Tracks trend momentum by comparing two exponential moving averages."),
                ("OBV","#00FF88","On-Balance Volume. Rising OBV with flat price = accumulation = bullish divergence."),
                ("P/E Ratio","#38BDF8","Price-to-Earnings. Stock price divided by annual EPS. High P/E = expensive or high growth expected."),
                ("PEG Ratio","#A78BFA","P/E divided by earnings growth rate. Under 1 = potentially undervalued relative to growth."),
                ("Phase","#F97316","Weinstein Phase. Stocks cycle: 1=Base, 2=Uptrend (buy zone), 3=Top, 4=Downtrend (avoid)."),
                ("R:R Ratio","#FACC15","Risk-to-Reward. How much you can gain vs how much you risk. Never take a trade below 1:1."),
                ("RSI","#A78BFA","Relative Strength Index (0–100). Above 70 = overbought. Below 30 = oversold."),
                ("Support","#00FF88","A price level where buying tends to outweigh selling — the stock has bounced from here before."),
                ("Resistance","#FF6B6B","A price level where selling tends to outweigh buying — the stock has struggled to break through here."),
                ("VWAP","#5EEAD4","Volume-Weighted Average Price. Day traders use it as a key intraday reference line."),
            ]
            for term, color, definition in glossary:
                st.markdown(f"""
                <div style="padding:8px 0;border-bottom:1px solid #1E2D42;">
                  <div style="font-family:'JetBrains Mono',monospace;font-size:12px;font-weight:700;
                               color:{color};margin-bottom:3px;">{term}</div>
                  <div style="font-size:11px;color:#64748B;line-height:1.55;">{definition}</div>
                </div>""", unsafe_allow_html=True)

        st.markdown("""<div style="font-size:10px;color:#374151;letter-spacing:2px;text-transform:uppercase;
                    margin:16px 0 8px;font-family:'JetBrains Mono',monospace;">SYSTEM</div>""",
                    unsafe_allow_html=True)
        fmp_active = bool(st.secrets.get("FMP_API_KEY", ""))
        st.markdown(f"""
        <div style="background:#111827;border:1px solid #1E2D42;border-radius:6px;padding:8px 10px;margin-top:8px;">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
            <span style="font-size:10px;color:#64748B;">Data source</span>
            <span style="font-size:11px;font-weight:700;color:{'#00FF88' if fmp_active else '#FACC15'};">
              {'🟢 FMP + yfinance' if fmp_active else '🟡 yfinance only'}
            </span>
          </div>
          <div style="display:flex;justify-content:space-between;align-items:center;">
            <span style="font-size:10px;color:#64748B;">Cache TTL</span>
            <span style="font-size:11px;color:#94A3B8;">60 min</span>
          </div>
        </div>""", unsafe_allow_html=True)
        st.markdown("""<div style="padding:16px 0 4px;font-size:10px;color:#243348;
                    line-height:1.6;text-align:center;">Educational only · Not financial advice</div>""",
                    unsafe_allow_html=True)

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
                ticker_in = st.text_input("", placeholder="NVDA", key="ticker_input", label_visibility="collapsed")
                ticker_upper = ticker_in.strip().upper() if ticker_in else ""
                prev_val = st.session_state.get('_prev_ticker_val', '')
                st.session_state['_prev_ticker_val'] = ticker_upper
                selected_ticker = None

                def render_identity_card(sym, name, exch, curr, name_found=True):
                    border = "#14B8A6" if name_found else "#FACC15"
                    glow   = "0 0 20px rgba(20,184,166,0.15)" if name_found else "0 0 20px rgba(250,204,21,0.15)"
                    badge_bg  = "#0A1E1C" if name_found else "#1A1000"
                    badge_col = "#14B8A6" if name_found else "#FACC15"
                    badge_text = "✓ Confirmed" if name_found else "⚠ Verify"
                    name_col  = "#F1F5F9" if name_found else "#FACC15"
                    name_text = name if name_found else "Name not found — verify this symbol"
                    exch_text = f"{exch} &nbsp;·&nbsp; {curr}" if exch else "Exchange unknown"
                    st.markdown(f"""
                    <div style="background:linear-gradient(135deg,#0A1E2C 0%,#0D1525 100%);
                                border:1px solid {border};border-radius:10px;padding:14px 18px;
                                margin:8px 0 6px;box-shadow:{glow};
                                display:flex;align-items:center;gap:16px;">
                      <div style="font-family:'JetBrains Mono',monospace;font-size:24px;font-weight:800;
                                  color:#00FF88;letter-spacing:3px;min-width:70px;">{sym}</div>
                      <div style="flex:1;min-width:0;">
                        <div style="font-size:15px;font-weight:700;color:{name_col};margin-bottom:3px;
                                    white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{name_text}</div>
                        <div style="font-size:11px;color:#5EEAD4;">{exch_text}</div>
                      </div>
                      <div style="background:{badge_bg};border:1px solid {border};border-radius:20px;
                                  padding:3px 10px;font-size:10px;font-weight:700;color:{badge_col};">{badge_text}</div>
                    </div>""", unsafe_allow_html=True)

                def render_dropdown(rows):
                    unique_names = list({r["name"] for r in rows if r["name"]})
                    if len(unique_names) > 1:
                        st.markdown("""
                        <div style="background:linear-gradient(135deg,#1A1000 0%,#2D1500 100%);
                                    border:1px solid #FACC15;border-radius:8px;padding:10px 16px;margin:8px 0 4px;">
                          <div style="font-size:12px;font-weight:700;color:#FACC15;margin-bottom:2px;">⚠️ Same symbol — different companies</div>
                          <div style="font-size:11px;color:#CBD5E1;">Read the full name carefully before selecting.</div>
                        </div>""", unsafe_allow_html=True)
                    st.markdown("""<div style="background:#071420;border:1px solid #14B8A6;border-radius:10px;overflow:hidden;margin-top:4px;">
                      <div style="padding:6px 16px;font-size:10px;color:#5EEAD4;letter-spacing:2px;text-transform:uppercase;font-weight:700;border-bottom:1px solid #0D2030;">
                        Select exchange or share class</div>""", unsafe_allow_html=True)
                    for i, row in enumerate(rows):
                        border_b = "" if i == len(rows)-1 else "border-bottom:1px solid #0D2030;"
                        st.markdown(f"""<div style="padding:10px 16px;{border_b}display:flex;align-items:center;gap:12px;">
                          <div style="font-family:'JetBrains Mono',monospace;font-weight:800;color:#00FF88;font-size:15px;min-width:80px;">{row['sym']}</div>
                          <div style="flex:1;">
                            <div style="font-size:13px;font-weight:600;color:#E2E8F0;margin-bottom:2px;">{row['name']}</div>
                            <div style="font-size:11px;color:#5EEAD4;">{row['exch']} · {row['curr']}</div>
                          </div></div>""", unsafe_allow_html=True)
                        if st.button(f"▶ Analyze {row['sym']}", key=row["key"], use_container_width=True):
                            st.session_state["_resolved_name"] = row["name"]
                            st.session_state["_resolved_exch"] = row["exch"]
                            st.session_state["_resolved_curr"] = row["curr"]
                            return row["sym"]
                    st.markdown("</div>", unsafe_allow_html=True)
                    return None

                if ticker_upper:
                    if ticker_upper in MULTI_LISTED:
                        rows = [{"sym": o["ticker"], "name": o["name"], "exch": o["exchange"],
                                 "curr": o["currency"], "key": f'ml_{o["ticker"]}'}
                                for o in MULTI_LISTED[ticker_upper]]
                        result = render_dropdown(rows)
                        if result:
                            selected_ticker = result
                    elif fmp_key_lp:
                        results = search_ticker_fmp(ticker_upper, fmp_key_lp)
                        if results:
                            exact = next((r for r in results if r.get("symbol","").upper() == ticker_upper), None)
                            if exact and len({r.get("symbol","").upper() for r in results
                                              if r.get("symbol","").upper() == ticker_upper}) == 1:
                                name = exact.get("name","")[:52]
                                exch = exact.get("exchangeShortName","")
                                curr = exact.get("currency","USD")
                                render_identity_card(ticker_upper, name, exch, curr)
                                st.session_state["_resolved_name"] = name
                                st.session_state["_resolved_exch"] = exch
                                st.session_state["_resolved_curr"] = curr
                                btn_lbl = f"Analyze {name} →" if name else "Analyze →"
                                if st.button(btn_lbl, type="primary", use_container_width=True, key="analyze_exact"):
                                    selected_ticker = ticker_upper
                            else:
                                rows = [{"sym": r.get("symbol",""), "name": r.get("name","")[:45],
                                         "exch": r.get("exchangeShortName",""), "curr": r.get("currency","USD"),
                                         "key": f'fmp_{r.get("symbol","")}_{r.get("exchangeShortName","")}'}
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
                                rows = [{"sym": m["sym"], "name": m["name"], "exch": m["exchange"],
                                         "curr": m["currency"], "key": f'yf_{m["sym"]}_{m["exchange"]}'}
                                        for m in matches]
                                result = render_dropdown(rows)
                                if result:
                                    selected_ticker = result
                            elif len(matches) == 1:
                                m = matches[0]
                                render_identity_card(m["sym"], m["name"], m["exchange"], m["currency"])
                                st.session_state["_resolved_name"] = m["name"]
                                st.session_state["_resolved_exch"] = m["exchange"]
                                st.session_state["_resolved_curr"] = m["currency"]
                                c1, c2 = st.columns([3, 1])
                                with c1:
                                    if st.button(f"Analyze {m['name']} →", type="primary",
                                                 use_container_width=True, key="analyze_yf"):
                                        selected_ticker = m["sym"]
                                with c2:
                                    if st.button("↩ Reset", use_container_width=True, key="analyze_reset"):
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
                                    if st.button("↩ Reset", use_container_width=True, key="analyze_reset_unknown"):
                                        st.session_state.pop(cache_key, None)
                                        st.rerun()
                    else:
                        if st.button("Analyze →", type="primary", use_container_width=True, key="analyze_direct"):
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
        <span style="font-size:12px;color:#FACC15;font-weight:700;">Educational tool only - not financial advice</span>
      </div>
      <div style="font-size:11px;color:#CBD5E1;line-height:1.7;">
        AI-generated analysis does not guarantee any outcome. Always conduct your own research before
        making any investment decision. Never risk more than you can afford to lose. This tool is not
        registered with the AMF or any other securities regulator.
      </div>
    </div>""", unsafe_allow_html=True)
    col1, col2 = st.columns(2)
    with col1:
        with st.expander("Terms of Use"):
            st.markdown(
                "**Last updated: March 2026**\n\n"
                "**1. Educational Purpose Only** - Nothing constitutes financial, investment, or trading advice.\n\n"
                "**2. No Warranty** - Provided as-is. AI analysis may be inaccurate or out of date.\n\n"
                "**3. Limitation of Liability** - The operator shall not be liable for any financial losses.\n\n"
                "**4. No Fiduciary Relationship** - Use does not create an advisory relationship.\n\n"
                "**5. Regulatory Notice (Canada)** - Not registered with the AMF or any securities regulator.\n\n"
                "**6. Third-Party Services** - Uses Anthropic Claude API, yfinance, and FMP.\n\n"
                "**7. Changes** - Terms may be updated at any time."
            )
    with col2:
        with st.expander("Privacy Policy"):
            st.markdown(
                "**Last updated: March 2026**\n\n"
                "**What we collect** - We do not collect, store, or sell any personal information.\n\n"
                "**Third-party logging:** Streamlit Cloud, Anthropic API, and FMP may log usage.\n\n"
                "**Cookies** - We do not set cookies. Streamlit may use session cookies.\n\n"
                "**Contact** - Since we store no personal data, there is nothing to access or delete."
            )
    st.markdown('<div style="text-align:center;font-size:10px;color:#374151;padding:8px 0;letter-spacing:1px;">AI-GENERATED - NOT FINANCIAL ADVICE - EDUCATIONAL PURPOSES ONLY</div>',
                unsafe_allow_html=True)


def run_analysis(ticker):
    prog = st.empty()
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
        # PATCH: _v=17
        data  = fetch_ticker_data(ticker, fmp_key, _v=17)
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
                    pub   = (item.get('publisher') or '')
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
                buy_cnt  = int((r.get('strongBuy',0) or 0) + (r.get('buy',0) or 0))
                hold_cnt = int(r.get('hold', 0) or 0)
                sell_cnt = int((r.get('strongSell',0) or 0) + (r.get('sell',0) or 0))
                num_ana  = buy_cnt + hold_cnt + sell_cnt
        except: pass

        if target_mean == 0:
            target_mean = float(info.get('targetMeanPrice') or 0)
            target_low  = float(info.get('targetLowPrice')  or 0)
            target_high = float(info.get('targetHighPrice') or 0)
        if num_ana == 0:
            num_ana = int(info.get('numberOfAnalystOpinions') or 0)

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
                    buy_cnt  = int((r.get('strongBuy', r.get('strong_buy', 0)) or 0) + (r.get('buy', 0) or 0))
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
                    est = float(er.get('epsEstimate', er.get('EPS Estimate', er.get('estimate', 0))) or 0)
                    act = float(er.get('epsActual',   er.get('Reported EPS', er.get('actual',   0))) or 0)
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
            if ins is not None and not ins.empty:
                for _, ri in ins.head(5).iterrows():
                    shares = int(ri.get('Shares', ri.get('shares', 0)) or 0)
                    val    = float(ri.get('Value', ri.get('value', 0)) or 0)
                    text   = str(ri.get('Text',   ri.get('text', '')) or '')
                    trans  = str(ri.get('Transaction', ri.get('transaction', '')) or '')
                    name   = str(ri.get('Insider', ri.get('filerName', ri.get('insider', ''))) or '')
                    role   = str(ri.get('Position', ri.get('filerRelation', '')) or '')
                    date_i = str(ri.get('Date', ri.get('startDate', '')) or '')
                    combined = (text + trans).lower()
                    is_sell = any(w in combined for w in ('sale','sell','dispose','disposed'))
                    is_buy  = (not is_sell and
                               any(w in combined for w in ('purchase','buy','acquisition','grant','award','exercise')))
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
