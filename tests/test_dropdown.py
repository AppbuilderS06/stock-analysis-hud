"""
test_dropdown.py
Dropdown ticker selection logic.
MULTI_LISTED priority, FMP fallback, never-auto-pick rules.
"""
import pytest


# ── Simulated dropdown decision logic ────────────────────────

def resolve_ticker(ticker_upper, multi_listed, fmp_results, should_analyze,
                   button_clicked=None):
    """
    Mirrors the dropdown logic in main().
    Returns selected_ticker or None.
    button_clicked = symbol string if user clicked a ▶ button.
    """
    selected = None

    # Button click always wins
    if button_clicked:
        return button_clicked

    # Step 1: MULTI_LISTED check (hardcoded, always first)
    if ticker_upper in multi_listed:
        # Never auto-pick from MULTI_LISTED — must click
        return None  # shows dropdown, waits for click

    # Step 2: FMP results
    if fmp_results:
        # Never auto-pick with FMP — must click
        return None  # shows dropdown, waits for click

    # Step 3: No FMP results + should_analyze → try directly
    if should_analyze and not fmp_results:
        return ticker_upper

    return None


MULTI_LISTED = {
    'BRK':  [{'ticker': 'BRK-B', 'name': 'Berkshire Hathaway B', 'exchange': 'NYSE', 'currency': 'USD'},
             {'ticker': 'BRK-A', 'name': 'Berkshire Hathaway A', 'exchange': 'NYSE', 'currency': 'USD'}],
    'RY':   [{'ticker': 'RY',    'name': 'Royal Bank (US)',       'exchange': 'NYSE', 'currency': 'USD'},
             {'ticker': 'RY.TO', 'name': 'Royal Bank (TSX)',      'exchange': 'TSX',  'currency': 'CAD'}],
    'SHOP': [{'ticker': 'SHOP',    'name': 'Shopify (NYSE)', 'exchange': 'NYSE', 'currency': 'USD'},
             {'ticker': 'SHOP.TO', 'name': 'Shopify (TSX)',  'exchange': 'TSX',  'currency': 'CAD'}],
}


class TestDropdownNeverAutoRuns:
    """
    Core rule: NEVER auto-pick when multiple results exist.
    User must always click ▶ to confirm their choice.
    """

    def test_brk_shows_dropdown_not_auto_run(self):
        result = resolve_ticker("BRK", MULTI_LISTED, None, should_analyze=True)
        assert result is None, "BRK must show dropdown, not auto-run"

    def test_brk_button_click_picks_brk_b(self):
        result = resolve_ticker("BRK", MULTI_LISTED, None, should_analyze=True,
                                button_clicked="BRK-B")
        assert result == "BRK-B"

    def test_brk_button_click_picks_brk_a(self):
        result = resolve_ticker("BRK", MULTI_LISTED, None, should_analyze=True,
                                button_clicked="BRK-A")
        assert result == "BRK-A"

    def test_fmp_multiple_results_no_auto_run(self):
        fmp = [
            {"symbol": "NPK", "name": "National Presto", "exchangeShortName": "NYSE"},
            {"symbol": "NPK", "name": "Verde AgriTech",  "exchangeShortName": "TSX"},
        ]
        result = resolve_ticker("NPK", {}, fmp, should_analyze=True)
        assert result is None, "Multiple FMP results must show dropdown, not auto-run"

    def test_fmp_button_click_picks_correct(self):
        fmp = [
            {"symbol": "NPK", "name": "National Presto", "exchangeShortName": "NYSE"},
            {"symbol": "NPK", "name": "Verde AgriTech",  "exchangeShortName": "TSX"},
        ]
        result = resolve_ticker("NPK", {}, fmp, should_analyze=True,
                                button_clicked="NPK")
        assert result == "NPK"

    def test_no_fmp_results_direct_run(self):
        """When FMP has nothing, direct run is the only option."""
        result = resolve_ticker("NPK.TO", {}, [], should_analyze=True)
        assert result == "NPK.TO"

    def test_no_fmp_results_no_analyze_does_nothing(self):
        result = resolve_ticker("NPK.TO", {}, [], should_analyze=False)
        assert result is None

    def test_enter_key_with_fmp_results_still_shows_dropdown(self):
        """Enter key = should_analyze=True. Still must NOT auto-pick."""
        fmp = [{"symbol": "NVDA", "name": "NVIDIA", "exchangeShortName": "NASDAQ"}]
        result = resolve_ticker("NVDA", {}, fmp, should_analyze=True)
        assert result is None, "Enter key with FMP results must show dropdown"

    def test_multi_listed_check_before_fmp(self):
        """BRK must hit MULTI_LISTED check before FMP is consulted."""
        fmp_that_returns_wrong = [
            {"symbol": "BRK", "name": "Some obscure BRK", "exchangeShortName": "OTC"}
        ]
        # With MULTI_LISTED priority, BRK goes to MULTI_LISTED path, not FMP
        result = resolve_ticker("BRK", MULTI_LISTED, fmp_that_returns_wrong,
                                should_analyze=True)
        # Still None — shows MULTI_LISTED dropdown, not FMP results
        assert result is None


class TestMultiListedSchema:

    def test_all_entries_have_required_keys(self):
        required = {"ticker", "name", "exchange", "currency"}
        for key, opts in MULTI_LISTED.items():
            for opt in opts:
                missing = required - set(opt.keys())
                assert not missing, f"MULTI_LISTED['{key}'] missing: {missing}"

    def test_brk_has_two_entries(self):
        assert len(MULTI_LISTED["BRK"]) == 2

    def test_brk_entries_correct(self):
        tickers = [o["ticker"] for o in MULTI_LISTED["BRK"]]
        assert "BRK-B" in tickers
        assert "BRK-A" in tickers

    def test_ry_has_usd_and_cad(self):
        currencies = {o["currency"] for o in MULTI_LISTED["RY"]}
        assert "USD" in currencies
        assert "CAD" in currencies

    def test_all_currencies_valid(self):
        valid_currencies = {"USD", "CAD", "GBP", "EUR", "HKD", "AUD"}
        for key, opts in MULTI_LISTED.items():
            for opt in opts:
                assert opt["currency"] in valid_currencies, \
                    f"Unknown currency '{opt['currency']}' in MULTI_LISTED['{key}']"


class TestSearchSorting:
    """FMP search results must be sorted: exact match first, major exchange second."""

    def _sort(self, results, query):
        major = {"NYSE", "NASDAQ", "TSX", "LSE", "EURONEXT", "XETRA", "ASX", "HKG", "NSE", "AMEX", "BATS"}
        def sort_key(r):
            is_exact = 0 if r.get("symbol", "").upper() == query.upper() else 1
            is_major = 0 if r.get("exchangeShortName", "") in major else 1
            return (is_exact, is_major)
        return sorted(results, key=sort_key)

    def test_exact_match_sorts_first(self):
        results = [
            {"symbol": "NVDAX", "exchangeShortName": "XETRA"},
            {"symbol": "NVDA",  "exchangeShortName": "NASDAQ"},
            {"symbol": "NVDA2", "exchangeShortName": "OTC"},
        ]
        sorted_r = self._sort(results, "NVDA")
        assert sorted_r[0]["symbol"] == "NVDA"

    def test_major_exchange_before_minor(self):
        results = [
            {"symbol": "NPK", "exchangeShortName": "OTC"},
            {"symbol": "NPK", "exchangeShortName": "NYSE"},
            {"symbol": "NPK", "exchangeShortName": "SET"},
        ]
        sorted_r = self._sort(results, "NPK")
        assert sorted_r[0]["exchangeShortName"] == "NYSE"

    def test_empty_results_handled(self):
        sorted_r = self._sort([], "NVDA")
        assert sorted_r == []
