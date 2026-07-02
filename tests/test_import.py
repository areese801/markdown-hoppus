"""
Smoke test: the hoppus package imports and exposes a version string.
"""

import hoppus


def test_import_and_version() -> None:
    """
    Assert the package imports and ``__version__`` is a non-empty string.
    """
    assert isinstance(hoppus.__version__, str)
    assert hoppus.__version__
