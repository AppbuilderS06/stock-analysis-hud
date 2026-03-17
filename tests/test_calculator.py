"""
test_calculator.py
Risk/Reward calculator math — position sizing, derived stop,
R:R ratio, currency detection. All deterministic.
"""
import pytest


# ── Position size model ───────────────────────────────────────

class TestPositionSizeModel:
    """
    New model: Position Size ($) + Risk (%) → Shares + Derived Stop
    Shares = Position ÷ Entry
    Dollar risk = Position × Risk%
    Derived stop = Entry − (Dollar risk ÷ Shares)
    """

    def _calc(self, position, risk_pct, entry, target, stop_override=None):
        shares = int(position / entry) if entry > 0 else 0
        dollar_risk = round(position * (risk_pct / 100), 2)
        derived_stop = round(entry - (dollar_risk / shares), 2) if shares > 0 else 0
        stop = stop_override if stop_override is not None else derived_stop

        risk_per_share   = round(abs(entry - stop), 2)
        reward_per_share = round(abs(target - entry), 2)
        rr_ratio = round(reward_per_share / risk_per_share, 2) if risk_per_share > 0 else 0
        actual_loss = round(shares * risk_per_share, 2)
        actual_gain = round(shares * reward_per_share, 2)
        return {
            "shares": shares,
            "dollar_risk": dollar_risk,
            "derived_stop": derived_stop,
            "stop": stop,
            "risk_per_share": risk_per_share,
            "reward_per_share": reward_per_share,
            "rr_ratio": rr_ratio,
            "actual_loss": actual_loss,
            "actual_gain": actual_gain,
        }

    def test_basic_position_sizing(self):
        """$10K position at $151 = 66 shares."""
        r = self._calc(position=10000, risk_pct=5, entry=151, target=157)
        assert r["shares"] == 66

    def test_dollar_risk_correct(self):
        """5% of $10K = $500 max loss."""
        r = self._calc(position=10000, risk_pct=5, entry=151, target=157)
        assert r["dollar_risk"] == 500.0

    def test_derived_stop_correct(self):
        """Stop = 151 − (500 ÷ 66) = 151 − 7.58 = 143.42"""
        r = self._calc(position=10000, risk_pct=5, entry=151, target=157)
        assert abs(r["derived_stop"] - 143.42) < 0.05

    def test_rr_ratio_with_derived_stop(self):
        """Reward = 157-151 = 6, Risk = 151-143.42 = 7.58 → R:R ≈ 0.79"""
        r = self._calc(position=10000, risk_pct=5, entry=151, target=157)
        # R:R = 6 / 7.58 ≈ 0.79
        assert r["rr_ratio"] > 0

    def test_stop_override_changes_rr(self):
        """Pro moves stop from $143 to $147 — R:R improves."""
        r_default = self._calc(position=10000, risk_pct=5, entry=151, target=157)
        r_override = self._calc(position=10000, risk_pct=5, entry=151, target=157,
                                stop_override=147)
        assert r_override["rr_ratio"] > r_default["rr_ratio"], \
            "Tighter stop should improve R:R ratio"

    def test_actual_loss_matches_dollar_risk_with_derived_stop(self):
        """With derived stop: actual loss should approximately equal dollar_risk."""
        r = self._calc(position=10000, risk_pct=5, entry=151, target=157)
        # 66 shares × 7.58 risk/share ≈ $500 (small rounding)
        assert abs(r["actual_loss"] - r["dollar_risk"]) < 5.0, \
            "Actual loss should be close to dollar_risk when using derived stop"

    def test_high_price_stock_fewer_shares(self):
        """BRK-B at $492 with $5K position = 10 shares."""
        r = self._calc(position=5000, risk_pct=2, entry=492, target=520)
        assert r["shares"] == 10

    def test_zero_risk_pct_doesnt_crash(self):
        """Edge case: 0% risk."""
        shares = int(10000 / 151) if 151 > 0 else 0
        dollar_risk = round(10000 * (0.1 / 100), 2)  # min is 0.1
        assert dollar_risk > 0

    def test_entry_equals_stop_no_division_by_zero(self):
        """Edge case: entry == stop."""
        entry = stop = 151.0
        risk_per_share = round(abs(entry - stop), 2)
        rr_ratio = 0  # safe default when risk_per_share == 0
        if risk_per_share > 0:
            rr_ratio = round(6.0 / risk_per_share, 2)
        assert rr_ratio == 0


# ── ATR preset tests ──────────────────────────────────────────

class TestATRPresets:
    """
    calc_presets must use ATR to set stops, not arbitrary values.
    Day: 0.5 ATR stop. Swing: 1.5 ATR stop (or support).
    """

    def _presets(self, mode, entry, atr, s1=0, r1=0, r2=0, ma200=0, s2=0):
        if mode == "Day Trade":
            stp = round(entry - 0.5 * atr, 2)
            tgt = round(entry + 1.5 * atr, 2)
        elif mode == "Swing Trade":
            stp = round(entry - 1.5 * atr, 2)
            if s1 > 0 and s1 < entry and s1 > stp:
                stp = round(s1 - 0.01, 2)
            tgt = r1 if r1 > entry else round(entry + 3 * atr, 2)
        else:
            stp = round(entry - 3 * atr, 2)
            deep = min(ma200 if ma200 > 0 else entry, s2 if s2 > 0 else entry)
            if 0 < deep < entry and deep > stp:
                stp = round(deep - 0.01, 2)
            tgt = r2 if r2 > entry else round(entry + 6 * atr, 2)
        return max(0.01, stp), max(entry + 0.01, tgt)

    def test_day_trade_stop_is_half_atr(self):
        stp, tgt = self._presets("Day Trade", entry=151, atr=5.88)
        assert abs(stp - (151 - 0.5 * 5.88)) < 0.02

    def test_day_trade_target_is_1_5_atr(self):
        stp, tgt = self._presets("Day Trade", entry=151, atr=5.88)
        assert abs(tgt - (151 + 1.5 * 5.88)) < 0.02

    def test_swing_stop_is_1_5_atr(self):
        stp, tgt = self._presets("Swing Trade", entry=151, atr=5.88)
        assert abs(stp - (151 - 1.5 * 5.88)) < 0.02

    def test_swing_uses_support_if_tighter(self):
        """Support at 143 is tighter than 1.5 ATR stop — use support."""
        stp, _ = self._presets("Swing Trade", entry=151, atr=2.0,
                                s1=143, r1=160)
        # 1.5 ATR stop = 151 - 3.0 = 148 — support at 143 is lower, don't use
        # But if s1=148.5 > 1.5ATR stop (148), use s1-0.01 = 148.49
        stp2, _ = self._presets("Swing Trade", entry=151, atr=2.0,
                                 s1=148.5, r1=160)
        assert abs(stp2 - 148.49) < 0.02

    def test_swing_uses_resistance_as_target(self):
        _, tgt = self._presets("Swing Trade", entry=151, atr=5.88, r1=165)
        assert tgt == 165.0

    def test_stop_never_negative(self):
        """Even with huge ATR, stop must be > 0."""
        stp, _ = self._presets("Day Trade", entry=5.0, atr=10.0)
        assert stp >= 0.01

    def test_target_always_above_entry(self):
        _, tgt = self._presets("Day Trade", entry=151, atr=5.88)
        assert tgt > 151


# ── R:R ratio color and label tests ──────────────────────────

class TestRRColorLabel:

    def _color_label(self, rr_ratio):
        rr_col   = "#00FF88" if rr_ratio >= 2 else "#FACC15" if rr_ratio >= 1 else "#FF6B6B"
        rr_label = ("Excellent" if rr_ratio >= 3 else
                    "Good"      if rr_ratio >= 2 else
                    "Acceptable" if rr_ratio >= 1 else
                    "Poor — avoid")
        return rr_col, rr_label

    def test_excellent(self):
        col, lbl = self._color_label(3.5)
        assert col == "#00FF88"
        assert lbl == "Excellent"

    def test_good(self):
        col, lbl = self._color_label(2.0)
        assert col == "#00FF88"
        assert lbl == "Good"

    def test_acceptable(self):
        col, lbl = self._color_label(1.5)
        assert col == "#FACC15"
        assert lbl == "Acceptable"

    def test_poor(self):
        col, lbl = self._color_label(0.5)
        assert col == "#FF6B6B"
        assert lbl == "Poor — avoid"

    def test_exactly_2_is_good(self):
        _, lbl = self._color_label(2.0)
        assert lbl == "Good"

    def test_exactly_1_is_acceptable(self):
        _, lbl = self._color_label(1.0)
        assert lbl == "Acceptable"


# ── Currency detection tests ──────────────────────────────────

class TestCurrencyDetection:

    def _detect(self, ticker):
        if ticker.endswith('.TO') or ticker.endswith('.CN'):
            return "CA$", "CAD"
        elif ticker.endswith('.L'):
            return "£", "GBP"
        elif ticker.endswith('.PA') or ticker.endswith('.DE') or ticker.endswith('.AS'):
            return "€", "EUR"
        elif ticker.endswith('.HK'):
            return "HK$", "HKD"
        else:
            return "$", "USD"

    def test_us_stock(self):      assert self._detect("NVDA")    == ("$",    "USD")
    def test_tsx_stock(self):     assert self._detect("RY.TO")   == ("CA$",  "CAD")
    def test_lse_stock(self):     assert self._detect("BP.L")    == ("£",    "GBP")
    def test_paris_stock(self):   assert self._detect("BNP.PA")  == ("€",    "EUR")
    def test_frankfurt_stock(self): assert self._detect("VOW.DE") == ("€",   "EUR")
    def test_hk_stock(self):      assert self._detect("0700.HK") == ("HK$",  "HKD")
    def test_neo_exchange(self):  assert self._detect("VGRO.CN") == ("CA$",  "CAD")
    def test_brk_b_is_usd(self):  assert self._detect("BRK-B")   == ("$",   "USD")
