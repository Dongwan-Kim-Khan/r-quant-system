"""
Risk-parameter SSOT: STOP_LOSS_PCT / TAKE_PROFIT_PCT live in constants.py.
Production code must call derive_* helpers instead of 0.95 / 1.15 / 1.08 literals.
"""
import os
import re
import sys
import unittest

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from al_sangmoo.core.constants import (
    HARD_STOP_PCT,
    PARTIAL_TP_MULT,
    PARTIAL_TP_PCT,
    STOP_LOSS_MULT,
    STOP_LOSS_PCT,
    TAKE_PROFIT_MULT,
    TAKE_PROFIT_PCT,
    TRAILING_ACTIVATE_PCT,
    derive_partial_tp_price,
    derive_stop_price,
    derive_target_price,
)

MAGIC_PRICE_RE = re.compile(r"\*\s*(0\.93|0\.95|1\.18|1\.15|1\.08)\b")
SCAN_ROOTS = [
    os.path.join(PROJECT_ROOT, "al_sangmoo"),
    os.path.join(PROJECT_ROOT, "al_sangmoo_daily_bot.py"),
]
ALLOW_FILES = {
    os.path.normpath(os.path.join(PROJECT_ROOT, "al_sangmoo", "core", "constants.py")),
}


class TestRiskConstantsSsot(unittest.TestCase):
    def test_constitution_values(self):
        self.assertEqual(STOP_LOSS_PCT, -0.07)
        self.assertEqual(TAKE_PROFIT_PCT, 0.18)
        self.assertEqual(PARTIAL_TP_PCT, 0.08)
        self.assertEqual(HARD_STOP_PCT, 7.0)
        self.assertEqual(TRAILING_ACTIVATE_PCT, 18.0)
        self.assertAlmostEqual(STOP_LOSS_MULT, 0.93)
        self.assertAlmostEqual(TAKE_PROFIT_MULT, 1.18)
        self.assertAlmostEqual(PARTIAL_TP_MULT, 1.08)

    def test_derive_prices(self):
        self.assertEqual(derive_stop_price(100.0), 93.0)
        self.assertEqual(derive_target_price(100.0), 118.0)
        self.assertEqual(derive_partial_tp_price(100.0), 108.0)
        self.assertEqual(derive_stop_price(125.0), 116.25)
        self.assertEqual(derive_target_price(125.0), 147.5)

    def test_production_python_has_no_magic_multipliers(self):
        hits = []
        for root in SCAN_ROOTS:
            if os.path.isfile(root):
                files = [root]
            else:
                files = []
                for dirpath, _, filenames in os.walk(root):
                    for name in filenames:
                        if name.endswith(".py"):
                            files.append(os.path.join(dirpath, name))
            for path in files:
                if os.path.normpath(path) in ALLOW_FILES:
                    continue
                with open(path, encoding="utf-8") as fh:
                    for lineno, line in enumerate(fh, 1):
                        if line.lstrip().startswith("#"):
                            continue
                        if MAGIC_PRICE_RE.search(line):
                            hits.append(f"{os.path.relpath(path, PROJECT_ROOT)}:{lineno}: {line.strip()}")
        self.assertEqual(hits, [], "magic stop/target multipliers remain:\n" + "\n".join(hits))

    def test_frontend_js_matches_python(self):
        js_path = os.path.join(PROJECT_ROOT, "frontend", "js", "constants.js")
        with open(js_path, encoding="utf-8") as fh:
            text = fh.read()
        sl = re.search(r"export const STOP_LOSS_PCT = ([-\d.]+);", text)
        tp = re.search(r"export const TAKE_PROFIT_PCT = ([-\d.]+);", text)
        self.assertIsNotNone(sl)
        self.assertIsNotNone(tp)
        self.assertEqual(float(sl.group(1)), STOP_LOSS_PCT)
        self.assertEqual(float(tp.group(1)), TAKE_PROFIT_PCT)


if __name__ == "__main__":
    unittest.main()
