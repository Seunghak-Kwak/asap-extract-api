from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.keys import verify
from app.db.meta.models import ApiKey

_bearer = HTTPBearer(auto_error=False, description="API key issued via /v1/admin/api-keys")


async def require_api_key(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> ApiKey:
    if creds is None or not creds.credentials:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    key = await verify(creds.credentials)
    if key is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired api key")
    return key


async def require_admin(key: ApiKey = Depends(require_api_key)) -> ApiKey:
    if not key.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin only")
    return key


ApiKeyDep = Depends(require_api_key)
AdminDep = Depends(require_admin)
