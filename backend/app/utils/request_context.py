from fastapi import Request


def client_ip(request: Request) -> str | None:
    """Caller's IP for the audit trail, or None when it cannot be determined."""
    if request.client is None:
        return None
    return request.client.host
