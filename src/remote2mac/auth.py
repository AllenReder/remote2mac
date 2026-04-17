"""Authentication helpers for the exec API."""

from __future__ import annotations

import secrets

from fastapi import HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

SESSION_TOKEN_HEADER = APIKeyHeader(name="X-Session-Token", auto_error=False)


async def verify_session_token(
    request: Request,
    session_token: str | None = Security(SESSION_TOKEN_HEADER),
) -> str:
    """Verify the in-memory session token."""
    runtime = request.app.state.runtime
    expected = runtime.session_token
    if session_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing session token. Include X-Session-Token header.",
        )
    if not expected or not secrets.compare_digest(session_token.encode(), expected.encode()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid session token.",
        )
    return session_token
