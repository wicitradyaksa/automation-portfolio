"""Every failure this package can produce, as a type you can catch.

The split that matters is :class:`RetryableError` vs :class:`PermanentError`.
The retry loop in ``http.py`` only ever retries the first one. Getting that
boundary wrong is how a client ends up hammering an endpoint that is returning
401 because the credential was revoked.
"""


class IntegrationError(Exception):
    """Base class. Catch this if you do not care which one it was."""


class RetryableError(IntegrationError):
    """Transient. Trying again later is reasonable.

    ``retry_after`` is the server's own instruction, in seconds, when it gave
    one. ``None`` means "we decide", i.e. fall back to exponential backoff.
    """

    def __init__(self, message: str, *, retry_after: float | None = None, status: int | None = None):
        super().__init__(message)
        self.retry_after = retry_after
        self.status = status


class PermanentError(IntegrationError):
    """Trying again with the same input will fail the same way."""

    def __init__(self, message: str, *, status: int | None = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


class AuthError(PermanentError):
    """401/403. The credential is wrong, expired beyond refresh, or revoked."""


class RateLimited(RetryableError):
    """429 specifically, kept separate so callers can count it on its own."""


class SignatureError(PermanentError):
    """A webhook whose HMAC did not verify. Never retry; never process."""


class ReplayError(PermanentError):
    """A webhook whose timestamp is outside the accepted window."""


class PaginationError(IntegrationError):
    """The cursor loop detected something it refuses to keep walking."""
