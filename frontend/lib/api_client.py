"""Thin REST client. The frontend reaches the backend only through this module."""

from typing import Any

import requests

from lib.config import API_BASE_URL, API_TIMEOUT_SECONDS


class ApiError(Exception):
    """Raised when the backend is unreachable or returns an unusable response."""


def get(path: str, **kwargs: Any) -> tuple[int, dict[str, Any]]:
    """GET `path` from the backend and return (status_code, payload)."""
    url = f"{API_BASE_URL}/{path.lstrip('/')}"
    try:
        response = requests.get(url, timeout=API_TIMEOUT_SECONDS, **kwargs)
    except requests.RequestException as exc:
        raise ApiError(f"Cannot reach the backend at {url}: {exc}") from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise ApiError(f"Backend returned a non-JSON response from {url}") from exc

    return response.status_code, payload


def health() -> tuple[int, dict[str, Any]]:
    return get("/health")
