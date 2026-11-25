"""Client HTTP poli : un débit maximal par hôte et des reprises sur erreur temporaire.

Sur 429 / 503, le serveur peut indiquer combien de temps attendre (en-tête Retry-After) : on
respecte ce délai, plafonné à MAX_WAIT. Sinon, attente exponentielle (2 s, 4 s, 8 s… jusqu'à MAX_WAIT).
Après MAX_ATTEMPTS tentatives, RetryableHTTPError remonte à l'appelant (le crawler la journalise,
marque l'élément pour le prochain run et continue).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable
from urllib.parse import urlparse

import httpx
from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)

# Intervalle minimal entre deux requêtes vers le même hôte (secondes).
# MusicBrainz : ~1 req/s officiellement ; 1,5 s pour rester sous la limite malgré les rafales.
# Discogs authentifié : 60 req/min.
MIN_INTERVAL = {
    "musicbrainz.org": 1.5,
    "api.discogs.com": 1.1,
    "query.wikidata.org": 1.0,
}
DEFAULT_INTERVAL = 1.0
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 8
MAX_WAIT = 120.0


class RetryableHTTPError(Exception):
    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    """Retry-After en secondes : soit un entier (« 30 »), soit une date HTTP. None si absent ou illisible."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - (now or datetime.now(timezone.utc))).total_seconds())


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, (RetryableHTTPError, httpx.TransportError))


_exponential = wait_exponential(multiplier=2, min=2, max=MAX_WAIT)


def _wait(retry_state) -> float:
    """Délai avant la tentative suivante : Retry-After s'il est donné, sinon exponentiel."""
    exc = retry_state.outcome.exception()
    retry_after = getattr(exc, "retry_after", None)
    if retry_after is not None:
        return min(max(retry_after, 1.0), MAX_WAIT)
    return _exponential(retry_state)


class PoliteClient:
    def __init__(
        self,
        user_agent: str,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
        )
        self._sleep = sleep
        self._last_call: dict[str, float] = {}

    def _throttle(self, host: str) -> None:
        interval = MIN_INTERVAL.get(host, DEFAULT_INTERVAL)
        elapsed = time.monotonic() - self._last_call.get(host, 0.0)
        if elapsed < interval:
            self._sleep(interval - elapsed)
        self._last_call[host] = time.monotonic()

    def get_json(self, url: str, params: dict | None = None, headers: dict | None = None) -> dict | None:
        """Renvoie le JSON, ou None si la ressource n'existe pas (404)."""
        retrying = Retrying(
            retry=retry_if_exception(_is_retryable),
            wait=_wait,
            stop=stop_after_attempt(MAX_ATTEMPTS),
            sleep=self._sleep,
            reraise=True,
        )
        return retrying(self._get_once, url, params, headers)

    def _get_once(self, url: str, params: dict | None, headers: dict | None) -> dict | None:
        host = urlparse(url).hostname or ""
        self._throttle(host)
        resp = self._client.get(url, params=params, headers=headers)
        if resp.status_code == 404:
            return None
        if resp.status_code in RETRYABLE_STATUS:
            retry_after = parse_retry_after(resp.headers.get("Retry-After"))
            log.warning("HTTP %s sur %s, nouvelle tentative%s", resp.status_code, url,
                        f" dans {retry_after:.0f} s (Retry-After)" if retry_after is not None else "")
            raise RetryableHTTPError(f"{resp.status_code} {url}", retry_after)
        resp.raise_for_status()
        return resp.json()

    def close(self) -> None:
        self._client.close()
