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
                    <span style="font-size:9px;color:#374151;letter-spacing:1px;">
                      ● BULL &nbsp; ○ BEAR
                    </span>
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
                 "1:2 = risk $1 to make $2. Never take a trade below 1:1."),
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

        if st.button("🔄  Clear Cache & Refresh", use_container_width=True):
            st.cache_data.clear()
            for k in list(st.session_state.keys()):
                del st.session_state[k]
            st.success("Cache cleared!")
            st.rerun()

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
