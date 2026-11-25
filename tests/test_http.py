"""Client HTTP : Retry-After, plafond d'attente, nombre de tentatives, débit MusicBrainz."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest

from lineage import http
from lineage.http import PoliteClient, RetryableHTTPError, parse_retry_after

URL = "https://musicbrainz.org/ws/2/release"


def client_with(responses):
    """Client dont le transport renvoie les réponses données dans l'ordre, et qui note les attentes."""
    calls, sleeps = [], []
    queue = list(responses)

    def handler(request):
        calls.append(request)
        return queue.pop(0) if len(queue) > 1 else queue[0]

    client = PoliteClient("test", transport=httpx.MockTransport(handler), sleep=sleeps.append)
    return client, calls, sleeps


def test_parse_retry_after():
    now = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
    assert parse_retry_after("30") == 30.0
    assert parse_retry_after(format_datetime(now + timedelta(seconds=45), usegmt=True), now) == 45.0
    assert parse_retry_after(format_datetime(now - timedelta(seconds=10), usegmt=True), now) == 0.0
    assert parse_retry_after("bientôt") is None
    assert parse_retry_after(None) is None


def test_retry_after_is_respected():
    client, calls, sleeps = client_with([
        httpx.Response(503, headers={"Retry-After": "7"}),
        httpx.Response(429, headers={"Retry-After": "3"}),
        httpx.Response(200, json={"ok": True}),
    ])
    assert client.get_json(URL) == {"ok": True}
    assert len(calls) == 3
    retry_waits = [s for s in sleeps if s > http.MIN_INTERVAL["musicbrainz.org"]]
    assert retry_waits == [7.0, 3.0]


def test_retry_after_is_capped():
    client, _, sleeps = client_with([
        httpx.Response(503, headers={"Retry-After": "600"}),
        httpx.Response(200, json={}),
    ])
    client.get_json(URL)
    assert max(sleeps) == http.MAX_WAIT == 120


def test_gives_up_after_max_attempts_with_bounded_backoff():
    client, calls, sleeps = client_with([httpx.Response(503)])
    with pytest.raises(RetryableHTTPError):
        client.get_json(URL)
    assert len(calls) == http.MAX_ATTEMPTS == 8
    backoff = [s for s in sleeps if s > http.MIN_INTERVAL["musicbrainz.org"]]
    assert len(backoff) == 7 and max(backoff) <= 120
    assert backoff == sorted(backoff)  # exponentiel


def test_not_found_is_none_and_not_retried():
    client, calls, _ = client_with([httpx.Response(404)])
    assert client.get_json(URL) is None
    assert len(calls) == 1


def test_musicbrainz_interval():
    assert http.MIN_INTERVAL["musicbrainz.org"] == 1.5
