"""OAuth2 refresh-token flow, with the concurrent-refresh race actually handled.

The bug this file exists to not have
-------------------------------------
The naive version looks correct:

    if token.expired():
        token = refresh()
    return token.access_token

Under one thread it is correct. Under eight workers it is not. All eight see
an expired token in the same millisecond, all eight POST to the token
endpoint, and now you have eight refreshes in flight. Depending on the
provider, one of three things happens:

* Google/Okta style -- refresh tokens rotate. Refresh #1 returns a new refresh
  token and invalidates the old one. Refreshes #2-#8 are using the old one, so
  they fail with ``invalid_grant``, and worse, several providers treat a reused
  refresh token as a breach signal and revoke the whole grant. Your integration
  is now dead until a human re-consents.
* Salesforce style -- you burn your token-endpoint rate limit and get 429ed on
  the one call you cannot afford to lose.
* Best case -- seven wasted round trips and a last-writer-wins scribble over
  the stored token.

The fix is single-flight: one refresh happens, everybody else waits for it and
uses the result. That is what :meth:`TokenManager.access_token` does, and
``tests/test_oauth.py`` proves it with real threads rather than asserting it in
a comment.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Callable

from .errors import AuthError, PermanentError
from .http import Client


@dataclass
class Token:
    access_token: str
    refresh_token: str
    expires_at: float
    scope: str = ""

    def expired(self, *, now: float, skew: float) -> bool:
        """``skew`` is the early-expiry margin, in seconds.

        Treating a token as expired slightly before it really is buys you the
        request's own flight time plus any clock drift between you and the
        provider. Without it you will periodically send a token that was valid
        when you checked and expired in transit, and get a 401 you cannot
        reproduce.
        """
        return now >= (self.expires_at - skew)


class TokenStore:
    """Where the token lives between process restarts.

    In production this is a row in Postgres or a Secrets Manager entry, and it
    needs a lock or a compare-and-swap so two *processes* cannot clobber each
    other the way two threads would. The in-memory implementation below is for
    tests and single-process runs.
    """

    def load(self) -> Token | None:
        raise NotImplementedError

    def save(self, token: Token) -> None:
        raise NotImplementedError


@dataclass
class MemoryTokenStore(TokenStore):
    token: Token | None = None
    saves: int = 0

    def load(self):
        return self.token

    def save(self, token):
        self.token = token
        self.saves += 1


@dataclass
class TokenManager:
    """Hands out a valid access token, refreshing at most once concurrently."""

    client: Client
    token_url: str
    client_id: str
    client_secret: str
    store: TokenStore
    # 60s default margin. Anything under ~30s is too tight once you account for
    # a slow hop and a machine whose clock is a few seconds out.
    expiry_skew: float = 60.0
    clock: Callable[[], float] = time.time

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    refresh_calls: int = 0

    def access_token(self) -> str:
        token = self.store.load()
        if token is None:
            raise AuthError("no token stored; run the authorisation code flow first")
        if not token.expired(now=self.clock(), skew=self.expiry_skew):
            return token.access_token

        # Single-flight. The second check inside the lock is the whole point:
        # threads that queued on the lock while another was refreshing must
        # re-read the store and find the *new* token, not refresh again.
        with self._lock:
            token = self.store.load()
            if token is not None and not token.expired(now=self.clock(), skew=self.expiry_skew):
                return token.access_token
            refreshed = self._refresh(token)
            self.store.save(refreshed)
            return refreshed.access_token

    def _refresh(self, token: Token | None) -> Token:
        if token is None or not token.refresh_token:
            raise AuthError("no refresh token; the grant must be re-authorised by a human")

        self.refresh_calls += 1
        payload = urllib.parse.urlencode(
            {
                "grant_type": "refresh_token",
                "refresh_token": token.refresh_token,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            }
        ).encode()

        try:
            resp = self.client.request(
                "POST",
                self.token_url,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                body=payload,
                # A token refresh is safe to replay: worst case the provider
                # issues a token we then discard. Being unable to retry a 503
                # on the token endpoint means one blip takes the whole
                # integration down.
                idempotent=True,
            )
        except AuthError as exc:
            raise AuthError(
                "refresh rejected (" + str(exc) + "); treat as re-consent required, not as transient"
            ) from exc

        data = resp.json()
        if "access_token" not in data:
            raise PermanentError("token endpoint returned no access_token: " + json.dumps(data)[:200])

        return Token(
            access_token=data["access_token"],
            # Rotating providers return a new refresh token and invalidate the
            # old one. Non-rotating providers omit the field entirely. Falling
            # back to the old value handles both; dropping it handles neither.
            refresh_token=data.get("refresh_token") or token.refresh_token,
            expires_at=self.clock() + float(data.get("expires_in", 3600)),
            scope=data.get("scope", token.scope),
        )

    def authorized_headers(self) -> dict[str, str]:
        return {"Authorization": "Bearer " + self.access_token()}
