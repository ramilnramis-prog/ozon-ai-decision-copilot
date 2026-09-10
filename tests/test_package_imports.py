"""Smoke tests for the approved package scaffold."""

from importlib import import_module


PACKAGES = (
    "app",
    "app.core",
    "app.domain",
    "app.providers",
    "app.analytics",
    "app.decisions",
    "app.ai",
    "app.services",
    "app.ui",
)


def test_approved_packages_import() -> None:
    for package_name in PACKAGES:
        module = import_module(package_name)
        assert module.__name__ == package_name
