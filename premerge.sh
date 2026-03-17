#!/bin/bash
# premerge.sh — Run before every push to GitHub
# Blocks on any failure. All checks must pass.

set -e
PASS=0
FAIL=0
ROOT="$(cd "$(dirname "$0")"; pwd)"

green() { echo -e "\033[32m$1\033[0m"; }
red()   { echo -e "\033[31m$1\033[0m"; }
bold()  { echo -e "\033[1m$1\033[0m"; }

bold "=== STOCK ANALYSIS HUD — PRE-MERGE CHECKLIST ==="
echo ""

# ① Syntax
bold "① Syntax check"
if python3 -m py_compile "$ROOT/app.py"; then
  green "  ✅ PASS — app.py compiles clean"
  PASS=$((PASS+1))
else
  red "  ❌ FAIL — SyntaxError in app.py"
  FAIL=$((FAIL+1))
fi

# ② No duplicate globals
bold "② No duplicate global definitions"
python3 - << 'PYCHECK'
import ast, sys
src = open("app.py").read()
tree = ast.parse(src)
names = [n.targets[0].id for n in ast.walk(tree)
         if isinstance(n, ast.Assign) and len(n.targets)==1
         and isinstance(n.targets[0], ast.Name)]
failed = False
for name in ["MULTI_LISTED", "INFO_LINKS", "VERDICT_COLORS"]:
    c = names.count(name)
    if c != 1:
        print(f"  ❌ FAIL: {name} defined {c}x — expected 1")
        failed = True
    else:
        print(f"  ✅ {name} defined once")
if failed:
    sys.exit(1)
PYCHECK
if [ $? -eq 0 ]; then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); fi

# ③ Cache version consistent
bold "③ Cache version consistent"
python3 - << 'PYCHECK'
import re, sys
src = open("app.py").read()
d = re.search(r'def fetch_ticker_data\([^)]*_v\s*=\s*(\d+)', src)
c = re.search(r'fetch_ticker_data\([^)]*_v\s*=\s*(\d+)', src)
if not d or not c:
    print("  ❌ FAIL: Could not find _v parameter")
    sys.exit(1)
if d.group(1) != c.group(1):
    print(f"  ❌ FAIL: definition _v={d.group(1)} ≠ call _v={c.group(1)}")
    sys.exit(1)
print(f"  ✅ Cache version _v={d.group(1)} consistent")
PYCHECK
if [ $? -eq 0 ]; then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); fi

# ④ No SVG in st.markdown
bold "④ No SVG in st.markdown"
python3 - << 'PYCHECK'
import re, sys
src = open("app.py").read()
# Simple check: any line with both st.markdown and <svg
lines = src.split("\n")
hits = [f"Line {i+1}" for i, l in enumerate(lines)
        if "st.markdown" in l and "<svg" in l]
if hits:
    print(f"  ❌ FAIL: SVG in st.markdown at: {hits}")
    sys.exit(1)
print("  ✅ No SVG in st.markdown")
PYCHECK
if [ $? -eq 0 ]; then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); fi

# ⑤ No st.form
bold "⑤ No st.form usage"
if grep -n "st\.form\b" app.py; then
  red "  ❌ FAIL: st.form found — breaks live search dropdown"
  FAIL=$((FAIL+1))
else
  green "  ✅ No st.form"
  PASS=$((PASS+1))
fi

# ⑥ FMP error guard in place
bold "⑥ FMP rate limit guard"
if grep -q "Error Message" app.py && grep -q '"message"' app.py; then
  green "  ✅ FMP error guard present"
  PASS=$((PASS+1))
else
  red "  ❌ FAIL: FMP rate limit guard missing in _fmp_get()"
  FAIL=$((FAIL+1))
fi

# ⑦ components.html used for SVG
bold "⑦ SVG uses components.html"
if grep -q "components.html(" app.py; then
  green "  ✅ components.html() found"
  PASS=$((PASS+1))
else
  red "  ❌ FAIL: SVG diagram must use components.html()"
  FAIL=$((FAIL+1))
fi

# ⑧ MULTI_LISTED has currency field
bold "⑧ MULTI_LISTED schema complete"
python3 - << 'PYCHECK'
import re, ast, sys
src = open("app.py").read()
start = src.find("MULTI_LISTED = {")
depth = 0
end = start
for i, ch in enumerate(src[start:], start):
    if ch == "{": depth += 1
    elif ch == "}":
        depth -= 1
        if depth == 0:
            end = i + 1
            break
local_ns = {}
exec(src[start:end], {}, local_ns)
ml = local_ns["MULTI_LISTED"]
failed = False
for key, opts in ml.items():
    for opt in opts:
        for field in ["ticker", "name", "exchange", "currency"]:
            if field not in opt:
                print(f"  ❌ FAIL: MULTI_LISTED['{key}'] missing '{field}'")
                failed = True
if not failed:
    print(f"  ✅ All {sum(len(v) for v in ml.values())} MULTI_LISTED entries valid")
if failed:
    sys.exit(1)
PYCHECK
if [ $? -eq 0 ]; then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); fi

# ⑨ Run test suite
bold "⑨ Test suite"
if python3 -m pytest tests/ -v --tb=short -q 2>&1; then
  green "  ✅ All tests passed"
  PASS=$((PASS+1))
else
  red "  ❌ FAIL: Test suite has failures"
  FAIL=$((FAIL+1))
fi

# ⑩ Line count warning
bold "⑩ File size"
LINES=$(wc -l < app.py)
if [ "$LINES" -gt 3500 ]; then
  echo "  ⚠  WARNING: app.py is $LINES lines — consider splitting into modules"
else
  green "  ✅ $LINES lines (under 3500 threshold)"
  PASS=$((PASS+1))
fi

# Summary
echo ""
bold "=== RESULTS: $PASS passed, $FAIL failed ==="
if [ "$FAIL" -gt 0 ]; then
  red "❌ Pre-merge checks FAILED — do not push"
  exit 1
else
  green "✅ All checks passed — safe to push"
  exit 0
fi
