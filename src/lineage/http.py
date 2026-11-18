"""Client HTTP poli : un débit maximal par hôte et des reprises sur erreur temporaire."""
from __future__ import annotations

import logging
import time
from urllib.parse import urlparse

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

log = logging.getLogger(__name__)

# Intervalle minimal entre deux requêtes vers le même hôte (secondes).
# MusicBrainz : ~1 req/s. Discogs authentifié : 60 req/min.
MIN_INTERVAL = {
    "musicbrainz.org": 1.1,
    "api.discogs.com": 1.1,
    "query.wikidata.org": 1.0,
}
DEFAULT_INTERVAL = 1.0
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class RetryableHTTPError(Exception):
    pass


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, (RetryableHTTPError, httpx.TransportError))


class PoliteClient:
    def __init__(self, user_agent: str, timeout: float = 30.0):
        self._client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            timeout=timeout,
            follow_redirects=True,
        )
        self._last_call: dict[str, float] = {}

    def _throttle(self, host: str) -> None:
        interval = MIN_INTERVAL.get(host, DEFAULT_INTERVAL)
        elapsed = time.monotonic() - self._last_call.get(host, 0.0)
        if elapsed < interval:
            time.sleep(interval - elapsed)
        self._last_call[host] = time.monotonic()

    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(multiplier=2, min=2, max=60),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def get_json(self, url: str, params: dict | None = None, headers: dict | None = None) -> dict | None:
        """Renvoie le JSON, ou None si la ressource n'existe pas (404)."""
        host = urlparse(url).hostname or ""
        self._throttle(host)
        resp = self._client.get(url, params=params, headers=headers)
        if resp.status_code == 404:
            return None
        if resp.status_code in RETRYABLE_STATUS:
            log.warning("HTTP %s sur %s, nouvelle tentative", resp.status_code, url)
            raise RetryableHTTPError(f"{resp.status_code} {url}")
        resp.raise_for_status()
        return resp.json()

    def close(self) -> None:
        self._client.close()
