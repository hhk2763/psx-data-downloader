"""HTTP against the PSX portal: one request at a time, politely.

This layer knows URLs and bytes. It has no idea where files end up -- that is
``store.py``. It never writes anything.

The validity rule comes straight from CLAUDE.md's verified facts: a file counts
only when the status is 200 *and* the content type is octet-stream. A missing
file answers 404 with a ~47 KB HTML error page, which must never be saved as a
``.pdf``.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from typing import Callable

import requests

from psx.manifest import ERROR, MISSING, OK, Status

log = logging.getLogger("psx.fetcher")

#: The only content type that marks a real download.
OCTET_STREAM = "application/octet-stream"


@dataclass(frozen=True, slots=True)
class FetchOutcome:
    """The result of trying one URL. ``body`` is set only when ``status`` is ok."""

    status: Status
    http_code: int | None = None
    content_type: str | None = None
    body: bytes | None = None
    attempts: int = 1
    error_msg: str | None = None

    @property
    def is_ok(self) -> bool:
        return self.status == OK


class Fetcher:
    """A polite, single-threaded HTTP client.

    Politeness lives here rather than in the caller, so every code path that
    can reach the site is rate limited by construction: ``delay_seconds`` plus
    jitter is waited out before *every* request, retries included.
    """

    def __init__(
        self,
        *,
        user_agent: str,
        delay_seconds: float = 1.5,
        jitter_seconds: float = 0.5,
        timeout: float = 30.0,
        retries: int = 3,
        backoff_seconds: float = 2.0,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
    ) -> None:
        self.delay_seconds = delay_seconds
        self.jitter_seconds = jitter_seconds
        self.timeout = timeout
        self.retries = max(1, retries)
        self.backoff_seconds = backoff_seconds
        self._sleep = sleep
        self._rng = rng or random.Random()
        self._owns_session = session is None
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.9",
                "Connection": "keep-alive",
            }
        )

    # --- lifecycle ---------------------------------------------------------

    def __enter__(self) -> Fetcher:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_session:
            self.session.close()

    # --- politeness --------------------------------------------------------

    def _wait(self) -> None:
        delay = self.delay_seconds
        if self.jitter_seconds > 0:
            delay += self._rng.uniform(0.0, self.jitter_seconds)
        if delay > 0:
            self._sleep(delay)

    def _backoff(self, attempt: int) -> None:
        """Exponential: 2s, 4s, 8s ... on top of the normal delay."""
        if self.backoff_seconds > 0:
            self._sleep(self.backoff_seconds * (2 ** (attempt - 1)))

    # --- the one public call -----------------------------------------------

    def fetch(self, url: str) -> FetchOutcome:
        """Try one URL and classify the answer. Never raises for HTTP problems."""
        last_error: str | None = None

        for attempt in range(1, self.retries + 1):
            self._wait()
            try:
                response = self.session.get(url, timeout=self.timeout, allow_redirects=True)
            except requests.Timeout as exc:
                last_error = f"timeout after {self.timeout}s: {exc}"
                log.debug("%s attempt %d/%d: %s", url, attempt, self.retries, last_error)
                if attempt < self.retries:
                    self._backoff(attempt)
                    continue
                return FetchOutcome(status=ERROR, attempts=attempt, error_msg=last_error)
            except requests.RequestException as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                log.debug("%s attempt %d/%d: %s", url, attempt, self.retries, last_error)
                if attempt < self.retries:
                    self._backoff(attempt)
                    continue
                return FetchOutcome(status=ERROR, attempts=attempt, error_msg=last_error)

            code = response.status_code
            content_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()

            # A real file.
            if code == 200 and content_type == OCTET_STREAM:
                return FetchOutcome(
                    status=OK,
                    http_code=code,
                    content_type=content_type,
                    body=response.content,
                    attempts=attempt,
                )

            # Weekend, holiday, or a date before the dataset existed.
            # Not an error and not worth retrying.
            if code == 404:
                return FetchOutcome(
                    status=MISSING,
                    http_code=code,
                    content_type=content_type or None,
                    attempts=attempt,
                )

            # Server-side trouble: back off and try again.
            if 500 <= code < 600:
                last_error = f"HTTP {code}"
                log.debug("%s attempt %d/%d: %s", url, attempt, self.retries, last_error)
                if attempt < self.retries:
                    self._backoff(attempt)
                    continue
                return FetchOutcome(
                    status=ERROR,
                    http_code=code,
                    content_type=content_type or None,
                    attempts=attempt,
                    error_msg=last_error,
                )

            # 200 with an HTML body (a login wall or an error page), a redirect
            # to something unexpected, or another 4xx. Never save this; flag it
            # so the next run retries and the user sees it.
            if code == 200:
                message = f"expected {OCTET_STREAM}, got {content_type or 'no content-type'}"
            else:
                message = f"HTTP {code}"
            return FetchOutcome(
                status=ERROR,
                http_code=code,
                content_type=content_type or None,
                attempts=attempt,
                error_msg=message,
            )

        # Unreachable: every branch above returns or continues.
        return FetchOutcome(status=ERROR, attempts=self.retries, error_msg=last_error)  # pragma: no cover


def build_fetcher(settings, *, session: requests.Session | None = None, **kwargs) -> Fetcher:
    """Construct a Fetcher from a :class:`psx.config.Settings`."""
    return Fetcher(
        user_agent=settings.user_agent,
        delay_seconds=settings.delay_seconds,
        jitter_seconds=settings.jitter_seconds,
        timeout=settings.timeout,
        retries=settings.retries,
        backoff_seconds=settings.backoff_seconds,
        session=session,
        **kwargs,
    )
