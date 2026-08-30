import pytest
import requests

from lib.api_client import ApiClient, ApiError, ApiResult


class FakeResponse:
    def __init__(self, status_code=200, payload=None, *, content=b"{}", bad_json=False):
        self.status_code = status_code
        self.content = content
        self.headers = {}
        self._payload = payload
        self._bad_json = bad_json

    def json(self):
        if self._bad_json:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    """Records the last request and returns a canned response."""

    def __init__(self, response=None, raises=None):
        self.response = response or FakeResponse(200, {"ok": True})
        self.raises = raises
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        if self.raises:
            raise self.raises
        return self.response


def make_client(session):
    return ApiClient("http://backend:8000", timeout=3, session=session)


# --------------------------------------------------------------------------
# Requests
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("verb", "expected"),
    [("get", "GET"), ("post", "POST"), ("patch", "PATCH"), ("delete", "DELETE")],
)
def test_every_verb_is_supported(verb, expected):
    session = FakeSession()
    getattr(make_client(session), verb)("/records")

    assert session.calls[0]["method"] == expected
    assert session.calls[0]["url"] == "http://backend:8000/records"


def test_paths_join_cleanly_with_or_without_a_leading_slash():
    session = FakeSession()
    client = make_client(session)

    client.get("/health")
    client.get("health")

    assert {call["url"] for call in session.calls} == {"http://backend:8000/health"}


def test_token_is_attached_when_present():
    session = FakeSession()
    make_client(session).get("/auth/me", token="a-token")

    assert session.calls[0]["headers"] == {"Authorization": "Bearer a-token"}


def test_no_authorization_header_without_a_token():
    session = FakeSession()
    make_client(session).get("/health")

    assert session.calls[0]["headers"] == {}


def test_timeout_and_payload_are_forwarded():
    session = FakeSession()
    make_client(session).post("/records", json={"value": 1}, params={"page": 2})

    call = session.calls[0]
    assert call["timeout"] == 3
    assert call["json"] == {"value": 1}
    assert call["params"] == {"page": 2}


# --------------------------------------------------------------------------
# Responses
# --------------------------------------------------------------------------


def test_successful_response_is_parsed():
    session = FakeSession(FakeResponse(200, {"status": "ok"}))
    result = make_client(session).get("/health")

    assert result.ok is True
    assert result.data == {"status": "ok"}


def test_empty_body_is_handled():
    session = FakeSession(FakeResponse(204, content=b""))
    result = make_client(session).delete("/records/1")

    assert result.ok is True
    assert result.data is None


def test_error_status_is_returned_not_raised():
    session = FakeSession(FakeResponse(404, {"detail": "Record not found"}))
    result = make_client(session).get("/records/1")

    assert result.ok is False
    assert result.error_message == "Record not found"


def test_unauthorized_is_flagged():
    session = FakeSession(FakeResponse(401, {"detail": "Could not validate credentials"}))

    assert make_client(session).get("/auth/me").unauthorized is True


def test_validation_errors_are_readable():
    payload = {"detail": [{"loc": ["body", "value"], "msg": "field required"}]}
    session = FakeSession(FakeResponse(422, payload))

    assert make_client(session).post("/records").error_message == "field required"


def test_error_without_detail_still_reads_sensibly():
    session = FakeSession(FakeResponse(500, {"oops": True}))

    assert make_client(session).get("/x").error_message == "Request failed (HTTP 500)"


def test_unreachable_backend_raises_api_error():
    session = FakeSession(raises=requests.ConnectionError("refused"))

    with pytest.raises(ApiError, match="Cannot reach the backend"):
        make_client(session).get("/health")


def test_non_json_response_raises_api_error():
    session = FakeSession(FakeResponse(200, bad_json=True, content=b"<html>"))

    with pytest.raises(ApiError, match="non-JSON"):
        make_client(session).get("/health")


def test_successful_result_has_no_error_message():
    assert ApiResult(200, {"a": 1}).error_message == ""


# --------------------------------------------------------------------------
# Multipart upload and binary download
# --------------------------------------------------------------------------


def test_files_are_forwarded_for_multipart_uploads():
    session = FakeSession(FakeResponse(201, {"imported": 2}))
    files = {"file": ("records.csv", b"title,value", "text/csv")}

    make_client(session).post("/records/import", files=files, token="a-token")

    call = session.calls[0]
    assert call["files"] == files
    assert call["headers"] == {"Authorization": "Bearer a-token"}   # auth still attached
    assert call["json"] is None                                    # not sent as JSON


def test_binary_response_is_returned_as_bytes():
    payload = b"PK\x03\x04binary-workbook"
    session = FakeSession(FakeResponse(200, content=payload))
    session.response.content = payload

    result = make_client(session).get("/records/export.xlsx", raw=True)

    assert result.ok is True
    assert result.data == payload


def test_binary_responses_expose_headers():
    session = FakeSession(FakeResponse(200, content=b"PK"))
    session.response.headers = {"X-Export-Rows": "7"}

    result = make_client(session).get("/records/export.xlsx", raw=True)

    assert result.headers["X-Export-Rows"] == "7"


def test_binary_request_still_decodes_json_errors():
    session = FakeSession(FakeResponse(403, {"detail": "Insufficient permissions"}))

    result = make_client(session).get("/records/export.xlsx", raw=True)

    assert result.ok is False
    assert result.error_message == "Insufficient permissions"


def test_json_requests_are_unaffected_by_the_new_parameters():
    session = FakeSession(FakeResponse(200, {"status": "ok"}))

    result = make_client(session).get("/health")

    assert result.data == {"status": "ok"}
    assert session.calls[0]["files"] is None
