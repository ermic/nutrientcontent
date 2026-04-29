"""X-API-Key dependency. Constant-time comparison via secrets.compare_digest.
Missing and wrong keys both return 401 with the same opaque message."""
import secrets

from fastapi import Header, HTTPException, status

from .config import settings


async def require_api_key(x_api_key: str | None = Header(default=None)):
    # compare_digest on str requires ASCII; encode to bytes so a non-ASCII
    # header value yields 401 instead of crashing the handler with TypeError.
    if x_api_key is None or not secrets.compare_digest(
        x_api_key.encode("utf-8"), settings.api_key.encode("utf-8")
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid api key"
        )
