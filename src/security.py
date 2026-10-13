import os
import re
import hmac
import logging
from typing import Any, Dict, Optional
from fastapi import HTTPException, Security, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from src.keys import key_store

security_scheme = HTTPBearer(auto_error=False)

TOKEN_REDACTION_PATTERN = re.compile(r"(ya29\.[a-zA-Z0-9_\-]+|1//[a-zA-Z0-9_\-]+)")

class SensitiveDataFilter(logging.Filter):
    """
    Log filter that scrubs OAuth access tokens and refresh tokens from all log messages.
    """
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = TOKEN_REDACTION_PATTERN.sub("[REDACTED_TOKEN]", record.msg)
        if record.args:
            cleaned_args = []
            for arg in record.args:
                if isinstance(arg, str):
                    cleaned_args.append(TOKEN_REDACTION_PATTERN.sub("[REDACTED_TOKEN]", arg))
                else:
                    cleaned_args.append(arg)
            record.args = tuple(cleaned_args)
        return True


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] [%(name)s]: %(message)s"
        )
        handler.setFormatter(formatter)
        handler.addFilter(SensitiveDataFilter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


logger = get_logger("security")


def _missing_bearer() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing Authorization header. Expected Bearer token.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def verify_api_key(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security_scheme)
) -> Dict[str, Any]:
    """
    Validates the bearer token against the key store (and the legacy PROXY_API_KEY).
    Returns the matching key's public record.
    """
    if not credentials or not credentials.credentials:
        raise _missing_bearer()

    record = key_store.verify(credentials.credentials)
    if record is None:
        # Fails closed: with no keys configured nothing matches. Tell the operator,
        # but give callers the same 401 either way.
        if not key_store.has_active_keys():
            logger.error("No active API keys: set PROXY_API_KEY or run `python -m src.keys create <name>`.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid, revoked or unauthorized API key.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return record


def verify_admin_key(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security_scheme)
) -> None:
    """
    Guards /admin endpoints with ADMIN_API_KEY. When it is unset the admin API
    does not exist (404), so key management is then CLI-only.
    """
    expected = os.getenv("ADMIN_API_KEY", "").strip()
    if not expected:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    if not credentials or not credentials.credentials:
        raise _missing_bearer()
    if not hmac.compare_digest(credentials.credentials.strip(), expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin key.",
            headers={"WWW-Authenticate": "Bearer"},
        )
