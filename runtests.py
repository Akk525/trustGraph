"""
TrustGraph test runner.

Uses stdlib unittest to avoid the broken web3/ethpm pytest11 entry-point
conflict present in this environment's site-packages.

Usage:
    python runtests.py            # run all tests
    python runtests.py -v         # verbose
"""
import sys
import unittest

loader = unittest.TestLoader()
suite = loader.discover(start_dir="tests", pattern="test_*.py")

runner = unittest.TextTestRunner(verbosity=2 if "-v" in sys.argv else 1)
result = runner.run(suite)
sys.exit(0 if result.wasSuccessful() else 1)
