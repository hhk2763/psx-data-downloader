"""Step 5 — HTTP classification, retries, and politeness.

Every request is mocked; no test here touches the network.
"""

from __future__ import annotations

import random

import pytest
import requests
import responses

from psx.fetcher import OCTET_STREAM, Fetcher, build_fetcher
from psx.manifest import ERROR, MISSING, OK

URL = "https://dps.psx.com.pk/download/nd_accepted/2026-09-17.pdf"

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/129.0.0.0"

#: What a missing file really returns: a ~47 KB HTML error page.
ERROR_PAGE = b"<html><body>Not found</body></html>" * 1000


@pytest.fixture
def slept() -> list[float]:
    return []


@pytest.fixture
def fetcher(slept: list[float]) -> Fetcher:
    """A fetcher whose clock is fake, so tests stay instant but still assert waits."""
    return Fetcher(
        user_agent=UA,
        delay_seconds=1.5,
        jitter_seconds=0.5,
        timeout=30.0,
        retries=3,
        backoff_seconds=2.0,
        sleep=slept.append,
        rng=random.Random(0),
    )


# --- classification ---------------------------------------------------------


@responses.activate
def test_200_octet_stream_is_ok_with_the_exact_bytes(fetcher: Fetcher) -> None:
    body = b"%PDF-1.4 real file"
    responses.add(
        responses.GET,
        URL,
        body=body,
        status=200,
        content_type=OCTET_STREAM,
        headers={"content-disposition": "attachment; filename=nd_acc_202617sep.pdf"},
    )

    outcome = fetcher.fetch(URL)

    assert outcome.status == OK
    assert outcome.is_ok
    assert outcome.body == body
    assert outcome.http_code == 200
    assert outcome.content_type == OCTET_STREAM
    assert outcome.attempts == 1
    assert outcome.error_msg is None


@responses.activate
def test_content_type_parameters_are_ignored(fetcher: Fetcher) -> None:
    responses.add(responses.GET, URL, body=b"x", status=200, content_type=f"{OCTET_STREAM}; charset=binary")
    assert fetcher.fetch(URL).status == OK


@responses.activate
def test_404_html_is_missing_and_never_keeps_the_error_page(fetcher: Fetcher) -> None:
    responses.add(responses.GET, URL, body=ERROR_PAGE, status=404, content_type="text/html")

    outcome = fetcher.fetch(URL)

    assert outcome.status == MISSING
    assert outcome.http_code == 404
    assert outcome.body is None


@responses.activate
def test_404_is_not_retried(fetcher: Fetcher) -> None:
    responses.add(responses.GET, URL, body=ERROR_PAGE, status=404, content_type="text/html")
    outcome = fetcher.fetch(URL)
    assert outcome.attempts == 1
    assert len(responses.calls) == 1


@responses.activate
def test_200_text_html_is_an_error_and_writes_nothing(fetcher: Fetcher) -> None:
    """A 200 carrying HTML must never be saved as a .pdf."""
    responses.add(responses.GET, URL, body=b"<html>login</html>", status=200, content_type="text/html")

    outcome = fetcher.fetch(URL)

    assert outcome.status == ERROR
    assert outcome.body is None
    assert outcome.http_code == 200
    assert "expected application/octet-stream" in (outcome.error_msg or "")


@responses.activate
def test_200_without_a_content_type_is_an_error(fetcher: Fetcher) -> None:
    responses.add(responses.GET, URL, body=b"mystery", status=200, content_type=None)
    outcome = fetcher.fetch(URL)
    assert outcome.status == ERROR
    assert outcome.body is None


@responses.activate
def test_wrong_content_type_is_not_retried(fetcher: Fetcher) -> None:
    responses.add(responses.GET, URL, body=b"<html>", status=200, content_type="text/html")
    fetcher.fetch(URL)
    assert len(responses.calls) == 1


@responses.activate
def test_403_is_an_error(fetcher: Fetcher) -> None:
    responses.add(responses.GET, URL, body=b"nope", status=403, content_type="text/html")
    outcome = fetcher.fetch(URL)
    assert outcome.status == ERROR
    assert outcome.http_code == 403
    assert len(responses.calls) == 1


# --- retries ----------------------------------------------------------------


@responses.activate
def test_three_500s_end_as_an_error_after_three_attempts(fetcher: Fetcher) -> None:
    for _ in range(3):
        responses.add(responses.GET, URL, body=b"oops", status=500)

    outcome = fetcher.fetch(URL)

    assert outcome.status == ERROR
    assert outcome.attempts == 3
    assert outcome.error_msg == "HTTP 500"
    assert len(responses.calls) == 3


@responses.activate
def test_a_500_then_a_200_succeeds_on_the_second_attempt(fetcher: Fetcher) -> None:
    responses.add(responses.GET, URL, body=b"oops", status=503)
    responses.add(responses.GET, URL, body=b"good", status=200, content_type=OCTET_STREAM)

    outcome = fetcher.fetch(URL)

    assert outcome.status == OK
    assert outcome.body == b"good"
    assert outcome.attempts == 2


@responses.activate
def test_a_timeout_then_a_200_succeeds(fetcher: Fetcher) -> None:
    responses.add(responses.GET, URL, body=requests.Timeout("too slow"))
    responses.add(responses.GET, URL, body=b"good", status=200, content_type=OCTET_STREAM)

    outcome = fetcher.fetch(URL)

    assert outcome.status == OK
    assert outcome.attempts == 2


@responses.activate
def test_repeated_timeouts_end_as_an_error(fetcher: Fetcher) -> None:
    for _ in range(3):
        responses.add(responses.GET, URL, body=requests.Timeout("too slow"))

    outcome = fetcher.fetch(URL)

    assert outcome.status == ERROR
    assert outcome.attempts == 3
    assert "timeout" in (outcome.error_msg or "")


@responses.activate
def test_a_connection_error_is_retried_then_reported(fetcher: Fetcher) -> None:
    for _ in range(3):
        responses.add(responses.GET, URL, body=requests.ConnectionError("refused"))

    outcome = fetcher.fetch(URL)

    assert outcome.status == ERROR
    assert outcome.attempts == 3
    assert "ConnectionError" in (outcome.error_msg or "")


@responses.activate
def test_backoff_grows_exponentially_between_retries(fetcher: Fetcher, slept: list[float]) -> None:
    for _ in range(3):
        responses.add(responses.GET, URL, body=b"oops", status=500)

    fetcher.fetch(URL)

    # Waits alternate: delay, backoff, delay, backoff, delay.
    backoffs = [slept[1], slept[3]]
    assert backoffs == [2.0, 4.0]


# --- politeness -------------------------------------------------------------


@responses.activate
def test_every_request_waits_at_least_the_configured_delay(fetcher: Fetcher, slept: list[float]) -> None:
    responses.add(responses.GET, URL, body=b"x", status=200, content_type=OCTET_STREAM)

    fetcher.fetch(URL)

    assert len(slept) == 1
    assert 1.5 <= slept[0] <= 2.0  # delay plus jitter


@responses.activate
def test_retries_are_delayed_too(fetcher: Fetcher, slept: list[float]) -> None:
    responses.add(responses.GET, URL, body=b"oops", status=500)
    responses.add(responses.GET, URL, body=b"good", status=200, content_type=OCTET_STREAM)

    fetcher.fetch(URL)

    delays = [slept[0], slept[2]]
    assert all(1.5 <= d <= 2.0 for d in delays)


@responses.activate
def test_zero_delay_means_no_sleep_at_all(slept: list[float]) -> None:
    quick = Fetcher(user_agent=UA, delay_seconds=0.0, jitter_seconds=0.0, sleep=slept.append)
    responses.add(responses.GET, URL, body=b"x", status=200, content_type=OCTET_STREAM)

    quick.fetch(URL)

    assert slept == []


@responses.activate
def test_a_browser_user_agent_is_sent(fetcher: Fetcher) -> None:
    responses.add(responses.GET, URL, body=b"x", status=200, content_type=OCTET_STREAM)

    fetcher.fetch(URL)

    assert responses.calls[0].request.headers["User-Agent"] == UA
    assert "Mozilla" in responses.calls[0].request.headers["User-Agent"]


@responses.activate
def test_requests_are_sequential_never_concurrent(fetcher: Fetcher) -> None:
    """Politeness is non-negotiable: one request at a time, in order."""
    urls = [URL, URL.replace("nd_accepted", "omts").replace(".pdf", ".csv")]
    for url in urls:
        responses.add(responses.GET, url, body=b"x", status=200, content_type=OCTET_STREAM)

    for url in urls:
        fetcher.fetch(url)

    assert [call.request.url for call in responses.calls] == urls


# --- construction -----------------------------------------------------------


def test_build_fetcher_carries_the_settings(settings) -> None:
    built = build_fetcher(settings)
    assert built.delay_seconds == settings.delay_seconds
    assert built.retries == settings.retries
    assert built.session.headers["User-Agent"] == settings.user_agent
    built.close()


def test_a_supplied_session_is_not_closed_by_the_fetcher() -> None:
    session = requests.Session()
    with Fetcher(user_agent=UA, session=session, delay_seconds=0, jitter_seconds=0):
        pass
    assert session.adapters  # still usable
    session.close()
