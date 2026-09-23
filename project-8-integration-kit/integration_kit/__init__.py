"""Integration Kit — three third-party APIs, three auth models, one schema.

Nothing in this package imports a third-party library. Everything that would
touch the network goes through :class:`integration_kit.http.Transport`, so the
tests exercise the real code paths with a fake transport instead of mocking
out the logic under test.
"""

__all__ = ["errors", "http", "oauth", "webhooks", "pagination", "schema"]
__version__ = "1.0.0"
