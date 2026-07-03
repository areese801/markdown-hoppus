"""
Smoke test: the hoppus package imports and exposes a version string.
"""

import importlib.metadata

import hoppus


def test_import_and_version() -> None:
    """
    Assert the package imports and ``__version__`` is a non-empty string.
    """
    assert isinstance(hoppus.__version__, str)
    assert hoppus.__version__


def test_distribution_version_matches_dunder_version() -> None:
    """
    Assert installed distribution metadata matches ``hoppus.__version__``.

    Guards against dual-source drift now that pyproject.toml derives its
    version dynamically from ``src/hoppus/__init__.py``.
    """
    assert importlib.metadata.version("markdown-hoppus") == hoppus.__version__
