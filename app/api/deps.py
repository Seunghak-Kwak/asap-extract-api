from fastapi import Depends, Header, HTTPException, status

from app.auth.keys import verify
from app.db.meta.models import ApiKey


async def require_api_key(
    authorization: str | None = Header(default=None),
) -> ApiKey:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    token = authorization.split(None, 1)[1].strip()
    key = await verify(token)
    if key is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid api key")
    return key


ApiKeyDep = Depends(require_api_key)
