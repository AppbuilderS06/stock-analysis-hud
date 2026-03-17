"""
test_static_analysis.py
Static analysis of app.py — no runtime, no imports, no API calls.
Catches structural bugs before they ever deploy.
"""
import ast
import re
import py_compile
import pytest


def test_syntax_compiles(app_source):
    """app.py must parse as valid Python. Zero tolerance."""
    try:
        ast.parse(app_source)
    except SyntaxError as e:
        pytest.fail(f"SyntaxError in app.py line {e.lineno}: {e.msg}")


def test_py_compile_clean(tmp_path):
    """py_compile catches a wider class of errors than ast.parse."""
    import shutil, os
    src = os.path.join(os.path.dirname(__file__), "..", "app.py")
    dst = tmp_path / "app.py"
    shutil.copy(src, dst)
    try:
        py_compile.compile(str(dst), doraise=True)
    except py_compile.PyCompileError as e:
        pytest.fail(f"py_compile failed: {e}")


def test_no_duplicate_global_constants(app_source):
    """
    MULTI_LISTED, INFO_LINKS, VERDICT_COLORS must each be defined exactly once.
    Duplicate definitions caused silent bugs when first def had missing fields.
    """
    tree = ast.parse(app_source)
    top_level_assigns = [
        node.targets[0].id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    ]
    for name in ["MULTI_LISTED", "INFO_LINKS", "VERDICT_COLORS"]:
        count = top_level_assigns.count(name)
        assert count == 1, (
            f"'{name}' is defined {count} times — expected exactly 1. "
            f"Duplicate definitions cause silent overwrites."
        )


def test_no_svg_in_st_markdown(app_source):
    """
    Streamlit sanitizes SVG in st.markdown — use components.v1.html() instead.
    Any <svg in a st.markdown call will render as raw escaped text.
    """
    # Find st.markdown calls that contain literal <svg
    pattern = re.compile(r'st\.markdown\s*\(', re.MULTILINE)
    lines = app_source.split("\n")
    violations = []
    in_markdown = False
    depth = 0
    current_start = None

    for i, line in enumerate(lines, 1):
        if "st.markdown(" in line and not in_markdown:
            in_markdown = True
            current_start = i
            depth = line.count("(") - line.count(")")
        elif in_markdown:
            depth += line.count("(") - line.count(")")

        if in_markdown and "<svg" in line:
            violations.append(f"Line {i}: SVG tag found inside st.markdown block starting at line {current_start}")

        if in_markdown and depth <= 0:
            in_markdown = False

    assert len(violations) == 0, (
        "SVG tags found in st.markdown calls:\n" + "\n".join(violations) +
        "\nFix: use st.components.v1.html() instead."
    )


def test_no_st_form_usage(app_source):
    """
    st.form blocks live search updates — explicitly forbidden.
    Dropdown must use bare st.text_input that reruns on every keystroke.
    """
    matches = re.findall(r'\bst\.form\b', app_source)
    assert len(matches) == 0, (
        f"Found {len(matches)} st.form usage(s). "
        "st.form prevents dropdown from updating on keypress — remove it."
    )


def test_cache_version_consistent(app_source):
    """
    fetch_ticker_data definition _v=N must match every call site _v=N.
    Mismatch means cached stale data is served after code changes.
    """
    defn_match = re.search(
        r'def fetch_ticker_data\s*\([^)]*_v\s*=\s*(\d+)', app_source
    )
    call_matches = re.findall(
        r'fetch_ticker_data\s*\([^)]*_v\s*=\s*(\d+)', app_source
    )
    assert defn_match, "Could not find fetch_ticker_data definition with _v parameter"
    assert len(call_matches) >= 1, "Could not find fetch_ticker_data call with _v parameter"

    defn_v = defn_match.group(1)
    for call_v in call_matches:
        assert defn_v == call_v, (
            f"Cache version mismatch: definition has _v={defn_v} "
            f"but a call site has _v={call_v}. "
            "Increment BOTH when changing fetch_ticker_data."
        )


def test_multi_listed_schema_complete(app_source):
    """
    Every MULTI_LISTED entry must have ticker, name, exchange, currency.
    Missing 'currency' caused KeyError crash when dropdown rendered.
    """
    # Extract the MULTI_LISTED dict source by finding it in app_source
    # We'll do this by importing the constants directly
    import sys, os, types

    # Build a minimal module with just the MULTI_LISTED definition
    # by extracting lines between MULTI_LISTED = { and the closing }
    start = app_source.find("MULTI_LISTED = {")
    assert start != -1, "MULTI_LISTED definition not found"

    # Find matching closing brace
    depth = 0
    end = start
    for i, ch in enumerate(app_source[start:], start):
        if ch == "{": depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    ml_source = app_source[start:end]
    local_ns = {}
    exec(ml_source, {}, local_ns)
    ml = local_ns["MULTI_LISTED"]

    required_keys = {"ticker", "name", "exchange", "currency"}
    for key, opts in ml.items():
        for i, opt in enumerate(opts):
            missing = required_keys - set(opt.keys())
            assert not missing, (
                f"MULTI_LISTED['{key}'][{i}] missing keys: {missing}\n"
                f"Entry: {opt}"
            )


def test_no_raw_ticker_in_cache_return(app_source):
    """
    yf.Ticker objects are not pickle-serializable.
    Returning them from @st.cache_data causes crash on cache write.
    """
    # Find the return statement of fetch_ticker_data
    lines = app_source.split("\n")
    in_fetch = False
    for i, line in enumerate(lines, 1):
        if "def fetch_ticker_data(" in line:
            in_fetch = True
        if in_fetch and line.strip().startswith("return ") and "raw" in line:
            # Check if it's returning the raw yf.Ticker object
            if re.search(r'"raw"\s*:', line) or re.search(r"'raw'\s*:", line):
                pytest.fail(
                    f"Line {i}: fetch_ticker_data return dict contains 'raw' key. "
                    "yf.Ticker is not pickle-serializable — remove it from return."
                )
        if in_fetch and line.strip().startswith("def ") and "fetch_ticker_data" not in line:
            in_fetch = False


def test_fmp_error_guard_exists(app_source):
    """
    _fmp_get must check for FMP error response body.
    Without this guard, rate-limit responses silently return as valid data.
    """
    assert "Error Message" in app_source, (
        "_fmp_get must check for 'Error Message' key in FMP response. "
        "FMP returns HTTP 200 with error body on rate limit."
    )
    assert '"message"' in app_source or "'message'" in app_source, (
        "_fmp_get must also check for 'message' key (FMP auth error format)."
    )


def test_components_html_used_for_svg(app_source):
    """
    SVG rendering must use components.v1.html or components.html.
    """
    assert "components.html(" in app_source, (
        "SVG diagram must be rendered via components.html(). "
        "st.markdown strips SVG tags."
    )


def test_no_st_cache_data_on_search(app_source):
    """
    search_ticker_fmp must NOT use @st.cache_data decorator.
    @st.cache_data caches empty [] results for 60 min, breaking dropdown.
    Uses session_state cache instead (never caches empty results).
    """
    lines = app_source.split("\n")
    for i, line in enumerate(lines, 1):
        if "@st.cache_data" in line:
            # Next non-empty line should be the function def
            for j in range(i, min(i + 5, len(lines))):
                if "def " in lines[j]:
                    assert "search_ticker_fmp" not in lines[j], (
                        f"Line {j+1}: search_ticker_fmp must NOT use @st.cache_data. "
                        "Use session_state cache to avoid caching empty results."
                    )
                    break
