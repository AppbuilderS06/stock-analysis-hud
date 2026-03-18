"""
generate_fixtures.py — Run ONCE to capture golden fixtures.

Usage:
    python generate_fixtures.py

Saves to: tests/fixtures/
  - {TICKER}_df.csv          ← price + indicator history
  - {TICKER}_earn_hist.csv   ← earnings history (if available)
  - {TICKER}_rec_summary.csv ← analyst buy/hold/sell (if available)
  - {TICKER}_insider.csv     ← insider transactions (if available)
  - {TICKER}_meta.json       ← info dict + scalar fields (iv, news, etc.)

Run this after market close on a Tuesday-Thursday to maximize data availability.
Commit the fixtures to git — CI will use them forever without hitting any API.
"""

import os, json, sys
import pandas as pd

# ── Make sure we can import from app.py in repo root ─────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── FMP key — set as env var or paste here for one-time run ──
FMP_KEY = os.environ.get("FMP_API_KEY", "")

GOLDEN_TICKERS = ["NVDA", "AAPL", "RY.TO", "PLTR", "BRK-B"]

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "tests", "fixtures")
os.makedirs(FIXTURE_DIR, exist_ok=True)


def serialize_df(df, path):
    """Save DataFrame to CSV, preserving DatetimeIndex."""
    if df is None or (hasattr(df, "empty") and df.empty):
        return False
    try:
        df.to_csv(path)
        print(f"  ✅ Saved {os.path.basename(path)} ({len(df)} rows)")
        return True
    except Exception as e:
        print(f"  ⚠️  Could not save {os.path.basename(path)}: {e}")
        return False


def serialize_meta(data, path):
    """Save non-DataFrame fields as JSON."""
    meta = {}

    # Scalar / dict / list fields
    for key in ("iv", "news", "analyst_targets", "calendar"):
        val = data.get(key)
        if val is None:
            meta[key] = None
        elif isinstance(val, dict):
            # Convert any non-serializable values inside calendar/analyst_targets
            try:
                json.dumps(val)
                meta[key] = val
            except TypeError:
                meta[key] = str(val)
        elif isinstance(val, list):
            meta[key] = val
        else:
            meta[key] = val

    # info dict — strip non-JSON types
    info = data.get("info", {})
    clean_info = {}
    for k, v in info.items():
        try:
            json.dumps({k: v})
            clean_info[k] = v
        except (TypeError, ValueError):
            clean_info[k] = str(v)
    meta["info"] = clean_info

    # Snapshot: row counts for DataFrames — so test can assert they loaded
    for df_key in ("df", "earn_hist", "rec_summary", "insider"):
        df_val = data.get(df_key)
        if df_val is not None and hasattr(df_val, "__len__"):
            meta[f"{df_key}_rows"] = len(df_val)
        else:
            meta[f"{df_key}_rows"] = 0

    with open(path, "w") as f:
        json.dump(meta, f, indent=2, default=str)
    print(f"  ✅ Saved {os.path.basename(path)}")


def generate_fixture(ticker):
    print(f"\n{'='*50}")
    print(f"  Fetching {ticker}...")
    print(f"{'='*50}")

    # Import here so Streamlit isn't needed (we mock st.secrets)
    try:
        import streamlit as st
        # Inject FMP key into secrets for the import
        if not hasattr(st, "secrets") or not st.secrets.get("FMP_API_KEY"):
            st.secrets["FMP_API_KEY"] = FMP_KEY
    except Exception:
        pass

    from app import fetch_ticker_data, calculate_indicators

    # Fetch live data (this is the one-time API hit)
    try:
        data = fetch_ticker_data(ticker, FMP_KEY, _v=15)
    except Exception as e:
        print(f"  ❌ fetch_ticker_data failed: {e}")
        return

    prefix = ticker.replace(".", "_").replace("-", "_")

    # 1. Price history + calculated indicators
    df = data.get("df", pd.DataFrame())
    if not df.empty:
        try:
            df = calculate_indicators(df)
        except Exception as e:
            print(f"  ⚠️  calculate_indicators failed: {e}")
    serialize_df(df, os.path.join(FIXTURE_DIR, f"{prefix}_df.csv"))

    # 2. Earnings history
    eh = data.get("earn_hist")
    serialize_df(eh, os.path.join(FIXTURE_DIR, f"{prefix}_earn_hist.csv"))

    # 3. Analyst recommendations
    rs = data.get("rec_summary")
    serialize_df(rs, os.path.join(FIXTURE_DIR, f"{prefix}_rec_summary.csv"))

    # 4. Insider transactions
    ins = data.get("insider")
    serialize_df(ins, os.path.join(FIXTURE_DIR, f"{prefix}_insider.csv"))

    # 5. Everything else
    serialize_meta(data, os.path.join(FIXTURE_DIR, f"{prefix}_meta.json"))

    # Quick sanity print
    row = df.iloc[-1] if not df.empty else None
    if row is not None:
        print(f"  📊 Last close: {float(row.get('Close', 0)):.2f}")
        print(f"  📊 RSI: {float(row.get('RSI', 0)):.1f}")
        print(f"  📊 MA20: {float(row.get('MA20', 0)):.2f}")
    print(f"  ✅ {ticker} done")


if __name__ == "__main__":
    print("Stock Analysis HUD — Golden Fixture Generator")
    print(f"FMP key: {'SET ✅' if FMP_KEY else 'MISSING ⚠️ (yfinance only)'}")
    print(f"Output:  {FIXTURE_DIR}\n")

    tickers = sys.argv[1:] if len(sys.argv) > 1 else GOLDEN_TICKERS

    for ticker in tickers:
        generate_fixture(ticker)

    print(f"\n{'='*50}")
    print(f"✅ Fixtures saved to {FIXTURE_DIR}")
    print("Commit these files to git — CI will never call live APIs.")
    print(f"{'='*50}\n")
