import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import anthropic
import json
import re
import html as _html
from datetime import datetime, timedelta

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
    # FMP news first — ticker-tagged, structured, more reliable than yfinance
    if use_fmp:
        try:
            articles = _fmp_get(f"v3/stock_news?tickers={ticker}&limit=20", fmp_key)
            if articles and isinstance(articles, list):
                # Build relevance keywords from ticker + company name
                company_name = str(info.get('longName') or info.get('shortName') or '').lower()
                sector       = str(info.get('sector') or '').lower()
                industry     = str(info.get('industry') or '').lower()
                tick_lower   = ticker.lower().replace('-','').replace('.to','').replace('.l','')

                # Sector-specific keywords that indicate market-relevant news
                sector_kw = {
                    'technology': ['ai','chip','semiconductor','nvidia','amd','intel','tsmc','cloud','data center','gpu','cpu'],
                    'information technology': ['ai','chip','semiconductor','cloud','data center','software','saas'],
                    'communication services': ['ad','advertising','streaming','social','meta','google','alphabet','content'],
                    'financials': ['rate','fed','interest','bank','lending','credit','loan','yield'],
                    'health care': ['fda','drug','trial','biotech','pharma','approval','clinical'],
                    'healthcare': ['fda','drug','trial','biotech','pharma','approval','clinical'],
                    'energy': ['oil','gas','opec','crude','refinery','energy','pipeline'],
                    'consumer discretionary': ['retail','consumer','spending','amazon','tesla','ev'],
                    'industrials': ['aerospace','defense','manufacturing','supply chain'],
                    'materials': ['commodities','metal','mining','lithium','copper'],
                }
                extra_kw = sector_kw.get(sector, []) + sector_kw.get(industry[:20], [])

                def relevance_score(title):
                    t = title.lower()
                    score = 0
                    # Direct company/ticker mention = highest weight
                    if tick_lower in t: score += 10
                    if company_name and any(w in t for w in company_name.split() if len(w) > 3):
                        score += 8
                    # Sector/industry keywords
                    for kw in extra_kw:
                        if kw in t: score += 2
                    # Generic filler penalty
                    filler = ['$1,000','passive income','buy this etf','10 years ago',
                              'chipotle','coca-cola','dividend','here\'s how much',
                              'warren buffett','best stocks to buy','should you buy']
                    for f in filler:
                        if f in t: score -= 5
                    return score

                scored = []
                for a in articles[:20]:
                    t = str(a.get("title","")).strip()
                    if t:
                        scored.append((relevance_score(t), {
                            "title":     t,
                            "publisher": str(a.get("site","")),
                            "link":      str(a.get("url","")),
                            "published": str(a.get("publishedDate",""))
                        }))

                # Sort by relevance, keep top 8 with score > 0
                scored.sort(key=lambda x: x[0], reverse=True)
                news = [item for score, item in scored if score > 0][:8]
                # If filtering was too aggressive, fall back to top 5 unfiltered
                if not news:
                    news = [item for _, item in scored[:5]]
        except: pass

    # yfinance news as fallback when FMP returns nothing
    if not news:
        try:
            for item in (raw.news or [])[:5]:
                try:
                    t = str(item.get('title','') or item.get('content',{}).get('title',''))
                    p = str(item.get('publisher','') or '')
                    l = str(item.get('link','') or item.get('content',{}).get('canonicalUrl',{}).get('url',''))
                    if t: news.append({'title':t,'publisher':p,'link':l})
                except: pass
        except: pass

    earn_hist       = None
    insider         = None
    rec_summary     = None
    analyst_targets = None
    calendar        = None
    earn_dates      = None

    if use_fmp:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _fetch_profile():
            try:
                profile = _fmp_get(f"v3/profile/{ticker}", fmp_key)
                if not profile or not isinstance(profile, list) or not profile:
                    profile = _fmp_get(f"v3/profile/{ticker.replace('-','.')}", fmp_key)
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
                        # Fill if key missing OR if yfinance left it empty/None
                        if val and (key not in info or not info.get(key)):
                            info[key] = val
            except: pass

        def _fetch_earnings():
            try:
                surp = _fmp_get(f"v3/earnings-surprises/{ticker}", fmp_key)
                if surp and isinstance(surp, list):
                    rows = []
                    for e in surp[:4]:
                        act_val  = float(e.get("actualEarningResult", e.get("actualEps",   e.get("actual",   0))) or 0)
                        est_val  = float(e.get("estimatedEarning",    e.get("estimatedEps",e.get("estimate", 0))) or 0)
                        surp_pct = ((act_val - est_val) / abs(est_val)) if est_val != 0 else 0
                        rows.append({"period": e.get("date",""), "epsEstimate": est_val,
                                     "epsActual": act_val, "surprisePercent": surp_pct})
                    return pd.DataFrame(rows) if rows else None
            except: pass
            return None

        def _fetch_targets():
            try:
                tp = _fmp_get(f"v3/price-target-consensus/{ticker}", fmp_key)
                if tp and isinstance(tp, list) and tp:
                    t = tp[0]
                    targets = {"mean": t.get("targetConsensus",0),
                               "high": t.get("targetHigh",0),
                               "low":  t.get("targetLow",0)}
                    info.update({"targetMeanPrice": targets["mean"],
                                 "targetHighPrice": targets["high"],
                                 "targetLowPrice":  targets["low"]})
                    return targets
            except: pass
            return None

        def _fetch_recs():
            try:
                est = _fmp_get(f"v3/analyst-stock-recommendations/{ticker}", fmp_key)
                if est and isinstance(est, list) and est:
                    e = est[0]
                    sb = int(e.get("analystRatingsStrongBuy",  e.get("strongBuy",  0)) or 0)
                    b  = int(e.get("analystRatingsBuy",         e.get("buy",       0)) or 0)
                    h  = int(e.get("analystRatingsHold",        e.get("hold",      0)) or 0)
                    s  = int(e.get("analystRatingsSell",        e.get("sell",      0)) or 0)
                    ss = int(e.get("analystRatingsStrongSell",  e.get("strongSell",0)) or 0)
                    total = sb + b + h + s + ss
                    if total > 0:
                        info["numberOfAnalystOpinions"] = total
                    return pd.DataFrame([{"strongBuy":sb,"buy":b,"hold":h,"sell":s,"strongSell":ss}])
            except: pass
            return None

        def _fetch_calendar():
            try:
                today = datetime.now().strftime("%Y-%m-%d")
                fut   = (datetime.now() + timedelta(days=180)).strftime("%Y-%m-%d")
                cal = _fmp_get(f"v3/earning_calendar?from={today}&to={fut}", fmp_key)
                if cal and isinstance(cal, list):
                    matches = [e for e in cal if str(e.get("symbol","")).upper() == ticker.upper()]
                    if matches:
                        ned_str = str(matches[0].get("date",""))
                        if ned_str:
                            info["earningsDate"] = ned_str
                            return {"Earnings Date": ned_str}
            except: pass
            return None

        # Run all FMP calls in parallel — cuts wait time from ~5s sequential to ~1.5s
        with ThreadPoolExecutor(max_workers=5) as pool:
            fut_profile  = pool.submit(_fetch_profile)
            fut_earnings = pool.submit(_fetch_earnings)
            fut_targets  = pool.submit(_fetch_targets)
            fut_recs     = pool.submit(_fetch_recs)
            fut_calendar = pool.submit(_fetch_calendar)

            fut_profile.result()   # profile mutates info in-place
            earn_hist       = fut_earnings.result()
            analyst_targets = fut_targets.result()
            rec_summary     = fut_recs.result()
            calendar        = fut_calendar.result()

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
    border: 1px solid #5EEAD4;
    border-radius: 10px;
    padding: 16px 22px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 6px;
  }
  .ticker-name { font-family: 'JetBrains Mono', monospace; font-size: 42px; font-weight: 800; color: #00FF88; letter-spacing: 4px; text-shadow: 0 0 20px #00FF8840; }
  .company-name { font-size: 16px; font-weight: 600; color: #E2E8F0; }
  .exchange-pill { font-size:13px; color: #99F6E4; background: #0F3030; border: 1px solid #5EEAD4; padding: 3px 10px; border-radius: 4px; letter-spacing: 1px; }
  .price-display { font-family: 'JetBrains Mono', monospace; font-size: 36px; font-weight: 800; color: #FACC15; text-align: right; }
  .price-change-up { display: inline-block; background: #052A14; border: 1px solid #00FF8844; border-radius: 6px; padding: 4px 12px; font-size: 14px; font-weight: 700; color: #00FF88; font-family: 'JetBrains Mono', monospace; }
  .price-change-dn { display: inline-block; background: #2D0A0A; border: 1px solid #FF6B6B44; border-radius: 6px; padding: 4px 12px; font-size: 14px; font-weight: 700; color: #FF6B6B; font-family: 'JetBrains Mono', monospace; }

  .status-bar {
    background: linear-gradient(90deg, #0E2218 0%, #0E1C30 100%);
    border-radius: 6px; padding: 7px 16px;
    font-family: 'JetBrains Mono', monospace;
    font-size:13px; color: #3D6050;
    margin-bottom: 10px;
  }
  .status-bar span { color: #99F6E4; font-weight: 600; }

  .section-header {
    background: #0F3030;
    padding: 7px 14px;
    font-size:13px; color: #5EEAD4;
    letter-spacing: 2px; text-transform: uppercase;
    border-radius: 8px 8px 0 0;
    border-bottom: 1px solid #5EEAD433;
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
  .verdict-label { font-size:13px; letter-spacing: 2px; text-transform: uppercase; opacity: 0.8; margin-bottom: 5px; }
  .verdict-value { font-size: 28px; font-weight: 800; letter-spacing: 1px; }
  .verdict-meta { font-size:13px; color: #94A3B8; margin-top: 4px; }
  .verdict-note { font-size:13px; margin-top: 6px; line-height: 1.5; opacity: 0.9; }

  .score-card { background: #0A1525; border: 1px solid #1E2D42; border-left: 3px solid #38BDF8; border-radius: 8px; padding: 16px 18px; }
  .score-label { font-size:13px; color: #38BDF8; letter-spacing: 2px; text-transform: uppercase; margin-bottom: 6px; }
  .score-num { font-family: 'JetBrains Mono', monospace; font-size: 52px; font-weight: 800; line-height: 1; }
  .score-denom { font-size: 20px; color: #4A6080; }

  .reason-bull { background: #0D2818; border-left: 2px solid #00FF88; padding: 8px 12px; font-size:13px; color: #86EFAC; border-radius: 0 6px 6px 0; line-height: 1.5; margin-bottom: 4px; }
  .reason-bear { background: #2D1015; border-left: 2px solid #FF6B6B; padding: 8px 12px; font-size:13px; color: #FCA5A5; border-radius: 0 6px 6px 0; line-height: 1.5; margin-bottom: 4px; }

  .tf-day   { background: #111827; border: 1px solid #FACC1533; border-radius: 10px; overflow: hidden; margin-bottom: 6px; }
  .tf-swing { background: #111827; border: 1px solid #38BDF833; border-radius: 10px; overflow: hidden; margin-bottom: 6px; }
  .tf-inv   { background: #111827; border: 1px solid #00FF8833; border-radius: 10px; overflow: hidden; margin-bottom: 6px; }
  .tf-label { font-size:13px; font-weight: 800; letter-spacing: 1.5px; text-transform: uppercase; }
  .tf-note  { font-size: 13px; color: #E2E8F0; line-height: 1.7; padding: 12px 16px; }
  .tf-header-day   { background: linear-gradient(135deg, #251800 0%, #141525 100%); padding: 9px 16px; border-bottom: 1px solid #FACC1533; display: flex; align-items: center; gap: 8px; }
  .tf-header-swing { background: linear-gradient(135deg, #0A1E2C 0%, #0A1525 100%); padding: 9px 16px; border-bottom: 1px solid #38BDF833; display: flex; align-items: center; gap: 8px; }
  .tf-header-inv   { background: linear-gradient(135deg, #052A14 0%, #0A1525 100%); padding: 9px 16px; border-bottom: 1px solid #00FF8833; display: flex; align-items: center; gap: 8px; }

  .earn-bar { background: #1A2232; border: 1px solid #243348; border-left: 3px solid #38BDF8; border-radius: 8px; padding: 10px 16px; }
  .earn-label { font-size: 9px; color: #4A6080; letter-spacing: 1px; text-transform: uppercase; margin-bottom: 3px; }
  .earn-val { font-size: 13px; font-weight: 700; font-family: 'JetBrains Mono', monospace; }

  .summary-box { background: #1A2232; border-top: 2px solid #5EEAD4; border-radius: 0 0 8px 8px; padding: 14px 16px; }
  .summary-text { font-size: 13px; color: #E2E8F0; line-height: 1.8; }

  .pat-bull { background: #0D2818; border: 1px solid #00FF8830; border-radius: 8px; padding: 12px 14px; }
  .pat-bear { background: #2D0A0A; border: 1px solid #FF6B6B30; border-radius: 8px; padding: 12px 14px; }
  .pat-neut { background: #251800; border: 1px solid #FACC1530; border-radius: 8px; padding: 12px 14px; }
  .pat-name { font-size: 14px; font-weight: 700; margin-bottom: 4px; }
  .pat-desc { font-size:13px; color: #CBD5E1; line-height: 1.5; margin-top: 5px; }
  .pat-target { font-size:13px; font-weight: 600; margin-top: 6px; }

  .candle-card { background: #1A2232; border: 1px solid #2A3F5A; border-radius: 8px; padding: 12px 14px; text-align: center; }
  .candle-card-bull { background: #0D2818; border: 1px solid #00FF8830; border-radius: 8px; padding: 12px 14px; text-align: center; }
  .candle-card-bear { background: #2D1015; border: 1px solid #FF6B6B30; border-radius: 8px; padding: 12px 14px; text-align: center; }
  .candle-card-neut { background: #251800; border: 1px solid #FACC1530; border-radius: 8px; padding: 12px 14px; text-align: center; }

  .trend-tile { background: #1A2232; border: 1px solid #2A3F5A; border-radius: 8px; padding: 12px 14px; }
  .trend-tile-label { font-size: 10px; color: #94A3B8; letter-spacing: 1px; text-transform: uppercase; margin-bottom: 5px; }
  .trend-tile-val { font-size: 15px; font-weight: 800; margin-bottom: 4px; }
  .trend-tile-desc { font-size:13px; color: #CBD5E1; line-height: 1.5; }

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
  /* Arrow nav buttons for timeframe switcher */
  .tf-arrow .stButton button {
    background: #0A1525 !important;
    color: #38BDF8 !important;
    border: 1px solid #1E2D42 !important;
    font-size: 16px !important;
    font-weight: 900 !important;
    padding: 6px !important;
    border-radius: 6px !important;
    width: 100% !important;
  }
  .tf-arrow .stButton button:hover {
    background: #0A1525 !important;
    border-color: #38BDF8 !important;
    opacity: 1 !important;
  }
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

  .earn-hist-row { display: grid; grid-template-columns: 1fr 1fr 1fr 1fr; gap: 5px; padding: 8px 14px; border-bottom: 1px solid #111827; font-size:13px; }
  .earn-hist-row:last-child { border-bottom: none; }
  .earn-beat { color: #00FF88; font-weight: 700; }
  .earn-miss { color: #FF6B6B; font-weight: 700; }

  .insider-row { display: grid; grid-template-columns: 2fr 1.5fr 80px 1.2fr 1.2fr; align-items: center; padding: 11px 16px; border-bottom: 1px solid #111827; gap: 8px; }
  .insider-row:last-child { border-bottom: none; }
  .insider-header { background: #131F32; }
  .insider-name { color: #E2E8F0; font-size: 13px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .insider-role { color: #94A3B8; font-size: 13px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .insider-badge-buy  { display: inline-block; background: #052A14; border: 1px solid #00FF88; color: #00FF88; font-weight: 800; font-size:13px; padding: 3px 10px; border-radius: 4px; letter-spacing: 1px; text-align: center; }
  .insider-badge-sell { display: inline-block; background: #2D0A0A; border: 1px solid #FF6B6B; color: #FF6B6B; font-weight: 800; font-size:13px; padding: 3px 10px; border-radius: 4px; letter-spacing: 1px; text-align: center; }
  .insider-shares { color: #E2E8F0; font-size: 13px; font-family: 'JetBrains Mono', monospace; text-align: right; }
  .insider-value  { color: #FACC15; font-size: 13px; font-weight: 700; font-family: 'JetBrains Mono', monospace; text-align: right; }

  .news-row { padding: 8px 14px; border-bottom: 1px solid #111827; }
  .news-row:last-child { border-bottom: none; }
  .news-headline { font-size:13px; color: #E2E8F0; line-height: 1.4; margin-bottom: 3px; }
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
    font-size:13px;
    font-weight: 600;
    letter-spacing: 0.05em;
    padding: 6px 12px;
    transition: border-color 150ms, color 150ms, background 150ms;
  }
  div[data-testid="stButton"] button:hover {
    border-color: #5EEAD4;
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
    font-size:13px;
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
    background: linear-gradient(90deg, #5EEAD4, #00FF88) !important;
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


def calc_signals(row, prev=None):
    close = row['Close']
    obv_rising = (float(row['OBV']) > float(prev['OBV'])) if prev is not None else (row['OBV'] > 0)
    sigs = {
        'MA20':  {'bull': close > row['MA20'],          'label': '20 MA',  'subtitle': 'short-term trend',  'val': 'Above' if close > row['MA20']  else 'Below'},
        'MA50':  {'bull': close > row['MA50'],          'label': '50 MA',  'subtitle': 'mid-term trend',     'val': 'Above' if close > row['MA50']  else 'Below'},
        'MA200': {'bull': close > row['MA200'],         'label': '200 MA', 'subtitle': 'long-term trend',    'val': 'Above' if close > row['MA200'] else 'Below'},
        'RSI':   {'bull': 40 < row['RSI'] < 70,        'label': 'RSI',    'subtitle': 'momentum',           'val': f"{row['RSI']:.1f}", 'neut': True},
        'MACD':  {'bull': row['MACD'] > row['MACDSig'], 'label': 'MACD',  'subtitle': 'trend crossover',    'val': 'Bullish' if row['MACD'] > row['MACDSig'] else 'Bearish'},
        'OBV':   {'bull': obv_rising,                   'label': 'OBV',    'subtitle': 'volume flow',        'val': 'Rising' if obv_rising else 'Falling'},
        'Vol':   {'bull': row['VolTrend'] > 0.8,       'label': 'Volume', 'subtitle': 'vs average',         'val': f"{row['VolTrend']:.2f}x"},
        'ATR':   {'bull': row['ATRPct'] < 0.04,        'label': 'ATR',    'subtitle': 'volatility',         'val': f"${row['ATR']:.2f} ({row['ATRPct']*100:.1f}%)"},
    }
    bull_count = sum(1 for k, v in sigs.items() if v['bull'])
    return sigs, round((bull_count / 8) * 10)


def calc_timeframe_scores(row, prev, df, info, signals, phase_result,
                          market_ctx, analysis, fs):
    """
    Compute Day Trade, Swing Trade, and Position scores.
    Returns dict: {'Day': (score, meaning), 'Swing': (score, meaning), 'Position': (score, meaning)}
    """
    close      = float(row['Close'])
    obv_rising = float(row['OBV']) > float(prev['OBV'])
    vol_ratio  = float(row['VolTrend'])
    rsi        = float(row['RSI'])
    atr_pct    = float(row['ATRPct'])
    macd_bull  = float(row['MACD']) > float(row['MACDSig'])
    macd_hist  = float(row['MACDHist'])
    above_20   = close > float(row['MA20'])
    above_50   = close > float(row['MA50'])
    above_200  = close > float(row['MA200'])

    ph_num     = phase_result[0] if phase_result else 0
    tw_score   = int(market_ctx.get('tw_score', 0)) if market_ctx else 0

    # Analyst data
    analyst    = st.session_state.get('analyst_data', {})
    target     = float(analyst.get('target', 0) or 0)
    rec_key    = str(analyst.get('rec_key', '') or '').lower()
    price_vs_target = ((target - close) / close) if target > 0 else 0

    # Net news score
    net_news   = 0
    try:
        net_news = int(analysis.get('net_news_score', 0) or 0)
    except: pass

    # Fundamental screen score pct
    fs_pct = float(fs.get('score_pct') or 0)

    # ── THREE TAILWINDS — compute from market_ctx ──────────
    mkt_ph  = market_ctx.get('mkt_phase', 0)
    sec_ph  = market_ctx.get('sec_phase', 0)
    tw      = sum([1 if mkt_ph == 2 else 0,
                   1 if sec_ph == 2 else 0,
                   1 if ph_num == 2 else 0])

    # ── Regime multiplier (shared) ──────────────────────────
    regime_mult = {0: 0.60, 1: 0.80, 2: 1.00, 3: 1.20}.get(tw, 1.0)

    # ────────────────────────────────────────────────────────
    # DAY TRADE SCORE (max 10 pts before multiplier)
    # ────────────────────────────────────────────────────────
    dt = 0.0

    # ATR viability gate — prerequisite
    if atr_pct >= 0.015:
        dt += 1.5
    elif atr_pct >= 0.01:
        dt += 0.75

    # Volume — highest weight for day trading
    if vol_ratio >= 1.5:
        dt += 2.0
    elif vol_ratio >= 1.0:
        dt += 1.0
    elif vol_ratio >= 0.8:
        dt += 0.5

    # MACD histogram direction
    if macd_bull and macd_hist > 0:
        dt += 1.0
    elif macd_bull:
        dt += 0.5

    # RSI zone (room to run, not overbought/oversold)
    if 40 <= rsi <= 65:
        dt += 1.0
    elif 30 <= rsi < 40 or 65 < rsi <= 70:
        dt += 0.5

    # Price vs 20MA
    if above_20:
        dt += 1.5
    elif abs(close - float(row['MA20'])) / close < 0.005:
        dt += 0.75  # at the line

    # Wide-ranging bar bonus
    candle_range = float(row['High']) - float(row['Low'])
    if candle_range > 2.5 * float(row['ATR']):
        up_candle = close >= float(row['Open'])
        if (up_candle and above_20) or (not up_candle and not above_20):
            dt += 1.0  # WRB in trend direction

    # Candle pattern (from Claude analysis)
    candles = analysis.get('candle_patterns', [])
    if candles:
        top = candles[0]
        if top.get('session','') in ('Today','Yesterday'):
            if top.get('type') == 'bullish' and above_20:
                dt += 0.5
            elif top.get('type') == 'bearish' and not above_20:
                dt += 0.5

    # SPY Phase 4 penalty for long setups
    if mkt_ph == 4 and above_20:
        dt -= 1.5

    dt_base = min(dt, 10.0)
    # Day trade uses reduced regime multiplier (less relevant than swing)
    dt_mult = 1.0 + (regime_mult - 1.0) * 0.5
    dt_final = max(1.0, min(10.0, dt_base * dt_mult))

    # ────────────────────────────────────────────────────────
    # SWING TRADE SCORE (max 10 pts before multiplier)
    # ────────────────────────────────────────────────────────
    sw = 0.0

    # Weinstein Phase — dominant signal
    if ph_num == 2:
        sw += 2.5
    elif ph_num == 1:
        sw += 1.25
    elif ph_num == 3:
        sw += 0.5
    # Phase 4 = 0

    # MA stack alignment
    ma_pts = 0.0
    if above_200: ma_pts += 0.5
    if above_50:  ma_pts += 0.75
    if above_20:  ma_pts += 0.75
    sw += min(ma_pts, 2.0)

    # OBV — institutions leading price
    if obv_rising:
        sw += 1.5

    # Three Tailwinds
    sw += [0, 0.5, 1.0, 1.5][tw]

    # Volume confirmation
    if vol_ratio >= 1.5:
        sw += 1.0
    elif vol_ratio >= 1.0:
        sw += 0.5

    # News catalyst quality
    if net_news >= 3:
        sw += 0.75
    elif net_news >= 1:
        sw += 0.4
    elif net_news <= -3:
        sw += 0.0
    else:
        sw += 0.1

    # RSI room to run
    if 40 <= rsi <= 65:
        sw += 0.5
    elif 65 < rsi <= 75:
        sw += 0.25

    # MACD confirmation
    if macd_bull and macd_hist > 0:
        sw += 0.25

    sw_base = min(sw, 10.0)

    # Fundamental multiplier on swing
    if fs_pct >= 65:
        fs_mult = 1.0
    elif fs_pct >= 45:
        fs_mult = 0.95
    elif fs_pct > 0:
        fs_mult = 0.85
    else:
        fs_mult = 0.90  # no data — neutral

    sw_final = max(1.0, min(10.0, sw_base * regime_mult * fs_mult))

    # ────────────────────────────────────────────────────────
    # POSITION SCORE (max 10 pts before multiplier)
    # ────────────────────────────────────────────────────────
    pos = 0.0

    # Fundamental quality — dominant signal
    if fs_pct >= 65:
        pos += 3.0
    elif fs_pct >= 45:
        pos += 1.75
    elif fs_pct > 0:
        pos += 0.5
    # Failing hard stops = 0

    # Weinstein Phase — entry timing
    if ph_num == 2:
        pos += 1.5
    elif ph_num == 1:
        pos += 1.0
    elif ph_num == 3:
        pos += 0.25

    # Three Tailwinds — macro regime
    pos += [0, 0.5, 1.0, 1.5][tw]

    # Analyst consensus vs price
    if price_vs_target >= 0.15 and rec_key in ('buy', 'strong_buy', 'strongbuy'):
        pos += 1.5
    elif price_vs_target >= 0.05 and rec_key in ('buy', 'strong_buy', 'strongbuy', 'hold'):
        pos += 0.75
    elif price_vs_target >= 0 and rec_key in ('buy', 'strong_buy'):
        pos += 0.25
    elif rec_key in ('sell', 'strong_sell', 'strongsell', 'underperform'):
        pos += 0.0

    # 50MA and 200MA (only long-term MAs matter for position)
    if above_200 and above_50:
        pos += 1.0
    elif above_200:
        pos += 0.5

    # OBV long-term trend
    if obv_rising:
        pos += 0.75

    # Business cycle alignment (from Claude)
    cycle = analysis.get('cycle_phase', '')
    sector = str(info.get('sector', '') or '').lower()
    growth_sectors = {'technology', 'information technology', 'communication services',
                      'consumer discretionary', 'healthcare', 'health care'}
    defensive_sectors = {'consumer staples', 'consumer defensive', 'utilities',
                         'real estate', 'financials'}
    if cycle in ('Early', 'Mid') and sector in growth_sectors:
        pos += 0.5
    elif cycle == 'Late' and sector in defensive_sectors:
        pos += 0.5
    elif cycle == 'Recession' and sector in defensive_sectors:
        pos += 0.5

    # Structural news (earnings beat/guidance raise = PEAD effect)
    if net_news >= 3:
        pos += 0.25

    pos_base = min(pos, 10.0)

    # Position score regime multiplier — quality companies get floor protection
    pos_mult = regime_mult
    if fs_pct >= 65 and tw == 0:
        pos_mult = max(pos_mult, 0.75)  # quality floor in bad macro

    pos_final = max(1.0, min(10.0, pos_base * pos_mult))

    # ── Score labels ────────────────────────────────────────
    def _label(s, tf):
        s = round(s)
        if tf == 'Day':
            if s >= 8: return "Strong day setup"
            if s >= 6: return "Moderate day setup"
            if s >= 4: return "Marginal — wait for entry"
            return "Avoid — poor conditions"
        elif tf == 'Swing':
            if s >= 8: return "Strong swing setup"
            if s >= 6: return "Moderate swing setup"
            if s >= 4: return "Mixed — patience required"
            return "Avoid — trend unfavorable"
        else:
            if s >= 8: return "Strong position case"
            if s >= 6: return "Moderate position case"
            if s >= 4: return "Mixed fundamentals"
            return "Avoid — quality concerns"

    return {
        'Day':      (round(dt_final),  _label(dt_final,  'Day')),
        'Swing':    (round(sw_final),  _label(sw_final,  'Swing')),
        'Position': (round(pos_final), _label(pos_final, 'Position')),
    }


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
    # Standard yfinance sector names
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
    # FMP sector names (often different from yfinance)
    "Semiconductor":               "XLK",
    "Memory Chips":                "XLK",
    "Semiconductor Memory":        "XLK",
    "Semiconductors & Equipment":  "XLK",
    "Software":                    "XLK",
    "Technology Services":         "XLK",
    "Hardware":                    "XLK",
    "Electronic Technology":       "XLK",
    "Producer Manufacturing":      "XLI",
    "Commercial Services":         "XLI",
    "Transportation":              "XLI",
    "Retail Trade":                "XLY",
    "Consumer Services":           "XLY",
    "Distribution Services":       "XLY",
    "Health Services":             "XLV",
    "Health Technology":           "XLV",
    "Pharmaceutical":              "XLV",
    "Biotechnology":               "XLV",
    "Biotech":                     "XLV",
    "Finance":                     "XLF",
    "Banking":                     "XLF",
    "Insurance":                   "XLF",
    "Investment":                  "XLF",
    "Oil & Gas":                   "XLE",
    "Oil Gas":                     "XLE",
    "Mining":                      "XLB",
    "Chemicals":                   "XLB",
    "Metals":                      "XLB",
    "Utilities - Regulated":       "XLU",
    "Telecom":                     "XLC",
    "Media":                       "XLC",
    "Entertainment":               "XLC",
    "Internet":                    "XLC",
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

    if str(verdict).strip().upper() == 'AVOID':
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
        spy_df = yf.Ticker("SPY").history(period="1y")  # 1y sufficient for phase detection
        result = detect_weinstein_phase(spy_df)
        if result[0] == 0:
            result = _ma_phase_fallback(spy_df)
        return result
    except:
        return (0, 'PHASE ?', 'Unclear', '#94A3B8', 0, '', '')


@st.cache_data(ttl=900, show_spinner=False)
def get_sector_phase(sector_etf):
    try:
        s_df = yf.Ticker(sector_etf).history(period="1y")  # 1y sufficient for phase detection
        result = detect_weinstein_phase(s_df)
        if result[0] == 0:
            result = _ma_phase_fallback(s_df)
        return result
    except:
        return (0, 'PHASE ?', 'Unclear', '#94A3B8', 0, '', '')


def _perf_ret(series, tf):
    """Return % change for a price series over timeframe key. Module-level — never redefined."""
    try:
        s = series.dropna()
        if len(s) < 2: return 0.0
        now = datetime.now()
        if tf == '1W':
            past = s.iloc[-6]  if len(s) >= 6   else s.iloc[0]
        elif tf == '1M':
            past = s.iloc[-22] if len(s) >= 22  else s.iloc[0]
        elif tf == 'QTD':
            q_start = datetime(now.year, ((now.month - 1) // 3) * 3 + 1, 1)
            try: idx = s.index.tz_localize(None) if s.index.tzinfo else s.index
            except: idx = s.index
            mask = idx >= q_start
            past = s[mask].iloc[0] if mask.any() else s.iloc[-66]
        elif tf == 'YTD':
            ytd_start = datetime(now.year, 1, 1)
            try: idx = s.index.tz_localize(None) if s.index.tzinfo else s.index
            except: idx = s.index
            mask = idx >= ytd_start
            past = s[mask].iloc[0] if mask.any() else s.iloc[0]
        elif tf == '1Y':
            past = s.iloc[-252] if len(s) >= 252 else s.iloc[0]
        else:
            past = s.iloc[-22]
        return float((s.iloc[-1] / float(past) - 1) * 100)
    except: return 0.0


@st.cache_data(ttl=900, show_spinner=False)
def fetch_comparison_data(sector_etf):
    """
    Download SPY + sector ETF, precompute all 5 timeframe returns.
    Cached 15 min. If sector_etf == 'SPY' or download fails, SPY-only is returned.
    """
    TFS   = ['1W', '1M', 'QTD', 'YTD', '1Y']
    empty = {tf: 0.0 for tf in TFS}
    spy_data = empty.copy()
    sec_data = empty.copy()
    try:
        tickers = ['SPY'] if sector_etf in ('', 'SPY') else [sector_etf, 'SPY']
        comp_df = yf.download(tickers, period='2y', auto_adjust=True, progress=False, threads=True)
        # Handle both single and multi-ticker download shapes
        if 'Close' in comp_df.columns and not isinstance(comp_df['Close'], pd.DataFrame):
            # Single ticker — only SPY
            spy_s    = comp_df['Close'].dropna()
            spy_data = {tf: _perf_ret(spy_s, tf) for tf in TFS}
        else:
            close_df = comp_df['Close']
            if 'SPY' in close_df.columns:
                spy_data = {tf: _perf_ret(close_df['SPY'].dropna(), tf) for tf in TFS}
            if sector_etf and sector_etf != 'SPY' and sector_etf in close_df.columns:
                sec_data = {tf: _perf_ret(close_df[sector_etf].dropna(), tf) for tf in TFS}
    except:
        pass
    return {'spy': spy_data, 'sec': sec_data}



# ── Claude Analysis ───────────────────────────────────────────
def get_claude_analysis(ticker, info, df, signals, score, fibs, news_items, market_ctx, mode="Quick"):
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
        headlines_text = "\n".join(
            f"- {n.get('title','')} [{n.get('publisher','')}]{' (' + n.get('published','')[:10] + ')' if n.get('published') else ''}"
            for n in news_items[:10]
        )
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

    is_deep = (mode == "Deep Research")

    # ── Deep Research: multi-step reasoning preamble ──────────
    if is_deep:
        role_line = (
            f"You are a senior institutional stock analyst with 20 years of experience. "
            f"You are conducting a DEEP RESEARCH analysis of {ticker}.\n"
            "Before producing your JSON output, work through the following four steps in your reasoning. "
            "Be thorough — this analysis will be used to make a real trading decision.\n\n"
            "STEP 1 — TECHNICAL PICTURE: What do the moving averages, RSI, MACD, OBV, and volume "
            "tell you? Is the trend confirmed across timeframes or are signals diverging? "
            "Where are the critical support and resistance levels? Is momentum building or fading?\n\n"
            "STEP 2 — FUNDAMENTAL QUALITY: What does the revenue growth, margins, cash flow, "
            "and balance sheet tell you about this business? Is growth accelerating or slowing? "
            "Are margins expanding or compressing? Does the valuation make sense given the growth rate?\n\n"
            "STEP 3 — MACRO & SECTOR CONTEXT: How does the current market phase, sector trend, "
            "and broader macro environment affect the probability of this trade working? "
            "Is the market a tailwind or headwind right now?\n\n"
            "STEP 4 — SYNTHESIS: Where do the technicals, fundamentals, and macro agree? "
            "Where do they conflict? What is the highest-probability scenario and what would "
            "invalidate it? What is the single most important thing to watch?\n\n"
            "Now produce your complete analysis in the required JSON format. "
            "Return ONLY raw JSON — no markdown, no backticks, no explanation.\n\n"
        )
        summary_instruction = (
            "SUMMARY — return FOUR separate JSON fields:\n"
            "summary_levels: Key levels first — the most actionable section. Name the 2-3 most critical support prices and exactly what a decisive break below each means for the trade. Name the 1-2 most critical resistance prices and what a confirmed breakout means. State the entry zone and the single price that invalidates the bullish case.\n"
            "summary_levels_sentiment: 'bullish', 'bearish', or 'mixed' — based on where price sits relative to these levels\n"
            "summary_technical: Trend and momentum. Exact MA positions with prices, RSI value, OBV direction, MACD histogram, volume character. Is the trend confirmed or diverging across timeframes?\n"
            "summary_technical_sentiment: 'bullish', 'bearish', or 'mixed'\n"
            "summary_fundamental: Business quality. Revenue growth %, earnings growth, margins, cash flow quality, valuation vs growth rate. Is the business accelerating or decelerating?\n"
            "summary_fundamental_sentiment: 'bullish', 'bearish', or 'mixed'\n"
            "summary_macro: Write 3 flowing, well-constructed sentences — no lists, no numbering, no inline enumerations. "
            "Sentence 1: State the macro environment using the actual SPY/QQQ/DIA numbers provided — is it a tailwind or headwind and how strong? "
            "Sentence 2: Synthesize the overall news picture for THIS stock in one sentence — what is the dominant theme across the headlines (e.g. product momentum, regulatory pressure, analyst optimism) and is it net bullish or bearish? Do NOT list each headline. Distill them into a single coherent narrative. "
            "Sentence 3: State the decision trigger clearly — the exact price level or specific event that would change the current view from bearish to bullish or vice versa.\n"
            "summary_macro_sentiment: 'bullish', 'bearish', or 'mixed'\n"
            "news_scores: For each headline provided, apply two-tier scoring:\n"
            "  TIER 1 — Keyword trigger (find the highest-impact keyword in the headline):\n"
            "    earnings_beat → base Bullish Medium | earnings_miss → base Bearish Medium\n"
            "    guidance_raise → base Bullish High | guidance_cut → base Bearish High\n"
            "    analyst_upgrade → base Bullish Low | analyst_downgrade → base Bearish Low\n"
            "    insider_buy → base Bullish Low | insider_sell → base Bearish Low\n"
            "    regulation → base Bearish Medium | competition → base Bearish Medium\n"
            "    product_launch → base Bullish Medium | macro_catalyst → context-dependent\n"
            "    generic (no clear trigger) → Neutral, Low\n"
            "  TIER 2 — Context modifiers (adjust base up or down):\n"
            "    Magnitude words: 'record', 'historic', 'massive' → bump magnitude up\n"
            "    Qualifier words: 'slight', 'minor', 'modest' → bump magnitude down\n"
            "    Reversal words: 'cleared', 'dismissed', 'settled' flip Bearish → Bullish\n"
            "    Sector relevance: if headline is in same sector as {ticker}, magnitude stays; if unrelated sector, reduce magnitude by one level\n"
            "    Expectation context: 'beat expectations' or 'topped estimates' → bump up; 'in-line' → keep base\n"
            "  For each headline return: trigger, impact (Bullish/Bearish/Neutral), magnitude (High/Medium/Low), context (one sentence why this magnitude for THIS stock)\n"
            "net_news_score: integer -5 to +5. Sum: High=2pts, Medium=1pt, Low=0.5pt. Bullish=positive, Bearish=negative. Round to nearest integer.\n"
            "Be specific with numbers throughout. No vague language. No generic statements."
        )
        model_name = "claude-opus-4-6"
        max_tok    = 4000
    else:
        role_line = (
            f"You are an expert stock market analyst. Analyze {ticker}.\n"
            "Return ONLY raw JSON — no markdown, no backticks, no explanation.\n\n"
        )
        summary_instruction = (
            "SUMMARY — return FOUR separate JSON fields:\n"
            "summary_levels: Key levels first. Critical support prices + what a break means. Critical resistance + what a breakout means. Entry zone and invalidation level.\n"
            "summary_levels_sentiment: 'bullish', 'bearish', or 'mixed'\n"
            "summary_technical: 2 sentences. Trend and momentum. Exact RSI, MA positions with prices, OBV direction, MACD.\n"
            "summary_technical_sentiment: 'bullish', 'bearish', or 'mixed'\n"
            "summary_fundamental: 1-2 sentences. Business quality — revenue growth %, margins, valuation.\n"
            "summary_fundamental_sentiment: 'bullish', 'bearish', or 'mixed'\n"
            "summary_macro: Write 2 flowing sentences — no lists, no numbering. "
            "Sentence 1: Macro environment with actual SPY/QQQ/DIA numbers — tailwind or headwind? "
            "Sentence 2: Synthesize the dominant news theme for THIS stock (distill all headlines into one narrative, not a list) + the decision trigger.\n"
            "summary_macro_sentiment: 'bullish', 'bearish', or 'mixed'\n"
            "news_scores: For each headline, score using two-tier system:\n"
            "  Tier 1 keyword triggers: earnings_beat=Bullish Med, earnings_miss=Bearish Med, guidance_raise=Bullish High, guidance_cut=Bearish High, analyst_upgrade=Bullish Low, analyst_downgrade=Bearish Low, regulation=Bearish Med, competition=Bearish Med, product_launch=Bullish Med, insider_buy=Bullish Low, insider_sell=Bearish Low, generic=Neutral Low\n"
            "  Tier 2 modifiers: magnitude words bump up; qualifier words bump down; reversal words flip direction; unrelated sector reduces magnitude one level\n"
            "  Return per headline: trigger, impact, magnitude, context (one sentence why for THIS stock)\n"
            "net_news_score: integer -5 to +5. High=2pts, Medium=1pt, Low=0.5pt. Bullish positive, Bearish negative.\n"
            "Be specific with numbers. No vague language."
        )
        model_name = "claude-sonnet-4-20250514"
        max_tok    = 2500

    prompt = (
        role_line +
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
        + summary_instruction + "\n\n"
        "OTHER:\n"
        "- Classify each news headline as bullish/bearish/neutral\n"
        "- ALWAYS return trend_short/medium/long — NEVER return N/A\n"
        "- Use market context for business cycle phase\n"
        "- IMPORTANT: For Technology, Communication Services, Semiconductors, and high-growth sectors "
        "do NOT use P/E ratio as a risk reason. High P/E is structurally normal for growth companies. "
        "Use revenue growth, margins, cash flow, and debt instead to assess risk.\n\n"
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
        '"summary_technical":"2-3 sentences on trend and momentum",'
        '"summary_technical_sentiment":"bullish|bearish|mixed",'
        '"summary_levels":"2-3 sentences on critical support and resistance levels with exact prices",'
        '"summary_levels_sentiment":"bullish|bearish|mixed",'
        '"summary_fundamental":"1-2 sentences on business quality",'
        '"summary_fundamental_sentiment":"bullish|bearish|mixed",'
        '"summary_macro":"2-3 sentences: macro context with SPY/QQQ/DIA numbers + news synthesis + decision trigger",'
        '"summary_macro_sentiment":"bullish|bearish|mixed",'
        '"news_scores":[{"headline":"exact headline","trigger":"earnings_beat|earnings_miss|guidance_raise|guidance_cut|regulation|competition|insider_buy|insider_sell|analyst_upgrade|analyst_downgrade|product_launch|macro_catalyst|generic","impact":"Bullish|Bearish|Neutral","magnitude":"High|Medium|Low","context":"one sentence why this magnitude for THIS stock"}],'
        '"net_news_score":0,'
        '"summary":"fallback single paragraph",'
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
        _api_key = st.secrets.get("ANTHROPIC_API_KEY", "")
        if not _api_key:
            return {"error": "Anthropic API key not configured. Add ANTHROPIC_API_KEY to Streamlit secrets."}
        client = anthropic.Anthropic(api_key=_api_key)
        # ── FIX-024: retry up to 2 times on 529 overload ─────
        import time as _time
        _last_err = None
        _msg = None
        for _attempt in range(2):
            try:
                _msg = client.messages.create(
                    model=model_name,
                    max_tokens=max_tok,
                    messages=[{"role": "user", "content": prompt}]
                )
                break  # success
            except Exception as _e:
                _last_err = _e
                err_str = str(_e)
                if '529' in err_str or 'overloaded' in err_str.lower():
                    if _attempt == 0:
                        _time.sleep(8)  # wait 8 seconds then retry once
                        continue
                    # Second failure — return friendly message instead of crash
                    return {
                        "verdict": "WATCH",
                        "confidence": "Low",
                        "risk": "Medium",
                        "risk_reason": "Analysis temporarily unavailable — Anthropic servers busy.",
                        "summary": "Anthropic's servers are currently overloaded. This is temporary — please try again in 30-60 seconds.",
                        "summary_macro": "Anthropic API unavailable (error 529). Try again in 30-60 seconds.",
                        "summary_macro_sentiment": "mixed",
                        "reasons_bull": ["Retry in 30-60 seconds — Anthropic overloaded"],
                        "reasons_bear": ["Analysis not available"],
                        "day_trade_note": "",
                        "swing_note": "Server busy — retry shortly.",
                        "invest_note": "",
                        "news_scores": [], "net_news_score": 0,
                        "trend_short": "Unknown", "trend_short_desc": "Retry required",
                        "trend_medium": "Unknown", "trend_medium_desc": "Retry required",
                        "trend_long": "Unknown", "trend_long_desc": "Retry required",
                        "pattern_bias": "Neutral", "pattern_bias_desc": "",
                        "chart_patterns": [], "candle_patterns": [],
                        "cycle_phase": "Unknown", "cycle_desc": "",
                        "market_risk": "Moderate", "market_risk_desc": "",
                        "news_sentiment": [],
                        "_overloaded": True
                    }
                else:
                    raise  # non-529 error — let the outer except handle it
        if _msg is None:
            raise _last_err
        raw = _msg.content[0].text.strip()
        # Strip markdown fences
        if '```' in raw:
            parts = raw.split('```')
            for part in parts:
                part = part.strip()
                if part.startswith('json'):
                    part = part[4:].strip()
                if part.startswith('{'):
                    raw = part
                    break
        # Find outermost JSON object
        start = raw.find('{')
        end   = raw.rfind('}')
        if start != -1 and end != -1 and end > start:
            raw = raw[start:end+1]
        raw = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\xb6\u2028\u2029]', ' ', raw)
        try:
            parsed = json.loads(raw.strip())
        except json.JSONDecodeError:
            # Second attempt — strip ALL control chars + fix trailing commas
            raw2 = re.sub(r'[\x00-\x1f\x7f]', ' ', raw)
            raw2 = re.sub(r',\s*}', '}', raw2)
            raw2 = re.sub(r',\s*]', ']', raw2)
            parsed = json.loads(raw2.strip())
        # Validate minimum required keys
        if 'verdict' not in parsed:
            return {"error": "Claude response missing required fields"}
        return parsed
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
    'GOOGL':[{'ticker':'GOOGL',   'name':'Alphabet Inc. Class C (No Vote)','exchange':'NASDAQ','currency':'USD'},
             {'ticker':'GOOG',    'name':'Alphabet Inc. Class A (Voting)', 'exchange':'NASDAQ','currency':'USD'}],
    'GOOG': [{'ticker':'GOOGL',   'name':'Alphabet Inc. Class C (No Vote)','exchange':'NASDAQ','currency':'USD'},
             {'ticker':'GOOG',    'name':'Alphabet Inc. Class A (Voting)', 'exchange':'NASDAQ','currency':'USD'}],
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
    sub_html = f'<div style="font-size:13px;color:#CBD5E1;margin-top:3px;letter-spacing:0.3px;">{subtitle}</div>' if subtitle else ""
    return f'''<div class="{cls}">
      <div class="sig-label">{label}{info_icon(label)}</div>
      {sub_html}
      <div class="{vcls}">{prefix}{val}</div>
    </div>'''

def data_row(label, val, cls="val-w", show_info=False):
    icon = info_icon(label) if show_info and label in INFO_LINKS else ""
    safe_val = _html.escape(str(val)) if val else val
    return f'<div class="data-row"><span class="data-lbl">{label}{icon}</span><span class="{cls}">{safe_val}</span></div>'

def range_bar_html(low, high, current, cur):
    if high <= low: return ""
    pct = max(0, min(100, int((current - low) / (high - low) * 100)))
    return f'''
    <div class="data-row" style="flex-direction:column;gap:6px;">
      <div style="display:flex;justify-content:space-between;width:100%;font-size:13px;">
        <span class="data-lbl">52W Range</span>
        <span class="val-m" style="font-size:13px;">{cur}{low:.2f} → {cur}{high:.2f}</span>
      </div>
      <div style="display:flex;align-items:center;gap:8px;width:100%;">
        <span style="font-size:13px;color:#CBD5E1;">{cur}{low:.0f}</span>
        <div class="range-wrap" style="flex:1;position:relative;height:6px;background:#243348;border-radius:3px;">
          <div class="range-fill" style="width:{pct}%;position:absolute;top:0;left:0;height:6px;border-radius:3px;background:linear-gradient(90deg,#FF6B6B,#FACC15,#00FF88);"></div>
          <div class="range-dot" style="left:{pct}%;position:absolute;top:-4px;width:12px;height:12px;background:#F1F5F9;border-radius:50%;transform:translateX(-50%);border:2px solid #111827;"></div>
        </div>
        <span style="font-size:13px;color:#CBD5E1;">{cur}{high:.0f}</span>
      </div>
      <div style="text-align:center;font-size:13px;color:#CBD5E1;">{cur}{current:.2f} — {pct}% of 52W range</div>
    </div>'''



# ── Main App ──────────────────────────────────────────────────
def main():
    with st.sidebar:
        st.markdown("""
        <div style="padding:4px 0 16px;border-bottom:1px solid #1E2D42;margin-bottom:12px;">
          <div style="font-size:13px;color:#CBD5E1;letter-spacing:3px;text-transform:uppercase;
                      margin-bottom:5px;font-family:'JetBrains Mono',monospace;">Stock Analysis HUD</div>
          <div style="font-size:16px;font-weight:800;color:#F1F5F9;margin-bottom:2px;">Trading Reference</div>
          <div style="font-size:13px;color:#CBD5E1;">Signal guide · Glossary · Controls</div>
        </div>""", unsafe_allow_html=True)

        with st.expander("📡  Signal Legend", expanded=False):
            st.markdown("""<div style="font-size:13px;color:#CBD5E1;letter-spacing:2px;text-transform:uppercase;
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
                  <div style="font-size:13px;font-weight:600;color:#CBD5E1;margin-bottom:3px;">{full_name}</div>
                  <div style="font-size:13px;color:#CBD5E1;line-height:1.5;">{explanation}</div>
                </div>""", unsafe_allow_html=True)

        with st.expander("📊  Score Guide", expanded=False):
            st.markdown("""<div style="font-size:13px;color:#CBD5E1;letter-spacing:2px;text-transform:uppercase;
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
                    <span style="font-size:13px;font-weight:700;color:{color};white-space:nowrap;">{label}</span>
                  </div>
                  <div style="font-size:13px;color:#CBD5E1;line-height:1.5;">{desc}</div>
                </div>""", unsafe_allow_html=True)

        with st.expander("📖  Glossary", expanded=False):
            st.markdown("""<div style="font-size:13px;color:#CBD5E1;letter-spacing:2px;text-transform:uppercase;
                        font-weight:700;margin-bottom:10px;font-family:'JetBrains Mono',monospace;">KEY TERMS</div>""",
                        unsafe_allow_html=True)
            glossary = [

                ("Signal Score — Swing Trade","#38BDF8","Rates setup quality for a 1–8 week hold. Weighted toward: Weinstein Phase (dominant), MA stack alignment, OBV institutional flow, Three Tailwinds regime, volume, and news catalyst quality. Fundamentals act as a multiplier. Score 7+ = strong setup. Default view."),
                ("Signal Score — Position","#00FF88","Rates setup quality for a 3–12 month hold. Weighted toward: fundamental business quality (dominant), analyst consensus vs price, Three Tailwinds macro regime, and business cycle alignment. RSI, MACD, and candles have no weight. Score 7+ = strong long-term case. Use for evaluating whether to own the business, not for timing entries."),
                ("ADR %","#00FF88","Average Daily Range %. How much the stock moves from low to high on an average day, as a percentage of price. At $176 with 3.2% ADR, that's ~$5.72 of daily movement. · Under 1.5%: Too slow — not enough range to trade profitably after commissions. · 1.5–4% Sweet spot: Ideal for day and swing trading — enough movement for good R/R, not so wild it stops you out constantly. · 4–6% High momentum: Good for experienced day traders, risky for beginners. · Above 6% Dangerous: Whipsaws are frequent, position sizing must be very small. For investing, ADR% is mostly noise — only matters if it's rapidly expanding (rising volatility = potential catalyst or deterioration)."),
                ("ATR","#FACC15","Average True Range. The average daily price swing in dollars over 14 days. At $176 with ATR $5.72 — the stock moves ~$5.72 from low to high on an average day. Use it to set stop losses, size positions, and judge whether the stock has enough range to trade profitably."),
                ("EPS","#38BDF8","Earnings Per Share. Net profit divided by shares outstanding. Beat the estimate = stock usually gaps up."),
                ("Fibonacci","#00FF88","Retracement levels (38.2%, 50%, 61.8%). Traders watch these as potential support/resistance in pullbacks."),
                ("Float","#94A3B8","The number of shares available to trade publicly. Low float stocks are more volatile."),
                ("MACD","#5EEAD4","Moving Average Convergence Divergence. Tracks trend momentum by comparing two exponential moving averages."),
                ("OBV","#00FF88","On-Balance Volume. Rising OBV with flat price = accumulation = bullish divergence."),
                ("P/E Ratio","#38BDF8","Price-to-Earnings. Stock price divided by annual EPS. High P/E = expensive or high growth expected."),
                ("PEG Ratio","#A78BFA","P/E divided by earnings growth rate. Under 1 = potentially undervalued relative to growth."),
                ("Phase","#F97316","Weinstein Phase. Stocks cycle: 1=Base, 2=Uptrend (buy zone), 3=Top, 4=Downtrend (avoid)."),
                ("R:R Ratio","#FACC15","Risk-to-Reward ratio — the most important number in any trade. It compares how much you stand to gain against how much you risk losing. \n\nThe minimum acceptable ratio is 1:2 — meaning for every $1 you risk, you need a realistic path to $2 of profit. Anything below 1:2 means the math works against you even if you win more than you lose. \n\n· Stop loss is placed below the nearest meaningful support level — the price where the original trade thesis is proven wrong. \n· Target is placed at the next significant resistance level — where sellers are likely to emerge and limit upside. \n· Entry is the current price zone where you're willing to buy. \n\nExample: Entry $100 · Stop $95 · Target $110. Risk = $5, Reward = $10. R:R = 1:2. If you take 10 trades at 1:2 and win only 4, you still break even. Win 5 out of 10 and you're profitable. This is why R:R matters more than win rate."),
                ("RSI","#A78BFA","Relative Strength Index (0–100). Above 70 = overbought. Below 30 = oversold."),
                ("Support","#00FF88","A price level where buying tends to outweigh selling — the stock has bounced from here before."),
                ("Resistance","#FF6B6B","A price level where selling tends to outweigh buying — the stock has struggled to break through here."),
                ("VWAP","#5EEAD4","Volume-Weighted Average Price. Day traders use it as a key intraday reference line."),
            ]
            for term, color, definition in glossary:
                st.markdown(f"""
                <div style="padding:8px 0;border-bottom:1px solid #1E2D42;">
                  <div style="font-family:'JetBrains Mono',monospace;font-size:13px;font-weight:700;
                               color:{color};margin-bottom:3px;">{term}</div>
                  <div style="font-size:13px;color:#CBD5E1;line-height:1.55;">{definition}</div>
                </div>""", unsafe_allow_html=True)

        st.markdown("""<div style="font-size:13px;color:#CBD5E1;letter-spacing:2px;text-transform:uppercase;
                    margin:16px 0 8px;font-family:'JetBrains Mono',monospace;">SYSTEM</div>""",
                    unsafe_allow_html=True)
        fmp_active = bool(st.secrets.get("FMP_API_KEY", ""))
        st.markdown(f"""
        <div style="background:#111827;border:1px solid #1E2D42;border-radius:6px;padding:8px 10px;margin-top:8px;">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
            <span style="font-size:13px;color:#CBD5E1;">Data source</span>
            <span style="font-size:13px;font-weight:700;color:{'#00FF88' if fmp_active else '#FACC15'};">
              {'🟢 FMP + yfinance' if fmp_active else '🟡 yfinance only'}
            </span>
          </div>
          <div style="display:flex;justify-content:space-between;align-items:center;">
            <span style="font-size:13px;color:#CBD5E1;">Cache TTL</span>
            <span style="font-size:13px;color:#CBD5E1;">60 min</span>
          </div>
        </div>""", unsafe_allow_html=True)
        st.markdown("""<div style="padding:16px 0 4px;font-size:13px;color:#CBD5E1;
                    line-height:1.6;text-align:center;">Educational only · Not financial advice</div>""",
                    unsafe_allow_html=True)

    if 'analysis' not in st.session_state:
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            st.markdown("<br>", unsafe_allow_html=True)
            st.markdown('<div style="text-align:center;font-size:13px;color:#CBD5E1;letter-spacing:3px;text-transform:uppercase;margin-bottom:16px;">Stock Analysis · AI HUD</div>', unsafe_allow_html=True)
            tab1, tab2, tab3 = st.tabs(["📊 Stock Analysis", "🎙️ Earnings Call Analyzer", "📈 Screener"])

            with tab1:
                st.markdown('<div style="text-align:center;font-size:24px;font-weight:800;color:#F1F5F9;margin-bottom:4px;">Enter a ticker</div>', unsafe_allow_html=True)
                st.markdown('<div style="text-align:center;font-size:13px;color:#4A6080;margin-bottom:10px;">Type any symbol — a dropdown will guide you</div>', unsafe_allow_html=True)

                fmp_key_lp = st.secrets.get("FMP_API_KEY", "")
                if "analysis_mode" not in st.session_state:
                    st.session_state["analysis_mode"] = "Quick"
                if "_confirmed_ticker" not in st.session_state:
                    st.session_state["_confirmed_ticker"] = None

                # ── MODE PILLS — clicking the card activates the mode ──
                cur_mode = st.session_state["analysis_mode"]
                mp1, mp2 = st.columns(2)
                with mp1:
                    if cur_mode == "Quick":
                        st.markdown("""<div style="background:#081510;border:2px solid #00FF88;
                            border-radius:8px;padding:12px 14px;margin-bottom:2px;">
                          <div style="display:flex;justify-content:space-between;align-items:center;">
                            <span style="font-family:'JetBrains Mono',monospace;font-size:13px;
                              font-weight:800;color:#00FF88;letter-spacing:1.5px;">⚡ QUICK</span>
                            <span style="font-size:13px;color:#00FF88;font-weight:700;">● ACTIVE</span>
                          </div>
                          <div style="font-size:13px;color:#CBD5E1;margin-top:4px;">Sonnet · ~15s · Full analysis</div>
                        </div>""", unsafe_allow_html=True)
                    else:
                        st.markdown("""<div style="background:#0D1525;border:1px solid #243348;
                            border-radius:8px;padding:12px 14px;margin-bottom:2px;opacity:0.7;">
                          <div style="display:flex;justify-content:space-between;align-items:center;">
                            <span style="font-family:'JetBrains Mono',monospace;font-size:13px;
                              font-weight:800;color:#CBD5E1;letter-spacing:1.5px;">⚡ QUICK</span>
                          </div>
                          <div style="font-size:13px;color:#94A3B8;margin-top:4px;">Sonnet · ~15s · Full analysis</div>
                        </div>""", unsafe_allow_html=True)
                    if st.button("⚡ Select Quick", key="mode_q", use_container_width=True):
                        st.session_state["analysis_mode"] = "Quick"
                        st.rerun()
                with mp2:
                    if cur_mode == "Deep Research":
                        st.markdown("""<div style="background:#060818;border:2px solid #38BDF8;
                            border-radius:8px;padding:12px 14px;margin-bottom:2px;
                            box-shadow:0 0 18px rgba(56,189,248,0.15);">
                          <div style="display:flex;justify-content:space-between;align-items:center;">
                            <span style="font-family:'JetBrains Mono',monospace;font-size:13px;
                              font-weight:800;color:#38BDF8;letter-spacing:1.5px;">🔬 DEEP RESEARCH</span>
                            <span style="font-size:13px;color:#38BDF8;font-weight:700;">● ACTIVE</span>
                          </div>
                          <div style="font-size:13px;color:#CBD5E1;margin-top:4px;">Opus · ~45s · 4× more thorough</div>
                        </div>""", unsafe_allow_html=True)
                    else:
                        st.markdown("""<div style="background:#0D1525;border:1px solid #243348;
                            border-radius:8px;padding:12px 14px;margin-bottom:2px;opacity:0.7;">
                          <div style="display:flex;justify-content:space-between;align-items:center;">
                            <span style="font-family:'JetBrains Mono',monospace;font-size:13px;
                              font-weight:800;color:#CBD5E1;letter-spacing:1.5px;">🔬 DEEP RESEARCH</span>
                          </div>
                          <div style="font-size:13px;color:#94A3B8;margin-top:4px;">Opus · ~45s · Multi-step reasoning</div>
                        </div>""", unsafe_allow_html=True)
                    if st.button("🔬 Select Deep Research", key="mode_d", use_container_width=True):
                        st.session_state["analysis_mode"] = "Deep Research"
                        st.rerun()

                st.markdown("<div style='margin-top:6px;'></div>", unsafe_allow_html=True)

                # ── PHASE 1: SEARCH ───────────────────────────
                if not st.session_state["_confirmed_ticker"]:

                    ticker_in    = st.text_input("", placeholder="NVDA", key="ticker_input",
                                                 label_visibility="collapsed")
                    ticker_upper = ticker_in.strip().upper() if ticker_in else ""
                    # Persist across mode-switch reruns
                    if ticker_upper:
                        st.session_state["_search_ticker"] = ticker_upper
                    else:
                        ticker_upper = st.session_state.get("_search_ticker", "")
                    st.session_state["_prev_ticker_val"] = ticker_upper

                    def _confirm(sym, name, exch, curr, name_found=True):
                        st.session_state["_confirmed_ticker"]   = sym
                        st.session_state["_confirmed_name"]     = name
                        st.session_state["_confirmed_exch"]     = exch
                        st.session_state["_confirmed_curr"]     = curr
                        st.session_state["_confirm_name_found"] = name_found
                        st.rerun()

                    def render_preview_card(sym, name, exch, curr, name_found=True):
                        border    = "#5EEAD4" if name_found else "#FACC15"
                        badge_col = "#5EEAD4" if name_found else "#FACC15"
                        badge_bg  = "#071A18" if name_found else "#1A1000"
                        badge_txt = "✓ Confirmed" if name_found else "⚠ Verify"
                        name_col  = "#F1F5F9" if name_found else "#FACC15"
                        name_txt  = name if name_found else "Name not found — verify this symbol"
                        exch_txt  = f"{exch} &nbsp;·&nbsp; {curr}" if exch else "Exchange unknown"
                        st.markdown(f"""<div style="background:linear-gradient(135deg,#0A1E2C,#0D1525);
                            border:1px solid {border};border-radius:10px;padding:12px 16px;
                            margin:8px 0 4px;display:flex;align-items:center;gap:14px;">
                          <div style="font-family:'JetBrains Mono',monospace;font-size:22px;
                            font-weight:800;color:#00FF88;letter-spacing:3px;min-width:70px;">{sym}</div>
                          <div style="flex:1;min-width:0;">
                            <div style="font-size:14px;font-weight:700;color:{name_col};
                              margin-bottom:2px;white-space:nowrap;overflow:hidden;
                              text-overflow:ellipsis;">{name_txt}</div>
                            <div style="font-size:13px;color:#CBD5E1;">{exch_txt}</div>
                          </div>
                          <div style="background:{badge_bg};border:1px solid {badge_col};
                            border-radius:20px;padding:3px 10px;font-size:13px;
                            font-weight:700;color:{badge_col};white-space:nowrap;">{badge_txt}</div>
                        </div>""", unsafe_allow_html=True)

                    def render_dropdown_search(rows):
                        unique_names = list({r["name"] for r in rows if r["name"]})
                        if len(unique_names) > 1:
                            st.markdown("""<div style="background:#1A1000;border:1px solid #FACC1566;
                                border-radius:8px;padding:8px 14px;margin:8px 0 4px;
                                display:flex;align-items:center;gap:8px;">
                              <span style="font-size:13px;">⚠️</span>
                              <span style="font-size:13px;font-weight:700;color:#FACC15;">
                                Same symbol — different companies</span>
                              <span style="font-size:13px;color:#CBD5E1;margin-left:4px;">
                                Read the full name carefully.</span>
                            </div>""", unsafe_allow_html=True)
                        st.markdown("""<div style="background:#071420;border:1px solid #5EEAD4;
                            border-radius:10px;overflow:hidden;margin-top:6px;">
                          <div style="padding:5px 14px;font-size:13px;color:#CBD5E1;letter-spacing:2px;
                            text-transform:uppercase;font-weight:700;border-bottom:1px solid #0D2030;
                            background:#040E18;">Select exchange or share class</div>
                        """, unsafe_allow_html=True)
                        for i, row in enumerate(rows):
                            bb = "" if i == len(rows)-1 else "border-bottom:1px solid #0D2030;"
                            st.markdown(f"""<div style="padding:10px 14px;{bb}display:flex;
                                align-items:center;gap:12px;">
                              <div style="font-family:'JetBrains Mono',monospace;font-weight:800;
                                color:#00FF88;font-size:14px;min-width:72px;">{row["sym"]}</div>
                              <div style="flex:1;">
                                <div style="font-size:13px;font-weight:600;color:#E2E8F0;
                                  margin-bottom:1px;">{row["name"]}</div>
                                <div style="font-size:13px;color:#CBD5E1;">
                                  {row["exch"]} · {row["curr"]}</div>
                              </div>
                            </div>""", unsafe_allow_html=True)
                            if st.button(f"Select {row['sym']}", key=row["key"],
                                         use_container_width=True):
                                _confirm(row["sym"], row["name"], row["exch"], row["curr"])
                        st.markdown("</div>", unsafe_allow_html=True)

                    if ticker_upper:
                        if ticker_upper in MULTI_LISTED:
                            rows = [{"sym": o["ticker"], "name": o["name"],
                                     "exch": o["exchange"], "curr": o["currency"],
                                     "key": f'ml_{o["ticker"]}'}
                                    for o in MULTI_LISTED[ticker_upper]]
                            if len(rows) == 1:
                                # Single entry — auto-confirm
                                _confirm(rows[0]["sym"], rows[0]["name"],
                                         rows[0]["exch"], rows[0]["curr"])
                            else:
                                # Multiple options — user must choose
                                render_dropdown_search(rows)
                        elif fmp_key_lp:
                            results = search_ticker_fmp(ticker_upper, fmp_key_lp)
                            # If no results, try treating input as a company name
                            # e.g. "Tesla" → finds TSLA, "Google" → finds GOOGL
                            _name_search = False
                            if not results and len(ticker_upper) > 3:
                                # Try as company name — multiple case variants
                                for _q in [ticker_upper.title(), ticker_upper.lower(),
                                           ticker_upper.title().replace(' Inc','').replace(' Corp','').strip()]:
                                    results = search_ticker_fmp(_q, fmp_key_lp)
                                    if results:
                                        _name_search = True
                                        break
                            if results:
                                # Exact ticker match AND not a name search → auto-confirm
                                exact = next((r for r in results
                                              if r.get("symbol","").upper() == ticker_upper), None)
                                if exact and not _name_search and len({r.get("symbol","").upper() for r in results
                                                  if r.get("symbol","").upper() == ticker_upper}) == 1:
                                    # Single unambiguous ticker match — auto-confirm
                                    nm = exact.get("name","")[:52]
                                    ex = exact.get("exchangeShortName","")
                                    cu = exact.get("currency","USD")
                                    _confirm(ticker_upper, nm, ex, cu)
                                else:
                                    # Name search or multiple results — always show dropdown
                                    # so user picks the real symbol (e.g. TSLA not TESLA)
                                    rows = [{"sym": r.get("symbol",""), "name": r.get("name","")[:45],
                                             "exch": r.get("exchangeShortName",""), "curr": r.get("currency","USD"),
                                             "key": f'fmp_{r.get("symbol","")}_{r.get("exchangeShortName","")}'}
                                            for r in results[:10] if r.get("symbol","")]
                                    render_dropdown_search(rows)
                            else:
                                cache_key = f"_yf_{ticker_upper}"
                                if cache_key not in st.session_state:
                                    matches = resolve_all_matches(ticker_upper)
                                    st.session_state[cache_key] = matches
                                matches = st.session_state.get(cache_key, [])
                                if len(matches) > 1:
                                    rows = [{"sym": m["sym"], "name": m["name"],
                                             "exch": m["exchange"], "curr": m["currency"],
                                             "key": f'yf_{m["sym"]}_{m["exchange"]}'}
                                            for m in matches]
                                    render_dropdown_search(rows)
                                elif len(matches) == 1:
                                    # Single yfinance match — auto-confirm
                                    m = matches[0]
                                    _confirm(m["sym"], m["name"], m["exchange"], m["currency"])
                                else:
                                    # Check if input looks like a company name (not a ticker)
                                    _looks_like_name = (
                                        len(ticker_upper) > 4 and
                                        not any(c.isdigit() for c in ticker_upper) and
                                        ticker_upper not in MULTI_LISTED
                                    )
                                    if _looks_like_name:
                                        st.markdown(f"""
                                        <div style="background:#1A1000;border:1px solid #FACC1566;
                                            border-radius:10px;padding:14px 18px;margin-top:8px;">
                                          <div style="font-size:14px;font-weight:700;color:#FACC15;
                                            margin-bottom:6px;">⚠️ Company name not found</div>
                                          <div style="font-size:13px;color:#CBD5E1;line-height:1.6;">
                                            Try the ticker symbol instead.<br>
                                            Examples: <span style="color:#00FF88;font-family:monospace;
                                            font-weight:700;">GOOGL</span> for Google &nbsp;·&nbsp;
                                            <span style="color:#00FF88;font-family:monospace;
                                            font-weight:700;">TSLA</span> for Tesla &nbsp;·&nbsp;
                                            <span style="color:#00FF88;font-family:monospace;
                                            font-weight:700;">AAPL</span> for Apple
                                          </div>
                                        </div>""", unsafe_allow_html=True)
                                    else:
                                        render_preview_card(ticker_upper, "", "", "", name_found=False)
                                        if st.button(f"Select {ticker_upper} →", type="primary",
                                                     use_container_width=True, key="select_unk"):
                                            _confirm(ticker_upper, "", "", "", name_found=False)
                        else:
                            # No FMP key — auto-confirm directly
                            _confirm(ticker_upper, "", "", "", name_found=False)

                # ── PHASE 2: CONFIRM CARD ─────────────────────
                if st.session_state.get("_confirmed_ticker"):
                    sym        = st.session_state["_confirmed_ticker"]
                    name       = st.session_state.get("_confirmed_name", "")
                    exch       = st.session_state.get("_confirmed_exch", "")
                    curr       = st.session_state.get("_confirmed_curr", "")
                    name_found = st.session_state.get("_confirm_name_found", True)

                    card_border  = "#5EEAD4" if name_found else "#FACC15"
                    badge_col    = "#5EEAD4" if name_found else "#FACC15"
                    badge_bg     = "#071A18" if name_found else "#1A1000"
                    badge_txt    = "✓ Confirmed" if name_found else "⚠ Verify"
                    name_col     = "#F1F5F9" if name_found else "#FACC15"
                    name_display = name if name else sym
                    exch_display = f"{exch} &nbsp;·&nbsp; {curr}" if exch else ""

                    st.markdown(f"""<div style="background:linear-gradient(135deg,#0A1E2C,#0D1525);
                        border:1px solid {card_border};border-radius:12px;
                        padding:16px 20px;margin-top:4px;
                        box-shadow:0 4px 24px rgba(0,0,0,0.3);">
                      <div style="display:flex;align-items:center;gap:16px;">
                        <div style="font-family:'JetBrains Mono',monospace;font-size:26px;
                          font-weight:800;color:#00FF88;letter-spacing:4px;min-width:80px;
                          text-shadow:0 0 20px #00FF8830;">{sym}</div>
                        <div style="flex:1;min-width:0;">
                          <div style="font-size:15px;font-weight:700;color:{name_col};
                            margin-bottom:3px;white-space:nowrap;overflow:hidden;
                            text-overflow:ellipsis;">{name_display}</div>
                          <div style="font-size:13px;color:#CBD5E1;">{exch_display}</div>
                        </div>
                        <div style="background:{badge_bg};border:1px solid {badge_col};
                          border-radius:20px;padding:3px 10px;font-size:13px;
                          font-weight:700;color:{badge_col};">{badge_txt}</div>
                      </div>
                    </div>""", unsafe_allow_html=True)

                    ca, cb = st.columns([4, 1])
                    with ca:
                        if st.button(f"Analyze {name_display} →", type="primary",
                                     use_container_width=True, key="analyze_confirm"):
                            run_analysis(sym)
                    with cb:
                        if st.button("← Back", use_container_width=True,
                                     key="change_ticker"):
                            for k in ["_confirmed_ticker","_confirmed_name",
                                      "_confirmed_exch","_confirmed_curr",
                                      "_confirm_name_found","_resolved_name",
                                      "_resolved_exch","_resolved_curr",
                                      "_search_ticker"]:
                                st.session_state.pop(k, None)
                            for k in list(st.session_state.keys()):
                                if k.startswith("_yf_"):
                                    st.session_state.pop(k, None)
                            st.rerun()

                st.markdown('<div style="text-align:center;font-size:13px;color:#CBD5E1;margin-top:20px;">US · TSX · LSE · Euronext · HKEX · ASX — all major exchanges supported</div>', unsafe_allow_html=True)
                render_disclaimer()


            with tab2:
                st.markdown("""
                <div style="text-align:center;padding:60px 20px;">
                  <div style="font-size:36px;margin-bottom:16px;">🎙️</div>
                  <div style="font-size:22px;font-weight:800;color:#F1F5F9;margin-bottom:8px;">
                    Earnings Call Analyzer
                  </div>
                  <div style="font-size:13px;color:#4A6080;margin-bottom:24px;">
                    Paste any earnings call transcript and get an instant AI teardown
                  </div>
                  <div style="display:inline-block;background:#251800;border:1px solid #FACC15;
                              border-radius:8px;padding:8px 20px;">
                    <span style="font-size:13px;font-weight:700;color:#FACC15;letter-spacing:2px;">
                      🚧 COMING SOON
                    </span>
                  </div>
                </div>""", unsafe_allow_html=True)

            with tab3:
                st.markdown("""
                <div style="text-align:center;padding:60px 20px;">
                  <div style="font-size:36px;margin-bottom:16px;">📈</div>
                  <div style="font-size:22px;font-weight:800;color:#F1F5F9;margin-bottom:8px;">
                    Screener
                  </div>
                  <div style="font-size:13px;color:#4A6080;margin-bottom:24px;">
                    Find stocks matching specific criteria — describe what you're looking for
                  </div>
                  <div style="display:inline-block;background:#251800;border:1px solid #FACC15;
                              border-radius:8px;padding:8px 20px;">
                    <span style="font-size:13px;font-weight:700;color:#FACC15;letter-spacing:2px;">
                      🚧 COMING SOON
                    </span>
                  </div>
                </div>""", unsafe_allow_html=True)
        return

    render_hud()



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
        signals, score = calc_signals(row, prev)
        phase_result = detect_weinstein_phase(df.tail(300))
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

        analysis_mode = st.session_state.get('analysis_mode', 'Quick')
        if analysis_mode == 'Deep Research':
            prog.info(f"🔬 Running Deep Research on {ticker}... (30-45 sec)")
        else:
            prog.info(f"⚡ Running Quick Analysis on {ticker}... (10-15 sec)")
        analysis = get_claude_analysis(ticker, info, df, signals, score, fibs, news_items, market_ctx, mode=analysis_mode)
        if 'error' in analysis:
            prog.empty()
            err_msg = analysis['error']
            if '529' in err_msg or 'overloaded' in err_msg.lower():
                st.warning("⏳ Anthropic servers are busy right now. This happens during peak hours — please wait 30-60 seconds and try again.")
            else:
                st.error(f"Claude API error: {err_msg}")
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
                def _rs(row, *keys):
                    for k in keys:
                        try:
                            v = row[k]
                            if v is not None: return int(v or 0)
                        except: pass
                    return 0
                buy_cnt  = _rs(r,'strongBuy') + _rs(r,'buy')
                hold_cnt = _rs(r,'hold')
                sell_cnt = _rs(r,'strongSell') + _rs(r,'sell')
                num_ana  = buy_cnt + hold_cnt + sell_cnt
        except: pass

        if target_mean == 0:
            target_mean = float(info.get('targetMeanPrice') or 0)
            target_low  = float(info.get('targetLowPrice')  or 0)
            target_high = float(info.get('targetHighPrice') or 0)
        if num_ana == 0:
            num_ana = int(info.get('numberOfAnalystOpinions') or 0)

        # ── yfinance fallback for analyst + earnings + insider ───
        # One Ticker instance, all fallback fetches in parallel
        _yf_sym = ticker.replace('BRK.B','BRK-B').replace('BRK.A','BRK-A')
        _yt = None
        try: _yt = yf.Ticker(_yf_sym)
        except: pass

        if _yt is not None:
            from concurrent.futures import ThreadPoolExecutor as _TPE
            def _yf_apt():
                try: return _yt.analyst_price_targets
                except: return None
            def _yf_recs():
                try: return _yt.recommendations_summary
                except: return None
            def _yf_eh():
                try: return _yt.earnings_history
                except: return None
            def _yf_ed():
                try: return _yt.earnings_dates
                except: return None
            def _yf_ins():
                try: return _yt.insider_transactions
                except: return None

            with _TPE(max_workers=5) as _p:
                _f_apt  = _p.submit(_yf_apt)
                _f_recs = _p.submit(_yf_recs)
                _f_eh   = _p.submit(_yf_eh)
                _f_ed   = _p.submit(_yf_ed)
                _f_ins  = _p.submit(_yf_ins)
                _yf_apt_r  = _f_apt.result()
                _yf_recs_r = _f_recs.result()
                _yf_eh_r   = _f_eh.result()
                _yf_ed_r   = _f_ed.result()
                _yf_ins_r  = _f_ins.result()
        else:
            _yf_apt_r = _yf_recs_r = _yf_eh_r = _yf_ed_r = _yf_ins_r = None

        if target_mean == 0 and _yf_apt_r and isinstance(_yf_apt_r, dict):
            target_mean = float(_yf_apt_r.get('mean') or _yf_apt_r.get('current') or 0)
            target_low  = float(_yf_apt_r.get('low')  or 0)
            target_high = float(_yf_apt_r.get('high') or 0)

        if buy_cnt == 0 and _yf_recs_r is not None and not _yf_recs_r.empty:
            r = _yf_recs_r.iloc[0]
            def _rc(row, *keys):
                for k in keys:
                    try:
                        v = row[k]
                        if v is not None: return int(v or 0)
                    except: pass
                return 0
            buy_cnt  = _rc(r,'strongBuy','strong_buy') + _rc(r,'buy')
            hold_cnt = _rc(r,'hold')
            sell_cnt = _rc(r,'strongSell','strong_sell') + _rc(r,'sell')
            num_ana  = max(num_ana, buy_cnt + hold_cnt + sell_cnt)

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
        if (eh is None or (hasattr(eh,'empty') and eh.empty)) and _yf_eh_r is not None and not _yf_eh_r.empty:
            eh = _yf_eh_r
        if (eh is None or (hasattr(eh,'empty') and eh.empty)) and _yf_ed_r is not None and not _yf_ed_r.empty:
            _ed = _yf_ed_r[_yf_ed_r.index <= pd.Timestamp.now()]
            if not _ed.empty:
                eh = _ed
        # Hard FMP fallback — direct call if all yfinance sources returned empty
        if (eh is None or (hasattr(eh,'empty') and eh.empty)) and fmp_key:
            try:
                _fmp_surp = _fmp_get(f"v3/earnings-surprises/{ticker}", fmp_key)
                if _fmp_surp and isinstance(_fmp_surp, list):
                    _fmp_rows = []
                    for _e in _fmp_surp[:4]:
                        _act = float(_e.get("actualEarningResult", _e.get("actualEps", 0)) or 0)
                        _est = float(_e.get("estimatedEarning",    _e.get("estimatedEps", 0)) or 0)
                        _sp  = ((_act - _est) / abs(_est) * 100) if _est != 0 else 0
                        _fmp_rows.append({"period": _e.get("date",""), "epsEstimate": _est,
                                          "epsActual": _act, "surprisePercent": _sp})
                    if _fmp_rows:
                        eh = pd.DataFrame(_fmp_rows)
            except: pass
        try:
            if eh is not None and not eh.empty:
                for _, er in eh.head(4).iterrows():
                    def _er(row, *keys, default=0):
                        for k in keys:
                            try:
                                v = row[k]
                                if v is not None and str(v) not in ('nan','NaT',''): return v
                            except: pass
                        return default
                    est = float(_er(er,'epsEstimate','EPS Estimate','estimate') or 0)
                    act = float(_er(er,'epsActual','Reported EPS','actual') or 0)
                    surp_raw = _er(er,'surprisePercent','Surprise(%)','surprise', default=None)
                    if surp_raw is not None:
                        sv = float(surp_raw or 0)
                        surp = sv * 100 if abs(sv) <= 2 else sv
                    else:
                        surp = ((act - est) / abs(est) * 100) if est != 0 else 0
                    qtr = str(_er(er,'period','Date', default=er.name if hasattr(er,'name') else ''))[:10]
                    if act != 0 or est != 0:
                        earnings_hist.append({'quarter': qtr, 'estimate': est,
                                              'actual': act, 'surprise': surp, 'beat': surp > 0})
        except: pass

        try:
            ins = data.get('insider')
            if (ins is None or (hasattr(ins,'empty') and ins.empty)) and _yf_ins_r is not None and not _yf_ins_r.empty:
                ins = _yf_ins_r
            if ins is not None and not ins.empty:
                for _, ri in ins.head(5).iterrows():
                    def _ri(row, *keys, default=''):
                        for k in keys:
                            try:
                                v = row[k]
                                if v is not None and str(v) not in ('nan','NaT',''): return v
                            except: pass
                        return default
                    shares = int(float(_ri(ri,'Shares','shares', default=0) or 0))
                    val    = float(_ri(ri,'Value','value', default=0) or 0)
                    text   = str(_ri(ri,'Text','text') or '')
                    trans  = str(_ri(ri,'Transaction','transaction') or '')
                    name   = str(_ri(ri,'Insider','filerName','insider') or '')
                    role   = str(_ri(ri,'Position','filerRelation') or '')
                    date_i = str(_ri(ri,'Date','startDate') or '')
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
        # Pre-fetch comparison data for relative performance panel — zero-cost on arrow press
        # Try multiple sector field sources — yfinance and FMP use different field names
        sector_name_run = (
            str(info.get('sector') or '') or
            str(info.get('sectorDisp') or '') or
            str(info.get('industry') or '')
        ).strip()
        sector_etf_run  = SECTOR_ETF_MAP.get(sector_name_run, '')
        # If still no match, try case-insensitive lookup
        if not sector_etf_run and sector_name_run:
            for k, v in SECTOR_ETF_MAP.items():
                if k.lower() in sector_name_run.lower() or sector_name_run.lower() in k.lower():
                    sector_etf_run = v
                    break
        comp_data = fetch_comparison_data(sector_etf_run) if sector_etf_run else fetch_comparison_data('SPY')
        # Stock returns precomputed from df
        TFS_RUN = ['1W', '1M', 'QTD', 'YTD', '1Y']
        stk_returns = {tf: _perf_ret(df['Close'], tf) for tf in TFS_RUN}

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
        st.session_state.earn_date_str  = earn_date_str
        st.session_state.days_to_earn   = days_to_earn
        st.session_state.analysis_mode  = analysis_mode
        st.session_state.comp_data      = comp_data
        st.session_state.stk_returns    = stk_returns
        st.session_state.sector_etf_run = sector_etf_run  # store for render — avoids re-derive issue

        # Precompute expensive render-time values once — avoids recomputing on every rerun
        _sector_name_s = (
            str(info.get('sector','') or '') or
            str(info.get('sectorDisp','') or '') or
            str(info.get('industry','') or '')
        ).strip()
        _sector_etf_s = SECTOR_ETF_MAP.get(_sector_name_s, '')
        if not _sector_etf_s and _sector_name_s:
            for k, v in SECTOR_ETF_MAP.items():
                if k.lower() in _sector_name_s.lower() or _sector_name_s.lower() in k.lower():
                    _sector_etf_s = v
                    break
        _mkt_phase_s   = get_market_phase()
        _sec_phase_s   = get_sector_phase(_sector_etf_s) if _sector_etf_s else (0,'N/A','No ETF','#94A3B8',0,'','')
        _fs_s          = fundamental_screen(info, analysis.get('verdict',''))
        _tf_scores_s   = calc_timeframe_scores(
            row, prev, df, info, signals, phase_result,
            {**market_ctx, 'mkt_phase': _mkt_phase_s[0], 'sec_phase': _sec_phase_s[0]},
            analysis, _fs_s
        )
        st.session_state.mkt_phase_pre  = _mkt_phase_s
        st.session_state.sec_phase_pre  = _sec_phase_s
        st.session_state.fs_pre         = _fs_s
        st.session_state.tf_scores_pre  = _tf_scores_s
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


# ── Disclaimer ────────────────────────────────────────────────
def render_disclaimer():
    st.markdown("""
    <div style="background:#1A1000;border:1px solid #FACC1544;border-radius:8px;
                padding:12px 18px;margin-top:24px;margin-bottom:8px;">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">
        <span style="font-size:15px;">&#9888;&#65039;</span>
        <span style="font-size:13px;color:#CBD5E1;font-weight:700;letter-spacing:0.03em;">
          Educational tool only - not financial advice
        </span>
      </div>
      <div style="font-size:13px;color:#CBD5E1;line-height:1.7;">
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
                "**2. No Warranty** - Provided as-is without warranty.\n\n"
                "**3. Limitation of Liability** - The operator shall not be liable for any "
                "financial losses arising from use of this platform.\n\n"
                "**4. No Fiduciary Relationship** - Use of this tool does not create an "
                "advisory or professional relationship of any kind.\n\n"
                "**5. Regulatory Notice (Canada)** - This tool is not registered with the "
                "Autorite des marches financiers (AMF) or any other securities regulator.\n\n"
                "**6. Third-Party Services** - This platform uses Anthropic Claude API, "
                "yfinance, and Financial Modeling Prep (FMP).\n\n"
                "**7. Changes** - Terms may be updated at any time."
            )
    with col2:
        with st.expander("Privacy Policy"):
            st.markdown(
                "**Last updated: March 2026**\n\n"
                "**What we collect** - We do not collect, store, or sell any personal information.\n\n"
                "**Third-party logging:**\n"
                "- Streamlit Cloud may log usage metadata\n"
                "- Anthropic processes ticker queries via Claude API\n"
                "- Financial Modeling Prep provides market data\n\n"
                "**Cookies** - We do not set cookies."
            )

    st.markdown(
        '<div style="text-align:center;font-size:13px;color:#CBD5E1;padding:8px 0;letter-spacing:1px;">'
        'AI-GENERATED - NOT FINANCIAL ADVICE - EDUCATIONAL PURPOSES ONLY'
        '</div>',
        unsafe_allow_html=True
    )


def render_hud():
    a             = st.session_state.analysis
    df            = st.session_state.df
    info          = st.session_state.info
    ticker        = st.session_state.ticker
    signals       = st.session_state.signals
    score         = st.session_state.score
    fibs          = st.session_state.fibs
    row           = st.session_state.row
    prev          = st.session_state.prev
    analyst_data  = st.session_state.get('analyst_data', {})
    earnings_hist = st.session_state.get('earnings_hist', [])
    insider_data  = st.session_state.get('insider_data', [])
    news_items    = st.session_state.get('news_items', [])
    vol_data      = st.session_state.get('vol_data', {})
    earn_date_str = st.session_state.get('earn_date_str', 'Unknown')
    days_to_earn  = st.session_state.get('days_to_earn', 0)
    phase_result  = st.session_state.get('phase_result', (0, 'PHASE ?', 'Unclear', '#94A3B8', 0, '', ''))
    comp_data     = st.session_state.get('comp_data', {'spy': {}, 'sec': {}})
    stk_returns   = st.session_state.get('stk_returns', {})

    # Read precomputed values from session state — no recomputation on reruns
    sector_name_hud = (
        str(info.get('sector') or '') or
        str(info.get('sectorDisp') or '') or
        str(info.get('industry') or '')
    ).strip()
    sector_etf_hud  = SECTOR_ETF_MAP.get(sector_name_hud, '')
    if not sector_etf_hud and sector_name_hud:
        for k, v in SECTOR_ETF_MAP.items():
            if k.lower() in sector_name_hud.lower() or sector_name_hud.lower() in k.lower():
                sector_etf_hud = v
                break
    mkt_phase_hud   = st.session_state.get('mkt_phase_pre') or get_market_phase()
    sec_phase_hud   = st.session_state.get('sec_phase_pre') or (get_sector_phase(sector_etf_hud) if sector_etf_hud else (0,'N/A','No ETF','#94A3B8',0,'',''))

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

    sign      = "+" if chg >= 0 else ""
    vc        = VERDICT_COLORS.get(a.get('verdict','TECHNICAL SETUP'), VERDICT_COLORS['TECHNICAL SETUP'])
    score_col = "#00FF88" if score >= 7 else "#FACC15" if score >= 4 else "#FF6B6B"

    h52 = float(info.get('fiftyTwoWeekHigh', df['High'].tail(252).max()))
    l52 = float(info.get('fiftyTwoWeekLow',  df['Low'].tail(252).min()))
    vol = float(row['Volume'])
    atr_pct = float(row['ATRPct'])

    company = info.get('longName', info.get('shortName', ticker))
    if not company or company == ticker or company == ticker.replace('-','.'):
        resolved = st.session_state.get("_resolved_name", "")
        if resolved:
            company = resolved
    sector   = info.get('sector', a.get('sector',''))
    exchange = 'TSX' if ticker.endswith('.TO') else 'LSE' if ticker.endswith('.L') else 'NYSE / NASDAQ'

    if st.button("← New Analysis", key="btn_new_ticker"):
        for k in ['analysis','df','info','ticker','signals','score','fibs','row','prev',
                  '_prev_ticker_val','rr_mode','_rr_ticker','_resolved_name',
                  '_resolved_exch','_resolved_curr','analysis_mode',
                  '_confirmed_ticker','_confirmed_name','_confirmed_exch',
                  '_confirmed_curr','_confirm_name_found','score_timeframe',
                  'perf_timeframe']:
            if k in st.session_state: del st.session_state[k]
        st.rerun()

    chg_badge = (f'<span class="price-change-up">▲ {sign}{chg:.2f} ({sign}{chg_pct:.2f}%)</span>'
                 if chg >= 0 else
                 f'<span class="price-change-dn">▼ {chg:.2f} ({chg_pct:.2f}%)</span>')

    st.markdown(f'''
    <div class="identity-bar" style="border-top:3px solid {vc["border"]};">
      <div style="display:flex;align-items:center;gap:18px;">
        <div class="ticker-name">{ticker}</div>
        <div>
          <div class="company-name">{company}</div>
          <div style="margin-top:4px;">
            <span class="exchange-pill">{exchange}</span>
            <span style="font-size:13px;color:#CBD5E1;margin-left:8px;">{sector}</span>
          </div>
        </div>
      </div>
      <div style="text-align:right;">
        <div class="price-display">{cur}{close:.2f}</div>
        <div style="text-align:right;margin-top:6px;">{chg_badge}</div>
        <div style="margin-top:5px;display:flex;gap:6px;justify-content:flex-end;flex-wrap:wrap;">
          {"<span style='background:#0A3020;border:1px solid #00FF88;border-radius:4px;padding:2px 8px;font-size:13px;color:#CBD5E1;letter-spacing:1px;'>&#x26A1; FMP</span>" if st.secrets.get("FMP_API_KEY","") else "<span style='background:#2A1500;border:1px solid #FACC15;border-radius:4px;padding:2px 8px;font-size:13px;color:#CBD5E1;letter-spacing:1px;'>&#x26A0; yfinance</span>"}
          <span style="background:{phase_result[3]}18;border:1px solid {phase_result[3]};border-radius:4px;padding:2px 10px;font-size:13px;color:{phase_result[3]};letter-spacing:1px;font-weight:800;font-family:'JetBrains Mono',monospace;">{phase_result[1]} · {phase_result[2]}</span>
        </div>
      </div>
    </div>''', unsafe_allow_html=True)

    import streamlit.components.v1 as components

    st.markdown(f'''
    <div class="status-bar" style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:4px;">
      <div style="display:flex;gap:16px;align-items:flex-end;flex-wrap:wrap;">
        <div style="text-align:center;"><div style="color:#99F6E4;font-weight:700;">{row["Open"]:.2f}</div><div style="font-size:13px;color:#CBD5E1;letter-spacing:1px;text-transform:uppercase;">Open</div></div>
        <div style="text-align:center;"><div style="color:#99F6E4;font-weight:700;">{row["High"]:.2f}</div><div style="font-size:13px;color:#CBD5E1;letter-spacing:1px;text-transform:uppercase;">High</div></div>
        <div style="text-align:center;"><div style="color:#99F6E4;font-weight:700;">{row["Low"]:.2f}</div><div style="font-size:13px;color:#CBD5E1;letter-spacing:1px;text-transform:uppercase;">Low</div></div>
        <div style="text-align:center;"><div style="color:#99F6E4;font-weight:700;">{fmt_vol(vol)}</div><div style="font-size:13px;color:#CBD5E1;letter-spacing:1px;text-transform:uppercase;">Volume</div></div>
        <div style="text-align:center;"><div style="color:#99F6E4;font-weight:700;">{row["VolTrend"]:.2f}x</div><div style="font-size:13px;color:#CBD5E1;letter-spacing:1px;text-transform:uppercase;">Avg Vol</div></div>
        <div style="text-align:center;"><div style="color:#99F6E4;font-weight:700;">{cur}{float(row["ATR"]):.2f} ({atr_pct*100:.1f}%)</div><div style="font-size:13px;color:#CBD5E1;letter-spacing:1px;text-transform:uppercase;">Daily Range</div></div>
      </div>
      <div id="hud-localtime" style="color:#CBD5E1;font-size:13px;">--</div>
    </div>''', unsafe_allow_html=True)

    # Local time — JS reads browser clock directly
    components.html(
        """
        <script>
        (function() {
            var now = new Date();
            var months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
            var h = now.getHours(), m = now.getMinutes().toString().padStart(2,"0");
            var str = months[now.getMonth()] + " " + now.getDate() + " \u00b7 "
                    + (h % 12 || 12) + ":" + m + " " + (h >= 12 ? "PM" : "AM");
            try { var el = window.parent.document.getElementById("hud-localtime"); if (el) el.innerText = str; } catch(e) {}
        })();
        </script>
        """, height=0)

    # ── Volume Breakout Flag ─────────────────────────────────
    vol_ratio      = float(row['VolTrend'])
    price_20d_high = float(df['Close'].rolling(20).max().iloc[-2])
    price_break    = close > price_20d_high
    vol_confirm    = vol_ratio >= 1.5
    vol_surge      = vol_ratio >= 2.0

    if price_break and vol_confirm:
        st.markdown(f'''<div style="background:#052A14;border:1px solid #00FF88;border-radius:8px;
            padding:8px 16px;margin:6px 0;display:flex;align-items:center;gap:10px;">
          <span style="font-size:16px;">⚡</span>
          <div><span style="color:#00FF88;font-weight:800;font-size:13px;font-family:'JetBrains Mono',monospace;">
            BREAKOUT CONFIRMED</span>
          <span style="color:#CBD5E1;font-size:13px;margin-left:10px;">
            Price broke 20-day high on {vol_ratio:.1f}x average volume — institutional participation confirmed</span>
          </div></div>''', unsafe_allow_html=True)
    elif price_break and not vol_confirm:
        st.markdown(f'''<div style="background:#251800;border:1px solid #FACC15;border-radius:8px;
            padding:8px 16px;margin:6px 0;display:flex;align-items:center;gap:10px;">
          <span style="font-size:16px;">⚠️</span>
          <div><span style="color:#FACC15;font-weight:800;font-size:13px;font-family:'JetBrains Mono',monospace;">
            BREAKOUT UNCONFIRMED</span>
          <span style="color:#CBD5E1;font-size:13px;margin-left:10px;">
            Price broke 20-day high but volume only {vol_ratio:.1f}x average — wait for volume confirmation</span>
          </div></div>''', unsafe_allow_html=True)
    elif vol_surge and not price_break:
        st.markdown(f'''<div style="background:#0A1525;border:1px solid #38BDF8;border-radius:8px;
            padding:8px 16px;margin:6px 0;display:flex;align-items:center;gap:10px;">
          <span style="font-size:16px;">📊</span>
          <div><span style="color:#38BDF8;font-weight:800;font-size:13px;font-family:'JetBrains Mono',monospace;">
            VOLUME SURGE</span>
          <span style="color:#CBD5E1;font-size:13px;margin-left:10px;">
            {vol_ratio:.1f}x average volume — unusual activity, watch for a price move</span>
          </div></div>''', unsafe_allow_html=True)

    # ── Wide-Ranging Bar Flag ─────────────────────────────────
    atr_val   = float(row['ATR'])
    candle_range = float(row['High']) - float(row['Low'])
    wide_bar  = candle_range > 2.5 * atr_val
    if wide_bar:
        up_candle = float(row['Close']) >= float(row['Open'])
        wrb_col   = '#38BDF8' if up_candle else '#FF6B6B'
        wrb_bg    = '#040E18' if up_candle else '#1A0505'
        wrb_icon  = '⚡' if up_candle else '🔻'
        wrb_dir   = 'BULLISH' if up_candle else 'BEARISH'
        wrb_note  = ('Overwhelming buying — prior resistance levels may now act as support'
                     if up_candle else
                     'Overwhelming selling — prior support levels may no longer hold')
        st.markdown(f'''<div style="background:{wrb_bg};border:1px solid {wrb_col};border-radius:8px;
            padding:8px 16px;margin:6px 0;display:flex;align-items:center;gap:10px;">
          <span style="font-size:16px;">{wrb_icon}</span>
          <div><span style="color:{wrb_col};font-weight:800;font-size:13px;
            font-family:'JetBrains Mono',monospace;">
            WIDE-RANGING BAR — {wrb_dir}</span>
          <span style="color:{wrb_col}99;font-size:13px;margin-left:10px;">
            Range {candle_range:.2f} = {candle_range/atr_val:.1f}× ATR — {wrb_note}</span>
          </div></div>''', unsafe_allow_html=True)

    # ── OBV Divergence Flag ──────────────────────────────────
    obv_div = int(df['OBV_div'].iloc[-1]) if 'OBV_div' in df.columns else 0
    if obv_div == 1:
        st.markdown('''<div style="background:#052A14;border:1px solid #00FF88;border-left:4px solid #00FF88;
            border-radius:8px;padding:8px 16px;margin:6px 0;display:flex;align-items:center;gap:10px;">
          <span style="font-size:16px;">📈</span>
          <div><span style="color:#00FF88;font-weight:800;font-size:13px;font-family:'JetBrains Mono',monospace;">
            BULLISH OBV DIVERGENCE</span>
          <span style="color:#CBD5E1;font-size:13px;margin-left:10px;">
            Price declining but OBV rising — institutions accumulating quietly.</span>
          </div></div>''', unsafe_allow_html=True)
    elif obv_div == -1:
        st.markdown('''<div style="background:#2D1015;border:1px solid #FF6B6B;border-left:4px solid #FF6B6B;
            border-radius:8px;padding:8px 16px;margin:6px 0;display:flex;align-items:center;gap:10px;">
          <span style="font-size:16px;">📉</span>
          <div><span style="color:#FF6B6B;font-weight:800;font-size:13px;font-family:'JetBrains Mono',monospace;">
            BEARISH OBV DIVERGENCE</span>
          <span style="color:#CBD5E1;font-size:13px;margin-left:10px;">
            Price rising but OBV falling — smart money distributing. Tighten stops.</span>
          </div></div>''', unsafe_allow_html=True)

    bull_count = sum(1 for k,v in signals.items() if v['bull'])
    bull_names = " · ".join(signals[k]["label"] for k in signals if signals[k]["bull"]) or "None"
    bear_names = " · ".join(signals[k]["label"] for k in signals if not signals[k]["bull"]) or "None"

    # Analysis mode badge
    analysis_mode = st.session_state.get('analysis_mode', 'Quick')
    if analysis_mode == 'Deep Research':
        mode_badge = '<span style="background:#0A1525;border:1px solid #A78BFA;border-radius:4px;padding:2px 8px;font-size:13px;color:#CBD5E1;font-weight:700;letter-spacing:1px;margin-left:8px;">🔬 DEEP RESEARCH</span>'
    else:
        mode_badge = '<span style="background:#0A1525;border:1px solid #38BDF8;border-radius:4px;padding:2px 8px;font-size:13px;color:#CBD5E1;font-weight:700;letter-spacing:1px;margin-left:8px;">⚡ QUICK</span>'

    # Read precomputed values — no recalculation on arrow press reruns
    fs        = st.session_state.get('fs_pre') or fundamental_screen(info, a.get('verdict',''))
    tf_scores = st.session_state.get('tf_scores_pre') or calc_timeframe_scores(
        row, prev, df, info, signals, phase_result,
        {**st.session_state.get('market_ctx',{}),
         'mkt_phase': mkt_phase_hud[0], 'sec_phase': sec_phase_hud[0]},
        a, fs
    )
    fs_icon = (
        '📊' if fs['verdict_text'] == 'Passes fundamental screens' else
        '⚡' if fs['verdict_text'] == 'Technical setup only' else
        '🟡' if fs['verdict_text'] == 'Mixed fundamental picture' else
        'ℹ️'
    )
    fs_score_str  = f" · {fs['score_pct']:.0f}%" if (fs['score_pct'] is not None and fs['score_pct'] > 0) else ''
    fs_detail_str = f" · {fs['detail']}" if fs.get('detail') and fs['score_pct'] == 0 else ''
    fs_bucket_str = f" · Evaluated as: {fs['bucket']}" if fs['bucket'] else ''

    # Timeframe navigation state — default Swing
    tf_order = ['Swing', 'Position']
    tf_labels = {'Swing': '🔄 Swing Trade', 'Position': '📈 Position'}
    tf_desc   = {
        'Swing':    '1–8 weeks — trend structure, phase, OBV',
        'Position': '3–12 months — fundamentals, macro, analyst consensus',
    }
    tf_signals_used = {
        'Swing':    'Weinstein Phase · MA stack · OBV · Tailwinds · Volume · News catalyst · RSI',
        'Position': 'Fundamental quality · Analyst target · Tailwinds · Phase · 50/200MA · OBV · Cycle',
    }
    # Reset stale 'Day' value if stored from previous session
    if st.session_state.get('score_timeframe') not in tf_order:
        st.session_state['score_timeframe'] = 'Swing'
    cur_tf   = st.session_state['score_timeframe']
    cur_idx  = tf_order.index(cur_tf)
    tf_score, tf_meaning = tf_scores[cur_tf]
    score_col = "#00FF88" if tf_score >= 7 else "#FACC15" if tf_score >= 4 else "#FF6B6B"

    c1, c2 = st.columns([1.2, 0.8])
    with c1:
        st.markdown(f"""
        <div class="verdict-card" style="background:{vc['bg']};border-left-color:{vc['border']};">
          <div class="verdict-label" style="color:{vc['color']};display:flex;align-items:center;">AI Verdict{mode_badge}</div>
          <div class="verdict-value" style="color:{vc['color']};">{a.get('verdict','')}</div>
          <div class="verdict-meta">Confidence: {a.get('confidence','')} &nbsp;·&nbsp; Risk: {a.get('risk','')}</div>
          <div class="verdict-note" style="color:{vc['color']};">{a.get('risk_reason','')}</div>
          <div style="margin-top:10px;padding-top:8px;border-top:1px solid {vc['border']}33;">
            <span style="font-size:13px;color:{fs['verdict_color']};font-weight:700;">
              {fs_icon} {fs['verdict_text']}{fs_score_str}
            </span>
            <span style="font-size:13px;color:#CBD5E1;">{fs_bucket_str}{fs_detail_str}</span>
          </div>
        </div>""", unsafe_allow_html=True)

        # Timeframe notes — stacked vertically under verdict, fill the left column
        day_note   = a.get('day_trade_note', '')
        swing_note = a.get('swing_note', '')
        inv_note   = a.get('invest_note', '')

        if swing_note:
            st.markdown(
                f'<div class="tf-swing">'
                f'<div class="tf-header-swing">'
                f'<span style="font-size:15px;">🔄</span>'
                f'<span class="tf-label" style="color:#38BDF8;">Swing Trade</span>'
                f'<span style="margin-left:auto;font-size:13px;color:#38BDF888;font-family:monospace;">1–8 weeks</span>'
                f'</div>'
                f'<div class="tf-note">{swing_note}</div>'
                f'</div>',
                unsafe_allow_html=True)
        if inv_note:
            st.markdown(
                f'<div class="tf-inv">'
                f'<div class="tf-header-inv">'
                f'<span style="font-size:15px;">📈</span>'
                f'<span class="tf-label" style="color:#00FF88;">Position</span>'
                f'<span style="margin-left:auto;font-size:13px;color:#00FF8888;font-family:monospace;">3–12 months</span>'
                f'</div>'
                f'<div class="tf-note">{inv_note}</div>'
                f'</div>',
                unsafe_allow_html=True)

        # ── Quick Trade Setup — read-only math card ───────────
        _s1  = float(a.get('support1', 0) or 0)
        _s2  = float(a.get('support2', 0) or 0)
        _r1  = float(a.get('resistance1', 0) or 0)
        _r2  = float(a.get('resistance2', 0) or 0)
        _atr = float(row['ATR'])

        # Entry zone always at or above current price
        if _s1 > 0 and abs(close - _s1) < 0.5 * _atr:
            _entry_low = round(max(_s1 * 1.005, close - 0.1 * _atr), 2)
        else:
            _entry_low = round(close - 0.25 * _atr, 2)
        _entry_low  = max(_entry_low, round(close - 0.5 * _atr, 2))
        _entry_high = round(_entry_low + 0.5 * _atr, 2)
        _entry_mid  = round((_entry_low + _entry_high) / 2, 2)

        # Stop: below support or 1.5×ATR below entry
        _stop = round(_entry_low - 1.5 * _atr, 2)
        if _s1 > 0 and _s1 < _entry_low and _s1 > _stop:
            _stop = round(_s1 - 0.01, 2)
        _stop = max(0.01, _stop)
        _risk = _entry_mid - _stop

        # Target: furthest level that gives ≥2:1, else construct 2:1 minimum
        _min_target = round(_entry_mid + 2 * _risk, 2)
        if _r2 > _entry_mid and (_r2 - _entry_mid) >= 2 * _risk:
            _target = _r2
        elif _r1 > _entry_mid and (_r1 - _entry_mid) >= 2 * _risk:
            _target = _r1
        else:
            _target = _min_target   # construct 2:1 if no level qualifies

        _risk_pct   = round(_risk / _entry_mid * 100, 1) if _entry_mid > 0 else 0
        _reward_pct = round((_target - _entry_mid) / _entry_mid * 100, 1) if _entry_mid > 0 else 0
        _rr         = round((_target - _entry_mid) / _risk, 2) if _risk > 0 else 0

        # Correct professional thresholds
        if _rr >= 3:    _rr_col, _rr_lbl = '#00FF88', 'Excellent'
        elif _rr >= 2:  _rr_col, _rr_lbl = '#00FF88', 'Good — minimum standard'
        elif _rr >= 1.5:_rr_col, _rr_lbl = '#FACC15', 'Below minimum — reconsider'
        else:           _rr_col, _rr_lbl = '#FF6B6B', 'Poor — do not trade'

        _entry_str = f'{cur}{_entry_low:.2f} – {cur}{_entry_high:.2f}'

        st.markdown(f"""
        <div style="background:#070F1A;border:1px solid #1A2A3A;border-radius:8px;
                    padding:12px 14px;margin-top:6px;">
          <div style="font-size:13px;color:#CBD5E1;letter-spacing:2px;text-transform:uppercase;
                      font-weight:700;margin-bottom:10px;">📐 Swing Trade Math
            <span style="color:#CBD5E1;font-size:13px;margin-left:8px;text-transform:none;
                         letter-spacing:0;">· For educational position-sizing reference only</span>
          </div>
          <div style="display:flex;justify-content:space-between;align-items:center;
                      margin-bottom:7px;padding-bottom:7px;border-bottom:1px solid #1A2A3A;">
            <span style="font-size:13px;color:#CBD5E1;font-weight:600;">Entry Zone</span>
            <span style="font-size:13px;font-weight:800;color:#FACC15;
                         font-family:'JetBrains Mono',monospace;">{_entry_str}</span>
          </div>
          <div style="display:flex;justify-content:space-between;align-items:center;
                      margin-bottom:7px;padding-bottom:7px;border-bottom:1px solid #1A2A3A;">
            <span style="font-size:13px;color:#CBD5E1;font-weight:600;">Stop Level</span>
            <span style="font-size:13px;font-weight:800;color:#FF6B6B;
                         font-family:'JetBrains Mono',monospace;">{cur}{_stop:.2f}
              <span style="font-size:13px;font-weight:600;color:#FF6B6B88;"> −{_risk_pct:.1f}%</span>
            </span>
          </div>
          <div style="display:flex;justify-content:space-between;align-items:center;
                      margin-bottom:7px;padding-bottom:7px;border-bottom:1px solid #1A2A3A;">
            <span style="font-size:13px;color:#CBD5E1;font-weight:600;">Target Level</span>
            <span style="font-size:13px;font-weight:800;color:#00FF88;
                         font-family:'JetBrains Mono',monospace;">{cur}{_target:.2f}
              <span style="font-size:13px;font-weight:600;color:#00FF8888;"> +{_reward_pct:.1f}%</span>
            </span>
          </div>
          <div style="display:flex;justify-content:space-between;align-items:center;">
            <span style="font-size:13px;color:#CBD5E1;font-weight:600;">Risk / Reward</span>
            <span style="font-size:14px;font-weight:900;color:{_rr_col};
                         font-family:'JetBrains Mono',monospace;">1 : {_rr}
              <span style="font-size:13px;font-weight:600;color:{_rr_col}88;"> {_rr_lbl}</span>
            </span>
          </div>
        </div>""", unsafe_allow_html=True)
    with c2:
        st.markdown(f"""
        <div class="score-card">
          <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;">
            <div>
              <div style="font-size:13px;color:#CBD5E1;letter-spacing:2px;
                          text-transform:uppercase;font-weight:700;margin-bottom:2px;">Signal Score</div>
              <div style="font-size:13px;color:{score_col};font-weight:800;
                          letter-spacing:0.5px;">{tf_labels[cur_tf]}</div>
            </div>
          </div>
          <div><span class="score-num" style="color:{score_col};">{tf_score}</span><span class="score-denom">/10</span></div>
          <div class="score-bar-wrap">
            <div class="score-bar-track"></div>
            <div class="score-bar-fill" style="width:{tf_score*10}%;"></div>
          </div>
          <div class="score-markers"><span>AVOID</span><span>NEUTRAL</span><span>STRONG</span></div>
          <div style="font-size:13px;color:{score_col};font-weight:700;margin-top:7px;">{tf_meaning}</div>
          <div style="font-size:13px;color:#CBD5E1;font-weight:600;margin-top:4px;line-height:1.5;">{tf_desc[cur_tf]}</div>
          <div style="margin-top:8px;padding-top:6px;border-top:1px solid #243348;">
            <div style="font-size:13px;color:#00FF88;font-weight:700;margin-bottom:4px;line-height:1.6;">&#9650; {bull_names}</div>
            <div style="font-size:13px;color:#FF6B6B;font-weight:700;line-height:1.6;">&#9660; {bear_names}</div>
          </div>
        </div>""", unsafe_allow_html=True)

        # Arrows — wrapped in tf-arrow div for CSS targeting
        st.markdown('<div class="tf-arrow">', unsafe_allow_html=True)
        arr_l, arr_m, arr_r = st.columns([1, 2, 1])
        with arr_l:
            if st.button("◀", key="tf_prev", use_container_width=True):
                st.session_state['score_timeframe'] = tf_order[(cur_idx - 1) % 2]
                st.rerun()
        with arr_m:
            tf_full_names = {'Swing': 'SWING TRADE', 'Position': 'POSITION'}
            st.markdown(
                f'<div style="text-align:center;padding-top:2px;font-size:15px;'
                f'font-weight:900;color:{score_col};letter-spacing:1px;">'
                f'{tf_full_names[cur_tf]}</div>',
                unsafe_allow_html=True)
        with arr_r:
            if st.button("▶", key="tf_next", use_container_width=True):
                st.session_state['score_timeframe'] = tf_order[(cur_idx + 1) % 2]
                st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

        # ── Relative Performance — arrow nav (1W/1M/QTD/YTD/1Y) ──
        sector_etf_sc  = st.session_state.get('sector_etf_run', '')
        sector_name_sc = (
            str(info.get('sector') or '') or
            str(info.get('sectorDisp') or '') or
            str(info.get('industry') or '')
        ).strip()
        # Fallback: re-derive if session state missing
        if not sector_etf_sc and sector_name_sc:
            sector_etf_sc = SECTOR_ETF_MAP.get(sector_name_sc, '')
        # Case-insensitive fallback — catches "semiconductor" vs "Semiconductors"
        if not sector_etf_sc and sector_name_sc:
            for k, v in SECTOR_ETF_MAP.items():
                if k.lower() in sector_name_sc.lower() or sector_name_sc.lower() in k.lower():
                    sector_etf_sc = v
                    break

        # Always show panel — fall back to SPY-only if no sector ETF
        perf_tf_order  = ['1W', '1M', 'QTD', 'YTD', '1Y']
        perf_tf_labels = {'1W':'1 Week', '1M':'1 Month', 'QTD':'This Quarter', 'YTD':'Year to Date', '1Y':'1 Year'}
        if 'perf_timeframe' not in st.session_state:
            st.session_state['perf_timeframe'] = '1M'
        perf_tf   = st.session_state['perf_timeframe']
        perf_idx  = perf_tf_order.index(perf_tf)

        comp_data   = st.session_state.get('comp_data', {'spy': {}, 'sec': {}})
        stk_returns = st.session_state.get('stk_returns', {})
        if not comp_data.get('spy'):
            _fetch_etf = sector_etf_sc if sector_etf_sc else 'SPY'
            comp_data  = fetch_comparison_data(_fetch_etf)
        spy_ret = float(comp_data.get('spy', {}).get(perf_tf, 0) or 0)
        sec_ret = float(comp_data.get('sec', {}).get(perf_tf, 0) or 0)
        stk_ret = float(stk_returns.get(perf_tf, 0) or 0)
        if True:  # always render

            def _pc(v): return '#00FF88' if v > 0 else '#FF6B6B' if v < 0 else '#FACC15'
            def _pf(v): return f'{v:+.1f}%'

            # Relative read vs SPY
            if stk_ret > spy_ret + 2:
                rel_msg_sc = f'{ticker} outperforming SPY — relative strength'
                rel_col_sc = '#00FF88'
            elif stk_ret < spy_ret - 2:
                rel_msg_sc = f'{ticker} lagging SPY — relative weakness'
                rel_col_sc = '#FF6B6B'
            else:
                rel_msg_sc = f'{ticker} tracking market — in line with SPY'
                rel_col_sc = '#FACC15'

            # Render panel
            st.markdown(f"""
            <div style="background:#070F1A;border:1px solid #1A2A3A;border-radius:8px;
                        padding:10px 14px;margin-top:8px;">
              <div style="font-size:13px;color:#CBD5E1;letter-spacing:2px;text-transform:uppercase;
                          font-weight:700;margin-bottom:8px;">Relative Performance</div>
              <div style="display:flex;justify-content:space-between;align-items:center;
                          margin-bottom:5px;padding-bottom:5px;border-bottom:1px solid #1A2A3A;">
                <span style="font-size:13px;color:#CBD5E1;font-weight:600;">SPY · S&P 500</span>
                <span style="font-size:13px;font-weight:800;color:{_pc(spy_ret)};
                             font-family:'JetBrains Mono',monospace;">{_pf(spy_ret)}</span>
              </div>
              <div style="display:flex;justify-content:space-between;align-items:center;
                          margin-bottom:5px;padding-bottom:5px;border-bottom:1px solid #1A2A3A;">
                <span style="font-size:13px;color:#CBD5E1;font-weight:600;">{sector_etf_sc + ' · Sector ETF' if sector_etf_sc else 'Sector ETF · N/A'}</span>
                <span style="font-size:13px;font-weight:800;color:{_pc(sec_ret)};
                             font-family:'JetBrains Mono',monospace;">{_pf(sec_ret) if sector_etf_sc else '—'}</span>
              </div>
              <div style="display:flex;justify-content:space-between;align-items:center;">
                <span style="font-size:13px;color:#CBD5E1;font-weight:600;">{ticker} · Stock</span>
                <span style="font-size:13px;font-weight:800;color:{_pc(stk_ret)};
                             font-family:'JetBrains Mono',monospace;">{_pf(stk_ret)}</span>
              </div>
              <div style="margin-top:8px;padding-top:6px;border-top:1px solid #1A2A3A;
                          font-size:13px;color:{rel_col_sc};font-weight:600;">{rel_msg_sc}</div>
            </div>""", unsafe_allow_html=True)

            # Arrow nav — same style as signal score arrows
            st.markdown('<div class="tf-arrow">', unsafe_allow_html=True)
            pa1, pa2, pa3 = st.columns([1, 2, 1])
            with pa1:
                if st.button("◀", key="perf_prev", use_container_width=True):
                    st.session_state['perf_timeframe'] = perf_tf_order[(perf_idx - 1) % 5]
                    st.rerun()
            with pa2:
                st.markdown(
                    f'<div style="text-align:center;padding-top:2px;font-size:13px;'
                    f'font-weight:800;color:#5EEAD4;letter-spacing:1px;">'
                    f'{perf_tf_labels[perf_tf]}</div>',
                    unsafe_allow_html=True)
            with pa3:
                if st.button("▶", key="perf_next", use_container_width=True):
                    st.session_state['perf_timeframe'] = perf_tf_order[(perf_idx + 1) % 5]
                    st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)

    # ── AI Summary — 3 paragraph layout ─────────────────────
    def render_summary_para(label, icon, text, sentiment):
        """Render one summary paragraph — Option C: colored gradient header strip, neutral dark body."""
        if not text:
            return
        s = str(sentiment).lower().strip()
        if s == 'bullish':
            accent     = '#00FF88'
            grad_start = '#052A14'
            grad_end   = '#0A1525'
            dot_char   = '▲'
            glow       = 'rgba(0,255,136,0.08)'
        elif s == 'bearish':
            accent     = '#FF6B6B'
            grad_start = '#2D1015'
            grad_end   = '#1A1525'
            dot_char   = '▼'
            glow       = 'rgba(255,107,107,0.08)'
        else:
            accent     = '#FACC15'
            grad_start = '#251800'
            grad_end   = '#141525'
            dot_char   = '◆'
            glow       = 'rgba(250,204,21,0.08)'

        st.markdown(f"""
        <div style="background:#111827;border:1px solid {accent}33;border-radius:10px;
                    margin-bottom:8px;overflow:hidden;box-shadow:0 2px 16px {glow};">
          <div style="background:linear-gradient(135deg,{grad_start} 0%,{grad_end} 100%);
                      padding:10px 16px;display:flex;align-items:center;gap:10px;
                      border-bottom:1px solid {accent}33;">
            <span style="font-size:14px;">{icon}</span>
            <span style="font-size:13px;color:{accent};letter-spacing:2px;
                         text-transform:uppercase;font-weight:800;">{label}</span>
            <span style="margin-left:auto;font-size:13px;font-weight:800;
                         color:{accent};">{dot_char}</span>
          </div>
          <div style="padding:14px 16px;">
            <div style="font-size:13px;color:#E2E8F0;line-height:1.8;">{text}</div>
          </div>
        </div>""", unsafe_allow_html=True)

    s_tech       = a.get('summary_technical', '')
    s_tech_sent  = a.get('summary_technical_sentiment', 'mixed')
    s_lvl        = a.get('summary_levels', '')
    s_lvl_sent   = a.get('summary_levels_sentiment', 'mixed')
    s_fund       = a.get('summary_fundamental', '')
    s_fund_sent  = a.get('summary_fundamental_sentiment', 'mixed')
    s_macro      = a.get('summary_macro', '')
    s_macro_sent = a.get('summary_macro_sentiment', 'mixed')
    s_fall       = a.get('summary', '')

    has_structured = bool(s_tech or s_lvl or s_fund or s_macro)

    st.markdown("""
    <div style="font-size:13px;color:#CBD5E1;letter-spacing:2px;text-transform:uppercase;
                font-weight:600;margin:10px 0 6px;">AI SUMMARY</div>
    """, unsafe_allow_html=True)

    if has_structured:
        render_summary_para("Key Levels",          "🎯", s_lvl,   s_lvl_sent)
        render_summary_para("Technical Structure", "📊", s_tech,  s_tech_sent)
        render_summary_para("Fundamental Quality", "📈", s_fund,  s_fund_sent)
        render_summary_para("Macro & News Events", "🌍", s_macro, s_macro_sent)

    else:
        st.markdown(f"""
        <div style="background:#1A2232;border:1px solid #5EEAD4;border-top:2px solid #5EEAD4;
                    border-radius:8px;padding:14px 18px;">
          <div style="font-size:13px;color:#E2E8F0;line-height:1.8;">{s_fall}</div>
        </div>""", unsafe_allow_html=True)

    vwap   = float(a.get('vwap', close))
    ema100 = float(a.get('ema100', float(row['MA100'])))
    fib382, fib500, fib618 = fibs

    c1, c2 = st.columns(2)
    with c1:
        st.markdown('<div class="section-header">KEY LEVELS & TECHNICAL INDICATORS</div>', unsafe_allow_html=True)
        levels_html = '<div class="panel-body">'
        atr_dollar = float(row['ATR'])
        atr_low    = round(close - atr_dollar, 2)
        atr_high   = round(close + atr_dollar, 2)
        levels_html += data_row("Entry zone", f"{cur}{a.get('entry_low',0):.2f} – {cur}{a.get('entry_high',0):.2f}", "val-y")
        levels_html += data_row("ATR (14)", f"{cur}{atr_dollar:.2f}  →  range {cur}{atr_low:.2f} – {cur}{atr_high:.2f}", "val-b", True)
        levels_html += data_row("VWAP",    f"{cur}{vwap:.2f}",   "val-g" if close > vwap  else "val-r")
        levels_html += data_row("100 EMA", f"{cur}{ema100:.2f}", "val-g" if close > ema100 else "val-r")
        levels_html += data_row("38.2% Fib", f"{cur}{fib382:.2f}", "val-m", show_info=True)
        levels_html += data_row("50.0% Fib", f"{cur}{fib500:.2f}", "val-m", show_info=True)
        levels_html += data_row("61.8% Fib", f"{cur}{fib618:.2f}", "val-m", show_info=True)
        levels_html += data_row(a.get('support1_label','Support 1'),      f"{cur}{a.get('support1',0):.2f}",    "val-g")
        levels_html += data_row(a.get('resistance1_label','Resistance 1'), f"{cur}{a.get('resistance1',0):.2f}", "val-r")
        levels_html += data_row(a.get('support2_label','Support 2'),      f"{cur}{a.get('support2',0):.2f}",    "val-g")
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
            '<span style="font-size:13px;color:#CBD5E1;">0</span>'
            '<div style="flex:1;position:relative;height:6px;background:#243348;border-radius:3px;">'
            '<div style="position:absolute;left:0;top:0;width:100%;height:6px;border-radius:3px;background:linear-gradient(90deg,#00FF88 0%,#FACC15 30%,#FF6B6B 70%,#FF6B6B 100%);"></div>'
            f'<div style="position:absolute;left:{min(max(rsi_pct,2),98)}%;top:-4px;width:12px;height:12px;background:#F1F5F9;border-radius:50%;transform:translateX(-50%);border:2px solid #111827;"></div>'
            '</div>'
            '<span style="font-size:13px;color:#CBD5E1;">100</span>'
            '</div>'
            f'<div style="text-align:center;font-size:13px;color:#CBD5E1;">RSI {rsi_val:.1f} · Oversold &lt;30 · Overbought &gt;70</div>'
            '</div>'
        )
        levels_html += '</div>'
        st.markdown(levels_html, unsafe_allow_html=True)

        # ── Analyst Ratings — in left column below RSI ─────────
        buy_l   = analyst_data.get('buy', 0)
        hold_l  = analyst_data.get('hold', 0)
        sell_l  = analyst_data.get('sell', 0)
        tot_l   = buy_l + hold_l + sell_l
        tgt_l   = analyst_data.get('target', 0)
        tgt_lo  = analyst_data.get('target_low', 0)
        tgt_hi  = analyst_data.get('target_high', 0)
        rk_l    = analyst_data.get('rec_key', 'N/A').replace('-',' ').replace('_',' ').title()
        na_l    = analyst_data.get('num_analysts', 0)
        ups_l   = ((tgt_l / close) - 1) * 100 if tgt_l > 0 and close > 0 else 0
        up_cl   = "#00FF88" if ups_l > 10 else "#FACC15" if ups_l > 0 else "#FF6B6B"
        cn_cl   = "#00FF88" if 'Buy' in rk_l or 'Strong' in rk_l else "#FF6B6B" if 'Sell' in rk_l else "#FACC15"
        st.markdown('<div style="font-size:13px;color:#CBD5E1;letter-spacing:2px;text-transform:uppercase;font-weight:700;margin:12px 0 6px;padding-left:2px;">Analyst Ratings</div>', unsafe_allow_html=True)
        al1, al2, al3, al4 = st.columns(4)
        for acol, lbl, val, col in [
            (al1, "Consensus",    rk_l if rk_l != 'N/A' else "N/A", cn_cl),
            (al2, "Price Target", f"{cur}{tgt_l:.2f}" if tgt_l else "N/A", up_cl),
            (al3, "Upside",       f"{ups_l:+.1f}%" if tgt_l else "N/A", up_cl),
            (al4, "# Analysts",   str(na_l) if na_l else "N/A", "#94A3B8"),
        ]:
            with acol:
                st.markdown(f'<div class="earn-bar" style="border-left-color:{col};"><div class="earn-label">{lbl}</div><div class="earn-val" style="color:{col};">{val}</div></div>', unsafe_allow_html=True)
        if tot_l > 0:
            bp = int(buy_l  / tot_l * 100)
            hp = int(hold_l / tot_l * 100)
            sp = 100 - bp - hp
            st.markdown(f'''<div style="background:#1A2232;border:1px solid #243348;
                border-radius:0 0 8px 8px;padding:10px 16px;">
              <div style="display:flex;justify-content:space-between;font-size:13px;margin-bottom:6px;">
                <span style="color:#00FF88;font-weight:700;">Buy {buy_l} ({bp}%)</span>
                <span style="color:#FACC15;font-weight:700;">Hold {hold_l} ({hp}%)</span>
                <span style="color:#FF6B6B;font-weight:700;">Sell {sell_l} ({sp}%)</span>
              </div>
              <div style="display:flex;height:8px;border-radius:4px;overflow:hidden;">
                <div style="width:{bp}%;background:#00FF88;"></div>
                <div style="width:{hp}%;background:#FACC15;"></div>
                <div style="width:{sp}%;background:#FF6B6B;"></div>
              </div>
            </div>''', unsafe_allow_html=True)

    with c2:
        st.markdown('<div class="section-header">FUNDAMENTALS & GROWTH</div>', unsafe_allow_html=True)
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
        pe     = float(info.get('trailingPE') or 0)
        fwd_pe = float(info.get('forwardPE')  or 0)
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
        quick_ratio    = float(_get(['quickRatio'], 0) or 0)
        debt_to_assets = float(_get(['debtToAssets'], 0) or 0)
        int_coverage   = float(_get(['interestCoverage'], 0) or 0)
        asset_turn     = float(_get(['assetTurnover'], 0) or 0)
        inv_turn       = float(_get(['inventoryTurnover'], 0) or 0)
        div_yield  = float(_get(['dividendYield', 'trailingAnnualDividendYield'], 0) or 0) * (100 if float(_get(['dividendYield'], 0) or 0) < 1 else 1)
        short_pct  = float(_get(['shortPercentOfFloat', 'shortRatio'], 0) or 0) * (100 if float(_get(['shortPercentOfFloat'], 0) or 0) < 1 else 1)
        float_sh   = float(_get(['floatShares', 'sharesOutstanding'], 0) or 0)
        if mc == 0:
            shares = float(_get(['sharesOutstanding','impliedSharesOutstanding'], 0) or 0)
            if shares > 0: mc = shares * close
        funds_html = '<div class="panel-body">'
        funds_html += data_row("Market Cap",       fmt_cap(mc) if mc else "—",              "val-w",  True)
        funds_html += data_row("P/E (Trailing)",   f"{pe:.1f}" if pe else "—",              "val-r" if pe > 40 else "val-y" if pe > 20 else "val-g" if pe else "val-m", True)
        funds_html += data_row("P/E (Forward)",    f"{fwd_pe:.1f}" if fwd_pe else "—",      "val-r" if fwd_pe > 35 else "val-y" if fwd_pe > 18 else "val-g" if fwd_pe else "val-m", True)
        funds_html += data_row("P/B Ratio",        f"{pb:.1f}" if pb else "—",              "val-r" if pb > 5 else "val-g" if pb else "val-m", True)
        funds_html += data_row("PEG Ratio",        f"{peg:.2f}" if peg else "—",            "val-r" if peg > 3 else "val-y" if peg > 1.5 else "val-g" if peg else "val-m", True)
        funds_html += data_row("EPS Growth YoY",   f"{eps_g:+.1f}%" if eps_g else "—",     "val-g" if eps_g > 0 else "val-r", True)
        funds_html += data_row("Rev Growth YoY",   f"{rev_g:+.1f}%" if rev_g else "—",     "val-g" if rev_g > 0 else "val-r", True)
        funds_html += data_row("Operating Margin", f"{op_margin:.1f}%" if op_margin else "—", "val-g" if op_margin > 15 else "val-y" if op_margin > 0 else "val-r", True)
        funds_html += data_row("Profit Margin",    f"{profit_m:.1f}%" if profit_m else "—",   "val-g" if profit_m > 10 else "val-y" if profit_m > 0 else "val-r", True)
        funds_html += data_row("Return on Equity", f"{roe:.1f}%" if roe else "—",             "val-g" if roe > 15 else "val-y" if roe > 0 else "val-r", True)
        funds_html += data_row("Debt / Equity",    f"{debt_eq:.2f}" if debt_eq else "—",      "val-r" if debt_eq > 2 else "val-y" if debt_eq > 1 else "val-g", True)
        funds_html += data_row("Debt-to-Assets",   f"{debt_to_assets:.2f}" if debt_to_assets else "—", "val-r" if debt_to_assets > 0.6 else "val-y" if debt_to_assets > 0.4 else "val-g" if debt_to_assets else "val-m", True)
        funds_html += data_row("Interest Coverage",f"{int_coverage:.1f}x" if int_coverage else "—",    "val-r" if 0 < int_coverage < 1.5 else "val-y" if 0 < int_coverage < 3 else "val-g" if int_coverage >= 3 else "val-m", True)
        funds_html += data_row("Current Ratio",    f"{curr_ratio:.2f}" if curr_ratio else "—",  "val-g" if curr_ratio > 1.5 else "val-y" if curr_ratio > 1 else "val-r", True)
        funds_html += data_row("Quick Ratio",      f"{quick_ratio:.2f}" if quick_ratio else "—","val-g" if quick_ratio > 1.0 else "val-y" if quick_ratio > 0.7 else "val-r" if quick_ratio else "val-m", True)
        funds_html += data_row("Asset Turnover",   f"{asset_turn:.2f}x" if asset_turn else "—", "val-g" if asset_turn > 1.0 else "val-y" if asset_turn > 0.5 else "val-m" if asset_turn else "val-m", True)
        funds_html += data_row("Inventory Turnover",f"{inv_turn:.1f}x" if inv_turn else "—",    "val-g" if inv_turn > 6 else "val-y" if inv_turn > 3 else "val-m" if inv_turn else "val-m", True)
        funds_html += data_row("Dividend Yield",   f"{div_yield:.2f}%" if div_yield else "None", "val-g" if div_yield > 2 else "val-m", True)
        funds_html += data_row("Short % Float",    f"{short_pct:.1f}%" if short_pct else "—",   "val-r" if short_pct > 20 else "val-y" if short_pct > 10 else "val-g", True)
        funds_html += data_row("Float Shares",     fmt_cap(float_sh).replace("$","") if float_sh else "—", "val-m", True)
        funds_html += '</div>'
        st.markdown(funds_html, unsafe_allow_html=True)

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

    # ── WEINSTEIN PHASE + THREE TAILWINDS ────────────────────
    ph_num, ph_label, ph_sub, ph_col, ph_conf, ph_conf_text, ph_desc = phase_result
    sector_name = (
        str(info.get('sector','') or '') or
        str(info.get('sectorDisp','') or '') or
        str(info.get('industry','') or '')
    ).strip()
    sector_etf = SECTOR_ETF_MAP.get(sector_name, '')
    if not sector_etf and sector_name:
        for k, v in SECTOR_ETF_MAP.items():
            if k.lower() in sector_name.lower() or sector_name.lower() in k.lower():
                sector_etf = v
                break

    mkt_phase = mkt_phase_hud   # already read from session state above
    sec_phase = sec_phase_hud   # already read from session state above

    tw_market = 1 if mkt_phase[0] == 2 else 0
    tw_sector = 1 if sec_phase[0] == 2 else 0
    tw_stock  = 1 if ph_num == 2 else 0
    tw_score  = tw_market + tw_sector + tw_stock
    tw_col    = "#00FF88" if tw_score == 3 else "#FACC15" if tw_score == 2 else "#F97316" if tw_score == 1 else "#FF6B6B"
    tw_label  = {3:"All tailwinds aligned ✓", 2:"2 of 3 aligned", 1:"1 of 3 aligned", 0:"No tailwinds"}[tw_score]

    phase_conf_colors = ["#94A3B8","#F97316","#FACC15","#00FF88"]
    ph_conf_col = phase_conf_colors[min(ph_conf, 3)]

    st.markdown(f'''
    <div style="background:#0D1525;border:1px solid #1E2D42;border-radius:10px;
                padding:14px 18px;margin:8px 0;display:grid;
                grid-template-columns:1fr 1fr 1fr 1fr;gap:12px;">
      <div style="border-right:1px solid #1E2D42;padding-right:12px;border-top:2px solid {mkt_phase[3]};padding-top:10px;">
        <div style="font-size:13px;color:#CBD5E1;letter-spacing:2px;text-transform:uppercase;
                    font-family:'JetBrains Mono',monospace;margin-bottom:6px;">MARKET · SPY</div>
        <div style="font-size:16px;font-weight:800;color:{mkt_phase[3]};font-family:'JetBrains Mono',monospace;">{mkt_phase[1]}</div>
        <div style="font-size:13px;color:{mkt_phase[3]};margin-top:2px;">{mkt_phase[2]}</div>
        <div style="font-size:13px;color:#CBD5E1;margin-top:4px;line-height:1.4;">{mkt_phase[6][:55]}</div>
      </div>
      <div style="border-right:1px solid #1E2D42;padding-right:12px;border-top:2px solid {sec_phase[3]};padding-top:10px;">
        <div style="font-size:13px;color:#CBD5E1;letter-spacing:2px;text-transform:uppercase;
                    font-family:'JetBrains Mono',monospace;margin-bottom:6px;">SECTOR · {sector_etf or "N/A"}</div>
        <div style="font-size:16px;font-weight:800;color:{sec_phase[3]};font-family:'JetBrains Mono',monospace;">{sec_phase[1]}</div>
        <div style="font-size:13px;color:{sec_phase[3]};margin-top:2px;">{sec_phase[2]}</div>
        <div style="font-size:13px;color:#CBD5E1;margin-top:4px;line-height:1.4;">{sec_phase[6][:55]}</div>
      </div>
      <div style="border-right:1px solid #1E2D42;padding-right:12px;border-top:2px solid {ph_col};padding-top:10px;">
        <div style="font-size:13px;color:#CBD5E1;letter-spacing:2px;text-transform:uppercase;
                    font-family:'JetBrains Mono',monospace;margin-bottom:6px;">STOCK · {ticker}</div>
        <div style="font-size:16px;font-weight:800;color:{ph_col};font-family:'JetBrains Mono',monospace;">{ph_label}</div>
        <div style="font-size:13px;color:{ph_col};margin-top:2px;">{ph_sub}</div>
        <div style="font-size:13px;color:{ph_conf_col};margin-top:4px;">{ph_conf_text}</div>
        <div style="font-size:13px;color:#CBD5E1;margin-top:2px;line-height:1.4;">{ph_desc[:55]}</div>
      </div>
      <div style="border-top:2px solid {tw_col};padding-top:10px;">
        <div style="font-size:13px;color:#CBD5E1;letter-spacing:2px;text-transform:uppercase;
                    font-family:'JetBrains Mono',monospace;margin-bottom:6px;">THREE TAILWINDS</div>
        <div style="font-size:36px;font-weight:800;color:{tw_col};font-family:'JetBrains Mono',monospace;line-height:1;">
          {tw_score}<span style="font-size:18px;color:#4A6080;">/3</span></div>
        <div style="font-size:13px;color:{tw_col};margin-top:4px;font-weight:700;">{tw_label}</div>
        <div style="display:flex;gap:4px;margin-top:8px;">
          <div style="width:28px;height:6px;border-radius:3px;background:{"#00FF88" if tw_market else "#243348"};"></div>
          <div style="width:28px;height:6px;border-radius:3px;background:{"#00FF88" if tw_sector else "#243348"};"></div>
          <div style="width:28px;height:6px;border-radius:3px;background:{"#00FF88" if tw_stock else "#243348"};"></div>
        </div>
        <div style="font-size:13px;color:#CBD5E1;margin-top:4px;">Market · Sector · Stock</div>
      </div>
    </div>''', unsafe_allow_html=True)

    sig_keys = ['MA20','MA50','MA200','RSI','MACD','OBV','Vol','ATR']
    cols = st.columns(8)
    for i, k in enumerate(sig_keys):
        s = signals[k]
        with cols[i]:
            st.markdown(sig_html(s['label'], s['val'], s['bull'], s.get('neut', False), s.get('subtitle', '')), unsafe_allow_html=True)

    # ── LIVE CHART ────────────────────────────────────────────
    st.markdown('<div class="section-header" style="margin-top:8px;">LIVE CHART · DAILY CANDLES · 1 YEAR</div>', unsafe_allow_html=True)
    chart_df = df.tail(252).copy()
    st.plotly_chart(build_chart(chart_df, ticker), use_container_width=True, config={'displayModeBar': True})

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
    iv_label  = ("No options data" if iv == 0 else
                 "IV > HV — big move expected" if iv_vs_hv > 1.3 else
                 "IV < HV — calm expected" if iv_vs_hv < 0.7 else
                 "IV ≈ HV — normal")
    st.markdown('<div class="section-header">VOLATILITY ANALYSIS</div>', unsafe_allow_html=True)
    vc1, vc2, vc3 = st.columns(3)
    with vc1:
        st.markdown('<div class="vol-panel"><div class="data-header">Historical Volatility</div>', unsafe_allow_html=True)
        hv_rows  = f'<div class="vol-row"><span class="vol-lbl">Volatility 30d</span><span style="color:{hv_col};font-weight:700;font-family:monospace;">{hv_30:.1f}%</span></div>'
        hv_rows += f'<div class="vol-row"><span class="vol-lbl">Volatility 90d</span><span style="color:{hv_col};font-weight:700;font-family:monospace;">{hv_90:.1f}%</span></div>'
        hv_rows += f'<div class="vol-row"><span class="vol-lbl">ATR (14) $</span><span style="color:#38BDF8;font-weight:700;font-family:monospace;">{cur}{float(row["ATR"]):.2f}</span></div>'
        hv_rows += f'<div class="vol-row"><span class="vol-lbl">ATR % price</span><span style="color:#38BDF8;font-weight:700;font-family:monospace;">{float(row["ATRPct"])*100:.1f}%</span></div>'
        st.markdown(hv_rows + '</div>', unsafe_allow_html=True)
    with vc2:
        st.markdown('<div class="vol-panel"><div class="data-header">Bollinger Bands (20,2)</div>', unsafe_allow_html=True)
        bb_rows  = f'<div class="vol-row"><span class="vol-lbl">Upper Band</span><span style="color:#FF6B6B;font-weight:700;font-family:monospace;">{cur}{bb_upper:.2f}</span></div>'
        bb_rows += f'<div class="vol-row"><span class="vol-lbl">Middle (20MA)</span><span style="color:#94A3B8;font-weight:700;font-family:monospace;">{cur}{bb_mid:.2f}</span></div>'
        bb_rows += f'<div class="vol-row"><span class="vol-lbl">Lower Band</span><span style="color:#00FF88;font-weight:700;font-family:monospace;">{cur}{bb_lower:.2f}</span></div>'
        bb_rows += f'<div class="vol-row"><span class="vol-lbl">BB Width</span><span style="color:#A78BFA;font-weight:700;font-family:monospace;">{vol_data.get("bb_width",0):.1f}%</span></div>'
        bb_rows += (
            '<div class="vol-row" style="flex-direction:column;gap:6px;">'
            f'<div style="display:flex;justify-content:space-between;width:100%;font-size:13px;">'
            f'<span class="vol-lbl">Price in Band</span>'
            f'<span style="color:{bb_col};font-weight:700;font-family:monospace;">{bb_pct:.0f}% — {"Oversold" if bb_pct < 20 else "Overbought" if bb_pct > 80 else "Neutral"}</span>'
            '</div>'
            '<div style="display:flex;align-items:center;gap:8px;width:100%;">'
            f'<span style="font-size:13px;color:#CBD5E1;">{cur}{bb_lower:.0f}</span>'
            '<div style="flex:1;position:relative;height:6px;background:#243348;border-radius:3px;">'
            '<div style="position:absolute;left:0;top:0;width:100%;height:6px;border-radius:3px;background:linear-gradient(90deg,#00FF88,#FACC15,#FF6B6B);"></div>'
            f'<div style="position:absolute;left:{min(max(int(bb_pct),2),98)}%;top:-4px;width:12px;height:12px;background:#F1F5F9;border-radius:50%;transform:translateX(-50%);border:2px solid #111827;"></div>'
            '</div>'
            f'<span style="font-size:13px;color:#CBD5E1;">{cur}{bb_upper:.0f}</span>'
            '</div>'
            f'<div style="text-align:center;font-size:13px;color:#CBD5E1;">{cur}{cur_close:.2f} · Mid {cur}{bb_mid:.2f}</div>'
            '</div>'
        )
        st.markdown(bb_rows + '</div>', unsafe_allow_html=True)
    with vc3:
        st.markdown('<div class="vol-panel"><div class="data-header">Implied Volatility</div>', unsafe_allow_html=True)
        iv_rows  = f'<div class="vol-row"><span class="vol-lbl">IV</span><span style="color:{iv_col};font-weight:700;font-family:monospace;">{iv:.1f}%</span></div>'
        iv_rows += f'<div class="vol-row"><span class="vol-lbl">IV vs HV 30d</span><span style="color:{"#FF6B6B" if iv_vs_hv > 1.3 else "#00FF88"};font-weight:700;font-family:monospace;">{iv_vs_hv:.2f}x</span></div>'
        iv_rows += f'<div class="vol-row" style="flex-direction:column;"><span class="vol-lbl" style="margin-bottom:4px;">Signal</span><span style="color:{"#FF6B6B" if iv_vs_hv > 1.3 else "#00FF88" if iv > 0 else "#94A3B8"};font-size:13px;">{iv_label}</span></div>'
        iv_rows += f'<div class="vol-row"><span class="vol-lbl">Day range est.</span><span style="color:#38BDF8;font-family:monospace;">{cur}{cur_close - float(row["ATR"]):.2f} – {cur}{cur_close + float(row["ATR"]):.2f}</span></div>'
        st.markdown(iv_rows + '</div>', unsafe_allow_html=True)

    # ── R/R CALCULATOR — tabs removed ────────────────────────
    st.markdown('<div class="section-header" style="margin-top:8px;">⚡ RISK / REWARD CALCULATOR</div>', unsafe_allow_html=True)


    atr_val   = float(row['ATR'])
    verdict   = a.get('verdict', 'TECHNICAL SETUP')
    s1        = float(a.get('support1', 0) or 0)
    s2        = float(a.get('support2', 0) or 0)
    r1        = float(a.get('resistance1', 0) or 0)
    r2        = float(a.get('resistance2', 0) or 0)
    ma200     = float(row.get('MA200', close))
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

    vc_mode       = VERDICT_COLORS.get(verdict, VERDICT_COLORS['TECHNICAL SETUP'])
    ai_label_html = f'<span style="background:{vc_mode["bg"]};border:1px solid {vc_mode["border"]};border-radius:4px;padding:2px 8px;font-size:13px;color:{vc_mode["color"]};font-weight:700;letter-spacing:1px;">AI: {verdict}</span>'
    cur_badge     = f'<span style="background:#1C2A3A;border:1px solid #38BDF8;border-radius:4px;padding:2px 8px;font-size:13px;color:#CBD5E1;font-weight:700;letter-spacing:1px;margin-left:6px;">💱 {cur_code}</span>'

    st.markdown(f'''
    <div style="background:#1A2232;border:1px solid #243348;border-radius:0 0 8px 8px;padding:12px 16px 10px;">
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <div style="font-size:13px;color:#CBD5E1;">Pre-filled from AI analysis · Adjust any value to recalculate</div>
        <div>{ai_label_html}{cur_badge}</div>
      </div>
    </div>''', unsafe_allow_html=True)

    stop_preset, target_preset = calc_presets("Swing Trade")

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
        stop_price = st.number_input(f"Stop Loss ({cur})", min_value=0.01, value=float(derived_stop), step=0.01, key=f"rr_stop_{round(derived_stop,2)}", format="%.2f")
    with rr_c5:
        target_price = st.number_input(f"Target ({cur})", min_value=0.01, value=float(target_preset), step=0.01, key="rr_target", format="%.2f")

    risk_per_share   = round(abs(entry_price - stop_price), 2)
    reward_per_share = round(abs(target_price - entry_price), 2)
    rr_ratio         = round(reward_per_share / risk_per_share, 2) if risk_per_share > 0 else 0
    position_size    = shares_derived
    actual_loss      = round(position_size * risk_per_share, 2)
    actual_gain      = round(position_size * reward_per_share, 2)
    loss_pct         = round((actual_loss / position_size_input) * 100, 1) if position_size_input > 0 else 0
    stop_pct         = round((risk_per_share / entry_price) * 100, 2) if entry_price > 0 else 0
    target_pct       = round((reward_per_share / entry_price) * 100, 2) if entry_price > 0 else 0
    rr_col   = '#00FF88' if rr_ratio >= 2 else '#FACC15' if rr_ratio >= 1.5 else '#FF6B6B'
    rr_label = 'Excellent' if rr_ratio >= 3 else 'Good — minimum standard' if rr_ratio >= 2 else 'Below minimum — reconsider' if rr_ratio >= 1.5 else 'Poor — do not trade'

    rc1, rc2, rc3 = st.columns(3)
    with rc1:
        st.markdown(f'''<div class="earn-bar" style="border-left-color:{rr_col};margin-top:6px;">
          <div class="earn-label">Risk/Reward Ratio</div>
          <div class="earn-val" style="color:{rr_col};font-size:26px;letter-spacing:1px;">1 : {rr_ratio}</div>
          <div style="font-size:13px;color:{rr_col};margin-top:3px;font-weight:700;">{rr_label}</div>
        </div>''', unsafe_allow_html=True)
    with rc2:
        st.markdown(f'''<div class="earn-bar" style="border-left-color:#38BDF8;margin-top:6px;">
          <div class="earn-label">Shares to Buy</div>
          <div class="earn-val" style="color:#38BDF8;font-size:22px;">{position_size:,} <span style="font-size:13px;">shares</span></div>
          <div style="font-size:13px;color:#CBD5E1;margin-top:3px;">{cur}{position_size_input:,.0f} position · {position_size:,} × {cur}{entry_price:.2f}</div>
        </div>''', unsafe_allow_html=True)
    with rc3:
        st.markdown(f'''<div class="earn-bar" style="border-left-color:#38BDF8;margin-top:6px;">
          <div class="earn-label">Worst Case / Best Case</div>
          <div class="earn-val" style="color:#FF6B6B;font-size:18px;">−{cur}{actual_loss:,.0f} <span style="font-size:13px;color:#FF6B6B88;">({loss_pct:.1f}% of position)</span></div>
          <div style="font-size:16px;color:#00FF88;font-weight:700;font-family:monospace;margin-top:4px;">+{cur}{actual_gain:,.0f} <span style="font-size:13px;color:#00FF8888;">if target hit</span></div>
        </div>''', unsafe_allow_html=True)

    try:
        all_prices  = sorted([stop_price, entry_price, target_price])
        price_min   = all_prices[0]
        price_max   = all_prices[-1]
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
                font-size:13px;font-family:'JetBrains Mono',monospace;align-items:center;">
      <span style="color:#64748B;">Position <span style="color:#38BDF8;">{cur}{position_size_input:,.0f}</span></span>
      <span style="color:#64748B;">Entry <span style="color:#FACC15;">{cur}{entry_price:.2f}</span></span>
      <span style="color:#64748B;">Stop <span style="color:#FF6B6B;">{cur}{stop_price:.2f} (−{stop_pct:.1f}%)</span></span>
      <span style="color:#64748B;">Target <span style="color:#00FF88;">{cur}{target_price:.2f} (+{target_pct:.1f}%)</span></span>
      <span style="color:#64748B;">Max loss <span style="color:#FF6B6B;">{cur}{actual_loss:,.0f} ({loss_pct:.1f}% of position)</span></span>
    </div>''', unsafe_allow_html=True)

    st.markdown(
        '<div style="text-align:center;font-size:13px;color:#4A6080;padding:6px 0 2px;">'
        '⚠️ Educational position-sizing reference only — not financial advice</div>',
        unsafe_allow_html=True)

    # ── EARNINGS ──────────────────────────────────────────────
    beat_str = a.get('last_earnings_beat', 'Unknown') or 'Unknown'
    if earnings_hist:
        last_e   = earnings_hist[-1]
        s        = last_e.get('surprise', 0) or 0
        beat_str = f"Beat +{s:.1f}%" if s > 0 else f"Missed {s:.1f}%"
    earn_days = days_to_earn
    earn_col  = "#FF6B6B" if 0 < earn_days < 14 else "#FACC15" if 0 < earn_days < 30 else "#94A3B8"
    beat_col  = "#00FF88" if "Beat" in beat_str else "#FF6B6B" if "Miss" in beat_str else "#FACC15"
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
    st.markdown('<div class="section-header">EARNINGS HISTORY — LAST 4 QUARTERS</div>', unsafe_allow_html=True)
    if not earnings_hist:
        st.markdown('<div class="panel-body"><div style="padding:12px 14px;font-size:13px;color:#CBD5E1;">No earnings history available</div></div>', unsafe_allow_html=True)
    else:
        eh_html = '<div style="background:#1A2232;border:1px solid #243348;border-radius:0 0 8px 8px;">'
        eh_html += '<div class="earn-hist-row" style="background:#131F32;font-size:13px;color:#CBD5E1;"><span>Quarter</span><span>EPS Estimate</span><span>EPS Actual</span><span>Surprise</span></div>'
        for e in reversed(earnings_hist):
            beat_cls = "earn-beat" if e["beat"] else "earn-miss"
            icon     = "▲" if e["beat"] else "▼"
            surp_str = f'{icon} {e["surprise"]:+.1f}%'
            eh_html += f'<div class="earn-hist-row"><span style="color:#E2E8F0;">{e["quarter"]}</span><span style="color:#94A3B8;font-family:monospace;">{cur}{e["estimate"]:.2f}</span><span style="color:#E2E8F0;font-family:monospace;">{cur}{e["actual"]:.2f}</span><span class="{beat_cls};">{surp_str}</span></div>'
        st.markdown(eh_html + '</div>', unsafe_allow_html=True)

    # ── INSIDER TRADING ───────────────────────────────────────
    st.markdown('<div class="section-header">INSIDER TRANSACTIONS</div>', unsafe_allow_html=True)
    if not insider_data:
        st.markdown('<div style="background:#1A2232;border:1px solid #243348;border-radius:0 0 8px 8px;padding:12px 14px;font-size:13px;color:#CBD5E1;">No recent insider transactions found</div>', unsafe_allow_html=True)
    else:
        # ── Insider sentiment flag ────────────────────────────
        cutoff = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
        buy_val  = sum(i['value'] for i in insider_data if i['type']=='BUY'  and i.get('date','') >= cutoff)
        sell_val = sum(i['value'] for i in insider_data if i['type']=='SELL' and i.get('date','') >= cutoff)
        buy_cnt  = sum(1 for i in insider_data if i['type']=='BUY'  and i.get('date','') >= cutoff)
        sell_cnt = sum(1 for i in insider_data if i['type']=='SELL' and i.get('date','') >= cutoff)

        if sell_val > 0 and sell_val > buy_val * 2:
            # Heavy selling — worth flagging
            sell_fmt = f"${sell_val/1e6:.1f}M" if sell_val >= 1e6 else f"${sell_val:,.0f}"
            buy_fmt  = f"${buy_val/1e6:.1f}M"  if buy_val  >= 1e6 else (f"${buy_val:,.0f}" if buy_val > 0 else "none")
            st.markdown(f'''<div style="background:#1A0505;border:1px solid #FF6B6B66;border-radius:8px;
                padding:8px 16px;margin:4px 0 6px;display:flex;align-items:center;gap:10px;">
              <span style="font-size:15px;">⚠️</span>
              <div>
                <span style="color:#FF6B6B;font-weight:800;font-size:13px;
                  font-family:'JetBrains Mono',monospace;">INSIDER SELLING DOMINATES</span>
                <span style="color:#CBD5E1;font-size:13px;margin-left:10px;">
                  {sell_cnt} sell{'s' if sell_cnt>1 else ''} ({sell_fmt}) vs {buy_cnt} buy{'s' if buy_cnt!=1 else ''} ({buy_fmt}) — last 30 days</span>
              </div>
            </div>''', unsafe_allow_html=True)
        elif buy_val > 0 and buy_val > sell_val * 2:
            # Meaningful buying — positive signal
            buy_fmt = f"${buy_val/1e6:.1f}M" if buy_val >= 1e6 else f"${buy_val:,.0f}"
            st.markdown(f'''<div style="background:#030F07;border:1px solid #00FF8844;border-radius:8px;
                padding:8px 16px;margin:4px 0 6px;display:flex;align-items:center;gap:10px;">
              <span style="font-size:15px;">✅</span>
              <div>
                <span style="color:#00FF88;font-weight:800;font-size:13px;
                  font-family:'JetBrains Mono',monospace;">INSIDER BUYING ACTIVE</span>
                <span style="color:#CBD5E1;font-size:13px;margin-left:10px;">
                  {buy_cnt} purchase{'s' if buy_cnt>1 else ''} ({buy_fmt}) — insiders putting own money in</span>
              </div>
            </div>''', unsafe_allow_html=True)

        ins_html = '<div style="background:#1A2232;border:1px solid #243348;border-radius:0 0 8px 8px;overflow:hidden;">'
        ins_html += (
            '<div class="insider-row insider-header">'
            '<span style="font-size:13px;color:#5EEAD4;letter-spacing:1.5px;text-transform:uppercase;font-weight:700;">Insider</span>'
            '<span style="font-size:13px;color:#5EEAD4;letter-spacing:1.5px;text-transform:uppercase;font-weight:700;">Role</span>'
            '<span style="font-size:13px;color:#5EEAD4;letter-spacing:1.5px;text-transform:uppercase;font-weight:700;text-align:center;">Type</span>'
            '<span style="font-size:13px;color:#5EEAD4;letter-spacing:1.5px;text-transform:uppercase;font-weight:700;text-align:right;">Shares</span>'
            '<span style="font-size:13px;color:#5EEAD4;letter-spacing:1.5px;text-transform:uppercase;font-weight:700;text-align:right;">Value</span>'
            '</div>'
        )
        for ins in insider_data:
            badge_cls = "insider-badge-buy" if ins["type"] == "BUY" else "insider-badge-sell"
            val_str   = f'${ins["value"]:,.0f}' if ins["value"] > 0 else "—"
            shares_str = f'{ins["shares"]:,}' if ins["shares"] > 0 else "—"
            ins_html += (
                f'<div class="insider-row">'
                f'<span class="insider-name">{_html.escape(ins["name"])}</span>'
                f'<span class="insider-role">{_html.escape(ins["role"]) if ins["role"] else "—"}</span>'
                f'<span><span class="{badge_cls}">{ins["type"]}</span></span>'
                f'<span class="insider-shares">{shares_str}</span>'
                f'<span class="insider-value">{val_str}</span>'
                f'</div>'
            )
        st.markdown(ins_html + '</div>', unsafe_allow_html=True)

    # ── NEWS SENTIMENT ────────────────────────────────────────
    news_sentiment = a.get('news_sentiment', [])
    news_scores    = a.get('news_scores', [])
    net_score      = a.get('net_news_score', None)
    st.markdown('<div class="section-header">NEWS & SENTIMENT</div>', unsafe_allow_html=True)

    if not news_items:
        st.markdown('<div style="background:#1A2232;border:1px solid #243348;border-radius:0 0 8px 8px;padding:12px 14px;font-size:13px;color:#CBD5E1;">No recent news available</div>', unsafe_allow_html=True)
    else:
        news_html_rows = []
        for i, news in enumerate(news_items):
            title     = _html.escape(str(news.get('title','')))
            pub       = _html.escape(str(news.get('publisher','')))
            link      = _html.escape(str(news.get('link','') or ''))
            published = news.get('published','')[:10] if news.get('published') else ''

            raw_title = news.get('title','')
            sent_data = next((s for s in news_sentiment
                              if raw_title[:20] in s.get('headline','')
                              or s.get('headline','')[:20] in raw_title), None)
            sent      = sent_data.get('sentiment','neutral') if sent_data else 'neutral'
            reason    = _html.escape(str(sent_data.get('reason','') if sent_data else ''))

            score_data = next((s for s in news_scores
                               if raw_title[:25] in s.get('headline','')
                               or s.get('headline','')[:25] in raw_title), None)
            trigger   = _html.escape(score_data.get('trigger','').replace('_',' ').title() if score_data else '')
            magnitude = _html.escape(score_data.get('magnitude','') if score_data else '')

            sent_col  = '#00FF88' if sent=='bullish' else '#FF6B6B' if sent=='bearish' else '#FACC15'
            sent_icon = '▲' if sent=='bullish' else '▼' if sent=='bearish' else '↔'
            mag_col   = '#F97316' if magnitude=='High' else '#FACC15' if magnitude=='Medium' else '#CBD5E1'
            border_b  = 'border-bottom:1px solid #243348;' if i < len(news_items)-1 else ''

            title_html = f'<a href="{link}" target="_blank" rel="noopener" style="color:#E2E8F0;text-decoration:none;font-size:13px;line-height:1.4;">{title}</a>' if link else f'<span style="color:#CBD5E1;font-size:13px;line-height:1.4;">{title}</span>'
            mag_html   = f'<span style="font-size:13px;font-weight:700;padding:2px 5px;border-radius:3px;background:#111827;color:{mag_col};">{magnitude}</span>' if magnitude else ''
            trig_html  = f'<span style="font-size:13px;color:#CBD5E1;">· {trigger}</span>' if trigger else ''
            reas_html  = f'<div style="font-size:13px;color:#CBD5E1;margin-top:2px;">{reason}</div>' if reason else ''
            pub_date   = f"{pub}{' · ' + published if published else ''}"

            _left_accent = f'border-left:3px solid {sent_col};' if magnitude == "High" else ''
            _row = (
                f'<div style="padding:10px 14px;{border_b}{_left_accent}">'
                f'<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:8px;margin-bottom:4px;">'
                f'<div style="flex:1;min-width:0;">' + title_html + '</div>'
                f'<div style="display:flex;gap:4px;flex-shrink:0;align-items:center;">'
                + (f'<span style="font-size:13px;font-weight:700;padding:2px 5px;border-radius:3px;background:#111827;color:{mag_col};">{magnitude}</span>' if magnitude else '')
                + f'<span style="font-size:13px;font-weight:700;color:{sent_col};">{sent_icon} {sent.capitalize()}</span>'
                f'</div></div>'
                f'<div style="font-size:13px;color:#94A3B8;">{pub_date}'
                + (f' &nbsp;·&nbsp; <span style="color:#4A6080;">{trigger}</span>' if trigger else '')
                + '</div>'
                + (f'<div style="font-size:13px;color:#CBD5E1;margin-top:4px;">{reason}</div>' if reason else '')
                + '</div>'
            )
            news_html_rows.append(_row)

        # Single st.markdown call — no split div leak
        st.markdown(
            '<div style="background:#1A2232;border:1px solid #243348;border-radius:0 0 8px 8px;padding:4px 0;">'
            + ''.join(news_html_rows)
            + '</div>',
            unsafe_allow_html=True
        )

        # Net news score bar — separate st.markdown, no nesting issue
        if net_score is not None:
            try:
                ns = int(net_score)
            except:
                ns = 0
            ns_clamped = max(-5, min(5, ns))
            ns_pct     = int((ns_clamped + 5) / 10 * 100)
            if ns > 1:    ns_col, ns_label = '#00FF88', f'+{ns} Bullish'
            elif ns == 1: ns_col, ns_label = '#00FF88', '+1 Mildly Bullish'
            elif ns == 0: ns_col, ns_label = '#FACC15', '0 Neutral'
            elif ns ==-1: ns_col, ns_label = '#FF6B6B', '-1 Mildly Bearish'
            else:         ns_col, ns_label = '#FF6B6B', f'{ns} Bearish'
            st.markdown(f"""
            <div style="background:#0A1020;border:1px solid #1A2A3A;border-radius:8px;
                        padding:10px 14px;margin-top:6px;">
              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
                <span style="font-size:13px;color:#CBD5E1;letter-spacing:2px;
                             text-transform:uppercase;font-weight:700;">📰 News Impact Score</span>
                <span style="font-size:13px;font-weight:800;color:{ns_col};
                             font-family:'JetBrains Mono',monospace;">{ns_label}</span>
              </div>
              <div style="background:#0D1525;border-radius:4px;height:6px;overflow:hidden;">
                <div style="height:100%;width:{ns_pct}%;background:{ns_col};border-radius:4px;"></div>
              </div>
              <div style="display:flex;justify-content:space-between;margin-top:3px;">
                <span style="font-size:13px;color:#CBD5E1;">-5 Very Bearish</span>
                <span style="font-size:13px;color:#CBD5E1;">+5 Very Bullish</span>
              </div>
            </div>""", unsafe_allow_html=True)

    # ── MARKET CONTEXT ────────────────────────────────────────
    mctx      = st.session_state.get('market_ctx', {})
    cycle     = a.get('cycle_phase','')
    cycle_col = "#00FF88" if cycle=="Early" else "#38BDF8" if cycle=="Mid" else "#FACC15" if cycle=="Late" else "#FF6B6B"
    mkt_risk  = a.get('market_risk','')
    risk_col  = "#00FF88" if mkt_risk=="Low" else "#38BDF8" if mkt_risk=="Moderate" else "#FACC15" if mkt_risk=="High" else "#FF6B6B"
    st.markdown('<div class="section-header" style="margin-top:8px;">MARKET CONTEXT & BUSINESS CYCLE</div>', unsafe_allow_html=True)
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
              <div style="font-size:13px;color:#CBD5E1;margin-top:2px;">Last month · {sig}</div>
            </div>''', unsafe_allow_html=True)
    for mcol2, lbl2, val2, col2, desc2 in [
        (mc4, "Cycle Phase", cycle,    cycle_col, a.get("cycle_desc","")),
        (mc5, "Market Risk", mkt_risk, risk_col,  a.get("market_risk_desc","")),
    ]:
        with mcol2:
            st.markdown(f'''<div class="earn-bar" style="border-left-color:{col2};">
              <div class="earn-label">{lbl2}</div>
              <div class="earn-val" style="color:{col2};font-size:13px;">{val2 or "—"}</div>
              <div style="font-size:13px;color:#CBD5E1;margin-top:2px;">{desc2[:60]}</div>
            </div>''', unsafe_allow_html=True)

    # ── CHART PATTERNS ────────────────────────────────────────
    chart_pats = a.get('chart_patterns', [])
    st.markdown('<div class="section-header">CHART PATTERNS DETECTED</div>', unsafe_allow_html=True)
    if not chart_pats:
        st.markdown('<div class="panel-body"><div style="padding:14px;text-align:center;font-size:13px;color:#CBD5E1;">No significant chart patterns detected in current price action</div></div>', unsafe_allow_html=True)
    else:
        cols = st.columns(min(len(chart_pats), 3))
        for i, p in enumerate(chart_pats[:3]):
            ptype = p.get('type','neutral')
            pcls  = "pat-bull" if ptype=="bullish" else "pat-bear" if ptype=="bearish" else "pat-neut"
            pcol  = "#00FF88" if ptype=="bullish" else "#FF6B6B" if ptype=="bearish" else "#FACC15"
            conf  = min(100, max(0, int(p.get('confidence', 0))))
            with cols[i]:
                pat_name    = p.get("name","")
                inv_url     = f"https://www.investopedia.com/search?q={pat_name.replace(' ','+')}"
                bias_label  = "▲ Bullish" if ptype=="bullish" else "▼ Bearish" if ptype=="bearish" else "↔ Neutral"
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
                    <a href="{inv_url}" target="_blank" style="font-size:13px;color:#CBD5E1;text-decoration:none;">ⓘ</a>
                  </div>
                  <div style="font-size:13px;font-weight:700;color:{pcol};margin-bottom:6px;">{bias_label}</div>
                  <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">
                    <div style="font-size:13px;color:#CBD5E1;">Confidence: {conf}%</div>
                    <div style="flex:1;height:3px;background:#243348;border-radius:2px;">
                      <div style="width:{conf}%;height:3px;background:{pcol};border-radius:2px;"></div>
                    </div>
                  </div>
                  {f'<div style="font-size:13px;color:#CBD5E1;font-style:italic;margin-bottom:5px;">{conf_reason}</div>' if conf_reason else ''}
                  <div class="pat-desc" style="margin-bottom:6px;">{p.get("description","")}</div>
                  <div style="font-size:13px;color:{valid_col};font-weight:600;margin-bottom:3px;">{valid_label}</div>
                  {f'<div style="font-size:13px;color:#CBD5E1;">{validity_note}</div>' if validity_note else ''}
                  {target_html}
                </div>""", unsafe_allow_html=True)

    candle_pats = a.get('candle_patterns', [])
    st.markdown('<div class="section-header">CANDLESTICK PATTERNS · LAST 5 SESSIONS</div>', unsafe_allow_html=True)
    if not candle_pats:
        st.markdown('<div class="panel-body"><div style="padding:14px;text-align:center;font-size:13px;color:#CBD5E1;">No significant candlestick patterns in the last 5 sessions</div></div>', unsafe_allow_html=True)
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
                    <a href="{inv_c}" target="_blank" style="font-size:13px;color:#CBD5E1;text-decoration:none;">ⓘ</a>
                  </div>
                  <div style="font-size:13px;color:{ccol};font-weight:700;margin-bottom:5px;">{clabel} · {c.get("session","")}</div>
                  <div style="font-size:13px;color:#CBD5E1;line-height:1.5;">{c.get("meaning","")}</div>
                </div>''', unsafe_allow_html=True)

    st.markdown('<div class="section-header">TREND CONTEXT</div>', unsafe_allow_html=True)
    trend_items = [
        ("Short-term trend (5 days)",   a.get('trend_short','N/A'),  a.get('trend_short_desc','')),
        ("Medium-term trend (20 days)", a.get('trend_medium','N/A'), a.get('trend_medium_desc','')),
        ("Long-term trend (200 days)",  a.get('trend_long','N/A'),   a.get('trend_long_desc','')),
        ("Pattern Bias", a.get('pattern_bias','N/A'), a.get('pattern_bias_desc','')),
    ]
    cols = st.columns(4)
    for i, (lbl, val, desc) in enumerate(trend_items):
        tcol  = "#00FF88" if val=="Uptrend" or val=="Bullish" else "#FF6B6B" if val in ["Downtrend","Bearish"] else "#FACC15"
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
    transcript = st.text_area("Paste transcript here", height=280, placeholder="Q3 2024 Earnings Call Transcript...", key="ea_transcript")
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
                    _ea_key = st.secrets.get("ANTHROPIC_API_KEY", "")
                    if not _ea_key:
                        st.error("Anthropic API key not configured.")
                        return
                    client = anthropic.Anthropic(api_key=_ea_key)
                    msg = client.messages.create(
                        model="claude-sonnet-4-20250514",
                        max_tokens=1000,
                        messages=[{
                            "role": "user",
                            "content": (
                                f"Analyze this earnings call transcript for {ticker_ea or 'the company'}.\n\n"
                                "Return ONLY raw JSON:\n"
                                '{"tone":"Bullish|Neutral|Bearish","management_confidence":"High|Medium|Low",'
                                '"key_wins":["w1","w2","w3"],"key_risks":["r1","r2"],'
                                '"guidance":"","analyst_reception":"","surprise_factors":["s1"],'
                                '"verdict":"","summary":""}\n\n'
                                f"TRANSCRIPT:\n{transcript[:6000]}"
                            )
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
                    c3.markdown(f'<div class="earn-bar" style="border-left-color:#38BDF8;"><div class="earn-label">Analyst Reception</div><div class="earn-val" style="color:#CBD5E1;font-size:13px;">{ea.get("analyst_reception","")[:60]}</div></div>', unsafe_allow_html=True)
                    st.markdown(f'<div style="background:#1A2232;border:1px solid #5EEAD4;border-radius:8px;padding:14px 18px;margin:8px 0;"><div style="font-size:13px;color:#CBD5E1;margin-bottom:6px;">VERDICT</div><div style="font-size:14px;color:#E2E8F0;line-height:1.6;">{ea.get("verdict","")}</div></div>', unsafe_allow_html=True)
                    c1,c2 = st.columns(2)
                    with c1:
                        st.markdown('<div class="section-header">KEY WINS</div>', unsafe_allow_html=True)
                        for w in ea.get("key_wins",[]):
                            st.markdown(f'<div class="reason-bull">+ {w}</div>', unsafe_allow_html=True)
                    with c2:
                        st.markdown('<div class="section-header">KEY RISKS</div>', unsafe_allow_html=True)
                        for r in ea.get("key_risks",[]):
                            st.markdown(f'<div class="reason-bear">- {r}</div>', unsafe_allow_html=True)
                    if ea.get("guidance"):
                        st.markdown(f'<div style="background:#251800;border-left:3px solid #FACC15;border-radius:0 6px 6px 0;padding:10px 14px;font-size:13px;color:#E2E8F0;"><b style="color:#FACC15;">Guidance:</b> {ea["guidance"]}</div>', unsafe_allow_html=True)
                    st.markdown(f'<div class="summary-box"><div style="font-size:13px;color:#CBD5E1;margin-bottom:6px;">FULL ANALYSIS</div><div class="summary-text">{ea.get("summary","")}</div></div>', unsafe_allow_html=True)
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

def translate_theme_to_filter(theme):
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

def compute_composite_score(info, df, row):
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

def passes_filter(info, df, row, flt):
    close   = float(row.get('Close', 0))
    if close == 0: return False
    score   = compute_composite_score(info, df, row)
    rsi     = float(row.get('RSI', 50))
    vol_t   = float(row.get('VolTrend', 1))
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
    if flt.get("div_min", 0) > 0 and div_y <= 0:    return False
    return True

def sns_one_liner(ticker, score, row, flt):
    rsi   = float(row.get('RSI', 50))
    vol_t = float(row.get('VolTrend', 1))
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

        st.markdown('<div style="font-size:13px;color:#CBD5E1;margin-bottom:8px;letter-spacing:1px;text-transform:uppercase;">Quick Templates</div>', unsafe_allow_html=True)
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
                  <div class="tpl-card" style="background:{bg_col};border:1px solid {txt_col}44;">
                    <div style="font-size:13px;font-weight:800;color:{txt_col};margin-bottom:3px;">{tname}</div>
                    <div style="font-size:13px;color:#CBD5E1;line-height:1.5;">{tdata["desc"]}</div>
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
                        padding:8px 16px;margin:8px 0;display:flex;align-items:center;justify-content:space-between;">
              <div style="font-size:13px;color:#CBD5E1;font-weight:700;">{chosen_name}</div>
              <div style="font-size:13px;color:#CBD5E1;">{chosen_filter.get("desc","")}</div>
              <div style="font-size:13px;color:#CBD5E1;">Universe: <span style="color:#5EEAD4;font-weight:600;">{selected_universe} ({len(SNS_UNIVERSES[selected_universe])} stocks)</span></div>
            </div>''', unsafe_allow_html=True)

            if st.button("▶ Run Screener", type="primary", use_container_width=True, key="sns_run"):
                tickers = SNS_UNIVERSES[selected_universe]
                results = []
                prog = st.progress(0, text="Scanning universe...")
                for i, sym in enumerate(tickers):
                    try:
                        d  = fetch_ticker_data(sym, fmp_key_sc, _v=17)
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
                st.markdown(f'<div style="font-size:13px;color:#CBD5E1;font-weight:700;margin:8px 0;">{len(results)} stocks passed the filter</div>', unsafe_allow_html=True)
                for r in results[:15]:
                    sc_col  = "#00FF88" if r["score"]>=7 else "#FACC15" if r["score"]>=4 else "#FF6B6B"
                    chg_col = "#00FF88" if r["chg"] >= 0 else "#FF6B6B"
                    sign    = "+" if r["chg"] >= 0 else ""
                    vol_col = "#FACC15" if r["vol_t"] >= 1.5 else "#94A3B8"
                    cur_sym = "CA$" if r["ticker"].endswith(".TO") else "$"
                    st.markdown(f'''
                    <div style="background:#1A2232;border:1px solid #243348;border-radius:8px;
                                padding:10px 16px;margin-bottom:6px;display:flex;align-items:center;gap:16px;">
                      <div style="font-family:'JetBrains Mono',monospace;font-size:16px;font-weight:800;color:#00FF88;min-width:80px;">{r["ticker"]}</div>
                      <div style="flex:1;min-width:0;">
                        <div style="font-size:13px;font-weight:600;color:#E2E8F0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{r["name"]}</div>
                        <div style="font-size:13px;color:#CBD5E1;margin-top:2px;">{r["one_liner"]}</div>
                      </div>
                      <div style="text-align:center;min-width:50px;">
                        <div style="font-size:18px;font-weight:800;color:{sc_col};font-family:'JetBrains Mono',monospace;">{r["score"]}</div>
                        <div style="font-size:13px;color:#CBD5E1;">SCORE</div>
                      </div>
                      <div style="text-align:right;min-width:90px;">
                        <div style="font-family:'JetBrains Mono',monospace;font-size:15px;font-weight:700;color:#F1F5F9;">{cur_sym}{r["close"]:.2f}</div>
                        <div style="font-size:13px;color:{chg_col};font-weight:600;">{sign}{r["chg"]:.2f}%</div>
                      </div>
                      <div style="text-align:center;min-width:60px;">
                        <div style="font-size:13px;color:#38BDF8;font-family:monospace;font-weight:700;">{r["rsi"]:.0f}</div>
                        <div style="font-size:13px;color:#CBD5E1;">RSI</div>
                      </div>
                      <div style="text-align:center;min-width:50px;">
                        <div style="font-size:13px;color:{vol_col};font-family:monospace;font-weight:700;">{r["vol_t"]:.1f}x</div>
                        <div style="font-size:13px;color:#CBD5E1;">VOL</div>
                      </div>
                    </div>''', unsafe_allow_html=True)
                    if st.button(f"📊 Full Analysis → {r['ticker']}", key=f"sns_analyze_{r['ticker']}", use_container_width=False):
                        run_analysis(r['ticker'])

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
                    d   = fetch_ticker_data(sym, fmp_key_sc, _v=17)
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
                                    padding:10px 16px;margin-bottom:6px;display:flex;align-items:center;gap:16px;">
                          <div style="font-family:'JetBrains Mono',monospace;font-size:15px;font-weight:800;color:#00FF88;min-width:72px;">{sym}</div>
                          <div style="flex:1;font-size:13px;color:#CBD5E1;">{cname}</div>
                          <div style="font-size:19px;font-weight:800;color:{sc_col};font-family:monospace;min-width:30px;">{sc}</div>
                          <div style="font-family:monospace;font-size:15px;font-weight:700;color:#F1F5F9;">{cur_w}{close:.2f}</div>
                          <div style="font-size:13px;color:{chg_col};font-weight:700;">{sign}{chg_pct:.2f}%</div>
                          <div style="font-size:13px;color:#38BDF8;font-family:monospace;">{float(row["RSI"]):.0f} RSI</div>
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
