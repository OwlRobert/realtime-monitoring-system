"""Backend REST client.

The single place the frontend talks HTTP. Streamlit is deliberately not
imported here, so the client stays easy to test and reuse.
"""

from dataclasses import dataclass
from typing import Any

import requests

from lib.config import API_BASE_URL, API_TIMEOUT_SECONDS


class ApiError(Exception):
    """The backend could not be reached or answered unintelligibly."""


@dataclass(frozen=True)
class ApiResult:
    """One backend response: status plus decoded body.

    `data` is the decoded JSON, or raw bytes for a binary download.
    """

    status_code: int
    data: Any = None
    headers: dict[str, str] | None = None

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    @property
    def unauthorized(self) -> bool:
        return self.status_code == 401

    @property
    def error_message(self) -> str:
        """A message worth showing a user, whatever shape the error took."""
        if self.ok:
            return ""
        if isinstance(self.data, dict):
            detail = self.data.get("detail")
            if isinstance(detail, str):
                return detail
            if isinstance(detail, list) and detail:
                # FastAPI validation errors
                first = detail[0]
                if isinstance(first, dict) and "msg" in first:
                    return str(first["msg"])
        return f"Request failed (HTTP {self.status_code})"


class ApiClient:
    def __init__(
        self,
        base_url: str = API_BASE_URL,
        timeout: float = API_TIMEOUT_SECONDS,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = session or requests.Session()

    def request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        json: Any = None,
        params: dict[str, Any] | None = None,
        files: Any = None,
        raw: bool = False,
    ) -> ApiResult:
        """Perform one request. Raises ApiError only when there is no answer.

        `files` sends multipart/form-data; `raw` returns the response body as
        bytes instead of decoded JSON (used for the Excel download).
        """
        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = {"Authorization": f"Bearer {token}"} if token else {}

        try:
            response = self._session.request(
                method,
                url,
                headers=headers,
                json=json,
                params=params,
                files=files,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise ApiError(f"Cannot reach the backend at {url}") from exc

        response_headers = dict(response.headers)

        if response.status_code == 204 or not response.content:
            return ApiResult(response.status_code, None, response_headers)

        if raw and 200 <= response.status_code < 300:
            return ApiResult(response.status_code, response.content, response_headers)

        try:
            return ApiResult(response.status_code, response.json(), response_headers)
        except ValueError:
            raise ApiError(f"Backend returned a non-JSON response from {url}") from None

    def get(self, path: str, **kwargs: Any) -> ApiResult:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> ApiResult:
        return self.request("POST", path, **kwargs)

    def patch(self, path: str, **kwargs: Any) -> ApiResult:
        return self.request("PATCH", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> ApiResult:
        return self.request("DELETE", path, **kwargs)


# Shared client for the app; tests construct their own.
client = ApiClient()
