import json
from typing import Any, Optional, Tuple
from urllib import request as urllib_request
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

from fastapi import HTTPException

from ..config import PIPELINE_SERVER_URL


def _normalized_port(parts) -> Optional[int]:
    if parts.port is not None:
        return parts.port
    if parts.scheme == "http":
        return 80
    if parts.scheme == "https":
        return 443
    return None


def _is_allowed_pipeline_url(url: str) -> bool:
    """Allow only requests to the configured pipeline-server origin/path."""
    try:
        target = urlsplit(url)
        base = urlsplit(PIPELINE_SERVER_URL)
    except ValueError:
        return False

    if target.scheme not in {"http", "https"}:
        return False
    if not target.hostname or not base.hostname:
        return False
    if target.scheme != base.scheme:
        return False
    if target.hostname.lower() != base.hostname.lower():
        return False
    if _normalized_port(target) != _normalized_port(base):
        return False

    base_path = (base.path or "").rstrip("/")
    target_path = target.path or ""
    if base_path and not (
        target_path == base_path or target_path.startswith(f"{base_path}/")
    ):
        return False

    return True


def _assert_allowed_pipeline_url(url: str) -> None:
    if not _is_allowed_pipeline_url(url):
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Blocked outbound request to untrusted URL",
            },
        )


def http_json(method: str, url: str, payload: Optional[dict[str, Any]] = None) -> str:
    """Make an HTTP request with JSON payload and return response text."""
    _assert_allowed_pipeline_url(url)

    headers = {
        "Accept": "application/json",
    }
    data = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        data = body
        headers["Content-Type"] = "application/json"
    req = urllib_request.Request(url=url, data=data, headers=headers, method=method)
    try:
        with urllib_request.urlopen(req, timeout=120) as resp:
            return resp.read().decode("utf-8")
    except HTTPError as err:
        details = None
        try:
            details = err.read().decode("utf-8")
        except Exception:
            details = None
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Pipeline server error",
                "status": err.code,
                "body": details,
            },
        )
    except URLError as err:
        raise HTTPException(
            status_code=502,
            detail={"message": "Pipeline server unreachable", "error": str(err)},
        )
    except OSError as err:
        raise HTTPException(
            status_code=502,
            detail={"message": "Pipeline server connection failed", "error": str(err)},
        )


def try_get_json(url: str, timeout: int = 10) -> Tuple[Optional[int], Optional[dict]]:
    """Attempt a GET request and return (http_status_code, parsed_body).

    Unlike http_json, this function never raises. It returns (None, None) when
    the server is unreachable or the connection fails, allowing callers to treat
    network failures differently from HTTP error responses.

    Args:
        url: The URL to GET.
        timeout: Request timeout in seconds.

    Returns:
        A tuple of (status_code, body). status_code is None on connection
        failure; body is None when the response is not valid JSON.
    """
    if not _is_allowed_pipeline_url(url):
        return None, None

    req = urllib_request.Request(
        url=url, headers={"Accept": "application/json"}, method="GET"
    )
    try:
        with urllib_request.urlopen(req, timeout=timeout) as resp:
            try:
                body = json.loads(resp.read().decode("utf-8"))
            except Exception:
                body = None
            return resp.status, body
    except HTTPError as err:
        try:
            body = json.loads(err.read().decode("utf-8"))
        except Exception:
            body = None
        return err.code, body
    except (URLError, OSError):
        return None, None
