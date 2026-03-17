"""
test_insider.py
Insider transaction BUY/SELL classification.
Bug: 'shares > 0' flagged ALL transactions as BUY (sells also have positive counts).
Fix: check sell keywords first, then buy keywords, shares count is last resort only.
"""
import pytest


def classify_insider(text, trans, shares):
    """Mirrors the fixed is_buy logic in run_analysis."""
    combined = (text + trans).lower()
    is_sell = any(w in combined for w in ('sale', 'sell', 'dispose', 'disposed'))
    is_buy  = (not is_sell and
               any(w in combined for w in ('purchase', 'buy', 'acquisition', 'grant', 'award', 'exercise')))
    if not is_buy and not is_sell:
        is_buy = shares > 0  # last resort fallback only
    return "BUY" if is_buy else "SELL"


class TestInsiderClassification:

    # ── Clear SELL cases ──────────────────────────────────────

    def test_sale_text_is_sell(self):
        assert classify_insider("Sale of shares", "", 5000) == "SELL"

    def test_sell_text_is_sell(self):
        assert classify_insider("Automatic sell", "", 1000) == "SELL"

    def test_dispose_is_sell(self):
        assert classify_insider("", "Dispose", 2000) == "SELL"

    def test_disposed_is_sell(self):
        assert classify_insider("Shares disposed", "", 3000) == "SELL"

    def test_sale_with_positive_shares_is_still_sell(self):
        """THE BUG: shares=5000 > 0 was triggering BUY for sales."""
        assert classify_insider("Sale of common stock", "Dispose", 5000) == "SELL"

    # ── Clear BUY cases ───────────────────────────────────────

    def test_purchase_text_is_buy(self):
        assert classify_insider("Purchase of shares", "", 1000) == "BUY"

    def test_buy_text_is_buy(self):
        assert classify_insider("Open market buy", "", 500) == "BUY"

    def test_acquisition_is_buy(self):
        assert classify_insider("", "Acquisition", 200) == "BUY"

    def test_grant_is_buy(self):
        assert classify_insider("Grant of RSUs", "", 10000) == "BUY"

    def test_award_is_buy(self):
        assert classify_insider("Award of stock options", "", 50000) == "BUY"

    def test_exercise_is_buy(self):
        assert classify_insider("Exercise of options", "", 5000) == "BUY"

    # ── Edge cases ────────────────────────────────────────────

    def test_empty_text_positive_shares_is_buy(self):
        """Last resort: no keywords but shares > 0 → BUY."""
        assert classify_insider("", "", 1000) == "BUY"

    def test_empty_text_zero_shares_is_sell(self):
        assert classify_insider("", "", 0) == "SELL"

    def test_sell_keyword_overrides_positive_shares(self):
        """Sell keyword wins even if shares is a huge positive number."""
        assert classify_insider("Sale", "", 999999) == "SELL"

    def test_buy_keyword_overrides_zero_shares(self):
        """Buy keyword wins even if shares is 0."""
        assert classify_insider("Purchase", "", 0) == "BUY"

    def test_case_insensitive(self):
        assert classify_insider("SALE OF SHARES", "", 1000) == "SELL"
        assert classify_insider("PURCHASE OF SHARES", "", 1000) == "BUY"

    def test_real_ceo_sell_scenario(self, sample_insider_sell):
        """CFO Colette Kress selling 5000 shares must be classified as SELL."""
        ri = sample_insider_sell
        text  = str(ri.get('Text', '') or '')
        trans = str(ri.get('Transaction', '') or '')
        shares = int(ri.get('Shares', 0) or 0)
        result = classify_insider(text, trans, shares)
        assert result == "SELL"

    def test_real_ceo_buy_scenario(self, sample_insider_buy):
        """Jensen Huang purchasing 1000 shares must be classified as BUY."""
        ri = sample_insider_buy
        text  = str(ri.get('Text', '') or '')
        trans = str(ri.get('Transaction', '') or '')
        shares = int(ri.get('Shares', 0) or 0)
        result = classify_insider(text, trans, shares)
        assert result == "BUY"
