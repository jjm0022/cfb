"""Run the dashboard's JavaScript unit tests under Node as part of pytest."""

from dashboard_helpers import run_node


def test_dashboard_javascript_unit_tests():
    run_node("--test", "tests/js/*.test.mjs")
