#!/usr/bin/env python3
"""Discover the standard-library test suite and reject an empty run."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    suite = unittest.defaultTestLoader.discover(
        start_dir=str(ROOT / "tests"),
        pattern="test_*.py",
        top_level_dir=str(ROOT),
    )
    count = suite.countTestCases()
    if count == 0:
        print("error: no test_*.py cases were discovered", file=sys.stderr)
        return 1
    print(f"Discovered {count} tests")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
