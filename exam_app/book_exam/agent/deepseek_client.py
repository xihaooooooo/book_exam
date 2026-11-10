"""Send one non-streaming request to the DeepSeek Chat Completions API."""

from __future__ import annotations

import asyncio
import math

import httpx

from .context import Context
from .deepseek_request import build_deepseek_request
from .deepseek_response import DeepSeekResponseError, parse_deepseek_response
from .messages import AssistantMessage


class DeepSeekClientError(RuntimeError):
    """Raised when a DeepSeek request fails before its response is parsed.

    ``status_code`` is set for non-2xx HTTP responses. ``timed_out`` is true
    for requests cut off by the client or transport timeout, which separates
    them from ordinary network failures.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        timed_out: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.timed_out = timed_out


def _validate_api_key(api_key: object) -> str:
    if (
        not isinstance(api_key, str)
        or not api_key
        or any(not ("!" <= char <= "~") for char in api_key)
    ):
        raise ValueError(
            "api_key must be a non-empty string of printable ASCII "
            "characters without whitespace"
        )
    return api_key


def _validate_timeout(timeout_seconds: object) -> float:
    if isinstance(timeout_seconds, bool) or not isinstance(
        timeout_seconds, (int, float)
    ):
        raise ValueError("timeout_seconds must be a finite positive number")

    try:
        timeout = float(timeout_seconds)
    except OverflowError as error:
        raise ValueError(
            "timeout_seconds must be a finite positive number"
        ) from error
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout_seconds must be a finite positive number")
    return timeout


class DeepSeekClient:
    """A non-streaming DeepSeek client with one reused HTTP connection pool.

    Requests are never retried and redirects are not followed. Timeouts cover
    both sending the request and reading the complete response body.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.deepseek.com",
        timeout_seconds: float = 300.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = _validate_api_key(api_key)
        self._timeout_seconds = _validate_timeout(timeout_seconds)

        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("base_url must be a non-empty string")
        self._base_url = base_url.strip().rstrip("/")
        self._endpoint = f"{self._base_url}/chat/completions"

        if http_client is None:
            self._http = httpx.AsyncClient(
                timeout=self._timeout_seconds,
                follow_redirects=False,
            )
            self._owns_http_client = True
        else:
            # The caller owns an injected client and is responsible for closing
            # it; this client only closes clients it created itself.
            self._http = http_client
            self._owns_http_client = False

    async def __aenter__(self) -> DeepSeekClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def complete(self, context: Context) -> AssistantMessage:
        """Send one Chat Completions request and convert its response.

        Tool calls are returned inside the assistant message. Executing them,
        appending history, and requesting a follow-up are left to the agent
        loop.
        """
        request = build_deepseek_request(context)

        # Do not chain transport or timeout errors: their raw text can contain
        # request details, and only the sanitized classification is exposed.
        try:
            async with asyncio.timeout(self._timeout_seconds):
                # Override any auth configured on an injected client; the
                # Bearer header above is the only credential for this call.
                response = await self._http.post(
                    self._endpoint,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=request,
                    auth=httpx.Auth(),
                    follow_redirects=False,
                )
        except TimeoutError as error:
            raise DeepSeekClientError(
                f"DeepSeek request exceeded the "
                f"{self._timeout_seconds:g} second total deadline "
                f"({type(error).__name__})",
                timed_out=True,
            ) from None
        except httpx.TimeoutException as error:
            raise DeepSeekClientError(
                f"DeepSeek request timed out: {type(error).__name__}",
                timed_out=True,
            ) from None
        except httpx.HTTPError as error:
            raise DeepSeekClientError(
                f"DeepSeek request failed: {type(error).__name__}"
            ) from None

        status_code = response.status_code
        if not 200 <= status_code < 300:
            raise DeepSeekClientError(
                f"DeepSeek API request failed with HTTP {status_code}",
                status_code=status_code,
            )

        try:
            payload = response.json()
        except ValueError as error:
            raise DeepSeekResponseError(
                "DeepSeek response body is not valid JSON"
            ) from error

        return parse_deepseek_response(payload, model=request["model"])

    async def aclose(self) -> None:
        """Close the HTTP client when this instance created it."""
        if self._owns_http_client:
            await self._http.aclose()
