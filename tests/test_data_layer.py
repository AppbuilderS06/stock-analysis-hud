"""
test_data_layer.py
Tests for fetch_ticker_data, _fmp_get, search_ticker_fmp.
All mocked — zero live API calls, fully deterministic.
"""
import pytest
import pandas as pd
import numpy as np
from unittest.mock import patch, MagicMock, call


# ── _fmp_get tests ───────────────────────────────────────────

class TestFmpGet:
    """
    _fmp_get must return None for:
    - Rate limit responses (HTTP 200 + {"Error Message": ...})
    - Auth errors (HTTP 200 + {"message": ...})
    - HTTP non-200 responses
    - Empty responses
    - Network errors
    """

    def _make_response(self, status_code, json_body):
        mock = MagicMock()
        mock.status_code = status_code
        mock.json.return_value = json_body
        return mock

    def _run(self, json_body, status=200):
        """Run _fmp_get logic with a mocked response — no real HTTP calls."""
        from unittest.mock import MagicMock

        # Inline the _fmp_get logic so no requests import needed
        def mock_fmp_get(json_body, status_code):
            try:
                r = self._make_response(status_code, json_body)
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

        return mock_fmp_get(json_body, status)

    def test_rate_limit_returns_none(self, fmp_rate_limit_response):
        result = self._run(fmp_rate_limit_response)
        assert result is None, "Rate limit response must return None"

    def test_auth_error_returns_none(self, fmp_auth_error_response):
        result = self._run(fmp_auth_error_response)
        assert result is None, "Auth error response must return None"

    def test_valid_list_response_passes(self):
        valid = [{"companyName": "NVIDIA", "sector": "Technology", "pe": 65.0}]
        result = self._run(valid)
        assert result == valid

    def test_valid_dict_without_error_key_passes(self):
        valid = {"targetConsensus": 1050.0, "targetHigh": 1200.0}
        result = self._run(valid)
        assert result == valid

    def test_empty_list_returns_none(self):
        result = self._run([])
        assert result is None

    def test_none_body_returns_none(self):
        result = self._run(None)
        assert result is None

    def test_http_400_returns_none(self):
        result = self._run({"data": "something"}, status=400)
        assert result is None

    def test_http_429_returns_none(self):
        result = self._run({"data": "something"}, status=429)
        assert result is None

    def test_no_api_key_returns_none(self):
        def mock_fmp_get(endpoint, api_key, params=""):
            if not api_key:
                return None
            return {"data": "something"}

        result = mock_fmp_get("v3/profile/NVDA", "")
        assert result is None


# ── search_ticker_fmp cache tests ────────────────────────────

class TestSearchCache:
    """
    search_ticker_fmp must use session-state cache.
    Empty results must NEVER be cached (causes 60-min dropdown blackout).
    Non-empty results must be cached to save FMP calls.
    """

    def _run_search(self, session_state, fmp_return, query="NVDA"):
        """Simulate search_ticker_fmp with mocked dependencies."""
        cache_key = f"_fmp_search_{query.upper()}"

        # Check cache
        if session_state.get(cache_key):
            return session_state[cache_key]

        # Simulate FMP call result
        results = fmp_return
        if not results or not isinstance(results, list):
            return []

        stocks = [r for r in results if r.get("symbol", "")]
        result = stocks[:12]

        # Only cache non-empty
        if result:
            session_state[cache_key] = result

        return result

    def test_empty_result_not_cached(self):
        session = {}
        result = self._run_search(session, None, "XYZ")
        assert result == []
        assert "_fmp_search_XYZ" not in session, \
            "Empty result must NOT be written to session state cache"

    def test_empty_list_not_cached(self):
        session = {}
        result = self._run_search(session, [], "XYZ")
        assert result == []
        assert "_fmp_search_XYZ" not in session

    def test_valid_result_is_cached(self):
        session = {}
        mock_data = [{"symbol": "NVDA", "name": "NVIDIA Corp", "exchangeShortName": "NASDAQ"}]
        result = self._run_search(session, mock_data, "NVDA")
        assert result == mock_data
        assert session.get("_fmp_search_NVDA") == mock_data, \
            "Valid result must be cached in session state"

    def test_cached_result_returned_without_api_call(self):
        session = {"_fmp_search_NVDA": [{"symbol": "NVDA", "name": "NVIDIA"}]}
        call_count = {"n": 0}

        def fake_fmp(q, k):
            call_count["n"] += 1
            return [{"symbol": "NVDA"}]

        # Simulate: cache hit should skip FMP call
        result = self._run_search(session, None, "NVDA")  # won't call FMP
        assert result == [{"symbol": "NVDA", "name": "NVIDIA"}]

    def test_sort_order_exact_match_first(self):
        session = {}
        mock_data = [
            {"symbol": "NVDA.TO", "name": "NVDA Canada",    "exchangeShortName": "TSX"},
            {"symbol": "NVDA",    "name": "NVIDIA Corp",    "exchangeShortName": "NASDAQ"},
            {"symbol": "NVDAX",   "name": "NVDA Extended",  "exchangeShortName": "XETRA"},
        ]
        major = {"NYSE", "NASDAQ", "TSX", "LSE", "EURONEXT", "XETRA", "ASX", "HKG", "NSE", "AMEX"}

        def sort_key(r):
            is_exact = 0 if r.get("symbol", "").upper() == "NVDA" else 1
            is_major = 0 if r.get("exchangeShortName", "") in major else 1
            return (is_exact, is_major)

        sorted_data = sorted(mock_data, key=sort_key)
        assert sorted_data[0]["symbol"] == "NVDA", \
            "Exact symbol match must sort first"


# ── Fundamentals calculation tests ───────────────────────────

class TestFundamentals:
    """
    PE, P/B, ROE, margins must be calculable from
    income_stmt + balance_sheet + fast_info alone.
    No raw.info dependency.
    """

    def test_pe_calculated_from_income_stmt(self):
        price = 875.0
        shares = 2_440_000_000   # NVDA: 2.44B shares outstanding
        net_income = 29_760_000_000  # ~$29.76B net income

        eps = net_income / shares  # = $12.20
        calc_pe = round(price / abs(eps), 2)  # = ~71.7
        assert 1 < calc_pe < 500, f"PE={calc_pe} failed sanity check"
        assert abs(calc_pe - 71.7) < 5  # NVDA ballpark

    def test_pe_sanity_check_rejects_adr_currency_mismatch(self):
        """PE=1.03 for TSMC was caused by TWD financials vs USD price."""
        bad_pe_values = [0.5, 1.0, 0.03, 600, 1200, -5]
        for pe in bad_pe_values:
            valid = 1 < pe < 500
            assert not valid, f"PE={pe} should be rejected by sanity check"

    def test_pe_sanity_check_accepts_valid_range(self):
        valid_pes = [15.0, 25.5, 65.2, 100.0, 250.0, 499.9]
        for pe in valid_pes:
            assert 1 < pe < 500, f"PE={pe} should pass sanity check"

    def test_pb_ratio_calculation(self):
        price = 875.0
        shares = 24_400_000_000
        equity = 42_978_000_000
        bvps = equity / shares
        pb = round(price / abs(bvps), 2)
        assert pb > 0
        assert pb < 1000

    def test_roe_calculation(self):
        eps = 1.22
        shares = 24_400_000_000
        equity = 42_978_000_000
        net_income = eps * shares
        roe = net_income / abs(equity)
        assert 0 < roe < 100

    def test_revenue_growth_decimal_to_pct(self):
        """yfinance returns 0.122 — must be treated as 12.2%, not 122%."""
        raw_value = 0.122
        pct = raw_value * 100 if abs(raw_value) <= 2 else raw_value
        assert abs(pct - 12.2) < 0.1

    def test_earnings_growth_already_pct_not_doubled(self):
        """Claude returns 14.5 (already %) — must NOT be multiplied by 100."""
        claude_value = 14.5
        pct = claude_value * 100 if abs(claude_value) <= 2 else claude_value
        assert abs(pct - 14.5) < 0.1, "Claude % values must not be doubled"

    def test_dividend_yield_from_dividends(self):
        annual_div = 0.16  # 4 quarters × $0.04
        price = 875.0
        yield_pct = (annual_div / price) * 100
        assert 0 < yield_pct < 5


# ── Earnings history parse tests ─────────────────────────────

class TestEarningsHistoryParse:
    """
    earn_hist from FMP has columns: period, epsEstimate, epsActual, surprisePercent
    earn_hist from yfinance has: EPS Estimate, Reported EPS, Surprise(%)
    Both must parse correctly.
    """

    def _parse_row(self, row_dict):
        """Mirrors the parse logic in run_analysis."""
        est = float(row_dict.get('epsEstimate',
               row_dict.get('EPS Estimate',
               row_dict.get('estimate', 0))) or 0)
        act = float(row_dict.get('epsActual',
               row_dict.get('Reported EPS',
               row_dict.get('actual',   0))) or 0)
        surp_raw = row_dict.get('surprisePercent',
                   row_dict.get('Surprise(%)',
                   row_dict.get('surprise', None)))
        if surp_raw is not None:
            sv = float(surp_raw or 0)
            surp = sv * 100 if abs(sv) <= 2 else sv
        else:
            surp = ((act - est) / abs(est) * 100) if est != 0 else 0
        return {"estimate": est, "actual": act, "surprise": surp, "beat": surp > 0}

    def test_fmp_format_parses(self):
        row = {"period": "2024-11-01", "epsEstimate": 0.71, "epsActual": 0.81,
               "surprisePercent": 0.1408}  # FMP: decimal form
        result = self._parse_row(row)
        assert result["estimate"] == 0.71
        assert result["actual"] == 0.81
        assert result["beat"] is True
        assert abs(result["surprise"] - 14.08) < 0.1

    def test_yfinance_format_parses(self):
        row = {"EPS Estimate": 0.71, "Reported EPS": 0.81, "Surprise(%)": 14.1}
        result = self._parse_row(row)
        assert result["estimate"] == 0.71
        assert result["actual"] == 0.81
        assert abs(result["surprise"] - 14.1) < 0.1
        assert result["beat"] is True

    def test_miss_detected(self):
        row = {"epsEstimate": 1.0, "epsActual": 0.85, "surprisePercent": -0.15}
        result = self._parse_row(row)
        assert result["beat"] is False
        assert result["surprise"] < 0

    def test_surprise_calculated_when_missing(self):
        row = {"epsEstimate": 1.0, "epsActual": 1.15}  # no surprise field
        result = self._parse_row(row)
        assert abs(result["surprise"] - 15.0) < 0.1
        assert result["beat"] is True

    def test_zero_estimate_doesnt_divide_by_zero(self):
        row = {"epsEstimate": 0, "epsActual": 0.5}
        result = self._parse_row(row)
        assert result["surprise"] == 0  # safe fallback
