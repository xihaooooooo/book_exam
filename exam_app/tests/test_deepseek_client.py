import asyncio
import json
import traceback
import unittest
from unittest import mock

import httpx

import book_exam.agent.deepseek_client as deepseek_client_module
from book_exam.agent import (
    AssistantMessage,
    Context,
    DeepSeekClient,
    DeepSeekClientError,
    DeepSeekRequestError,
    DeepSeekResponseError,
    ImageContent,
    TextContent,
    ThinkingContent,
    ToolCall,
    UserMessage,
    build_deepseek_request,
)


API_KEY = "test-api-key"


def _context(text: str = "你好") -> Context:
    return Context(messages=[UserMessage(content=[TextContent(text=text)])])


def _success_payload(
    *,
    content: str | None = "答案",
    reasoning_content: str | None = "推理",
    finish_reason: str = "stop",
    tool_calls: list[dict] | None = None,
) -> dict:
    message: dict = {"role": "assistant", "content": content}
    if reasoning_content is not None:
        message["reasoning_content"] = reasoning_content
    if tool_calls is not None:
        message["tool_calls"] = tool_calls

    return {
        "id": "resp-1",
        "model": "deepseek-flash",
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        },
    }


def _success_response() -> httpx.Response:
    return httpx.Response(200, json=_success_payload())


class _InjectedClientTestCase(unittest.IsolatedAsyncioTestCase):
    def make_http_client(
        self,
        handler,
        *,
        follow_redirects: bool = False,
        auth: httpx.Auth | None = None,
    ) -> httpx.AsyncClient:
        http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            follow_redirects=follow_redirects,
            auth=auth,
        )
        self.addAsyncCleanup(http_client.aclose)
        return http_client

    def make_client(
        self,
        handler,
        *,
        base_url: str = "https://api.deepseek.com",
        timeout_seconds: float = 30.0,
        follow_redirects: bool = False,
        auth: httpx.Auth | None = None,
    ) -> DeepSeekClient:
        return DeepSeekClient(
            API_KEY,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            http_client=self.make_http_client(
                handler,
                follow_redirects=follow_redirects,
                auth=auth,
            ),
        )


class DeepSeekClientValidationTests(unittest.TestCase):
    def test_rejects_blank_api_key(self) -> None:
        for api_key in ("", "   ", "\t\n", None, 123):
            with self.subTest(api_key=api_key):
                with self.assertRaises(ValueError):
                    DeepSeekClient(api_key)

    def test_rejects_disallowed_api_key_characters_without_echoing_it(
        self,
    ) -> None:
        secret = "sk-review-secret"
        for suffix in (
            " key",
            "\tkey",
            "\nkey",
            "\x00key",
            "\x7fkey",
            "密钥",
            "\u00a0key",
            "\u200bkey",
        ):
            api_key = secret + suffix
            with self.subTest(api_key=api_key):
                with self.assertRaises(ValueError) as raised:
                    DeepSeekClient(api_key)
                self.assertNotIn(secret, str(raised.exception))

    def test_rejects_non_finite_or_non_positive_timeout(self) -> None:
        for timeout_seconds in (
            0,
            -1,
            -0.5,
            float("inf"),
            float("-inf"),
            float("nan"),
            True,
            "300",
            None,
            10**1000,
        ):
            with self.subTest(timeout_seconds=timeout_seconds):
                with self.assertRaises(ValueError):
                    DeepSeekClient(API_KEY, timeout_seconds=timeout_seconds)

    def test_rejects_blank_base_url(self) -> None:
        for base_url in ("", "   ", None, 123):
            with self.subTest(base_url=base_url):
                with self.assertRaises(ValueError):
                    DeepSeekClient(API_KEY, base_url=base_url)


class DeepSeekClientRequestTests(_InjectedClientTestCase):
    async def test_posts_one_authenticated_request_with_converted_body(
        self,
    ) -> None:
        requests: list[httpx.Request] = []

        def handle(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return _success_response()

        context = _context("请解释闭包")
        expected_body = build_deepseek_request(context)
        client = self.make_client(
            handle,
            base_url="https://example.test/v1/",
        )

        await client.complete(context)

        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(
            str(request.url),
            "https://example.test/v1/chat/completions",
        )
        self.assertEqual(
            request.headers["authorization"],
            f"Bearer {API_KEY}",
        )
        self.assertEqual(request.headers["content-type"], "application/json")

        body = json.loads(request.content)
        self.assertEqual(body, expected_body)
        self.assertEqual(body["model"], "deepseek-flash")
        self.assertIs(body["stream"], False)
        self.assertEqual(body["thinking"], {"type": "enabled"})
        self.assertEqual(
            body["messages"],
            [{"role": "user", "content": "请解释闭包"}],
        )
        self.assertNotIn("tools", body)

    async def test_bearer_header_overrides_injected_client_auth(self) -> None:
        requests: list[httpx.Request] = []

        def handle(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return _success_response()

        client = self.make_client(
            handle,
            auth=httpx.BasicAuth("basic-user", "basic-password"),
        )

        await client.complete(_context())

        self.assertEqual(len(requests), 1)
        self.assertEqual(
            requests[0].headers["authorization"],
            f"Bearer {API_KEY}",
        )

    async def test_request_conversion_errors_are_preserved_without_posting(
        self,
    ) -> None:
        requests: list[httpx.Request] = []

        def handle(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return _success_response()

        client = self.make_client(handle)
        context = Context(
            messages=[
                UserMessage(
                    content=[
                        TextContent(text="看图"),
                        ImageContent(data="aW1hZ2U=", mime_type="image/png"),
                    ]
                )
            ]
        )

        with self.assertRaises(DeepSeekRequestError):
            await client.complete(context)

        self.assertEqual(requests, [])


class DeepSeekClientResponseTests(_InjectedClientTestCase):
    async def test_returns_converted_assistant_message(self) -> None:
        client = self.make_client(lambda request: _success_response())

        message = await client.complete(_context())

        self.assertIsInstance(message, AssistantMessage)
        self.assertEqual(message.api, "openai-completions")
        self.assertEqual(message.provider, "deepseek")
        self.assertEqual(message.model, "deepseek-flash")
        self.assertEqual(message.stop_reason, "stop")
        self.assertEqual(message.raw_stop_reason, "stop")
        self.assertEqual(message.response_id, "resp-1")
        self.assertEqual(message.response_model, "deepseek-flash")
        self.assertIsNone(message.error_message)
        self.assertIsNotNone(message.usage)
        self.assertEqual(message.usage.input_tokens, 10)
        self.assertEqual(message.usage.output_tokens, 5)
        self.assertEqual(message.usage.total_tokens, 15)

        contents = list(message.content)
        self.assertEqual(len(contents), 2)
        self.assertIsInstance(contents[0], ThinkingContent)
        self.assertEqual(contents[0].thinking, "推理")
        self.assertIsInstance(contents[1], TextContent)
        self.assertEqual(contents[1].text, "答案")

    async def test_returns_tool_calls_inside_the_assistant_message(self) -> None:
        payload = _success_payload(
            content=None,
            reasoning_content=None,
            finish_reason="tool_calls",
            tool_calls=[
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "lookup",
                        "arguments": '{"section_id": "1.1"}',
                    },
                }
            ],
        )
        client = self.make_client(
            lambda request: httpx.Response(200, json=payload)
        )

        message = await client.complete(_context())

        self.assertEqual(message.stop_reason, "toolUse")
        calls = [
            block for block in message.content if isinstance(block, ToolCall)
        ]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].id, "call-1")
        self.assertEqual(calls[0].name, "lookup")
        self.assertEqual(calls[0].arguments, {"section_id": "1.1"})
        self.assertIsNone(calls[0].arguments_error)

    async def test_invalid_json_raises_response_error(self) -> None:
        client = self.make_client(
            lambda request: httpx.Response(
                200,
                content=b"not valid json",
                headers={"content-type": "application/json"},
            )
        )

        with self.assertRaises(DeepSeekResponseError) as raised:
            await client.complete(_context())

        self.assertNotIn("not valid json", str(raised.exception))

    async def test_structurally_invalid_json_raises_response_error(self) -> None:
        client = self.make_client(
            lambda request: httpx.Response(200, json={"choices": []})
        )

        with self.assertRaises(DeepSeekResponseError):
            await client.complete(_context())


class DeepSeekClientFailureTests(_InjectedClientTestCase):
    async def test_http_error_keeps_status_without_leaking_credentials(
        self,
    ) -> None:
        secret_body = f"rejected key {API_KEY}"

        def handle(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                401,
                json={"error": {"message": secret_body}},
            )

        client = self.make_client(handle)

        with self.assertRaises(DeepSeekClientError) as raised:
            await client.complete(_context())

        error = raised.exception
        self.assertEqual(error.status_code, 401)
        self.assertIs(error.timed_out, False)
        self.assertNotIn(API_KEY, str(error))
        self.assertNotIn(secret_body, str(error))

    async def test_redirect_is_not_followed(self) -> None:
        requests: list[httpx.Request] = []

        def handle(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if len(requests) == 1:
                return httpx.Response(
                    302,
                    headers={"location": "https://example.test/elsewhere"},
                )
            return httpx.Response(200, json=_success_payload())

        client = self.make_client(handle, follow_redirects=True)

        with self.assertRaises(DeepSeekClientError) as raised:
            await client.complete(_context())

        self.assertEqual(raised.exception.status_code, 302)
        self.assertEqual(len(requests), 1)

    async def test_network_error_is_not_reported_as_timeout(self) -> None:
        requests: list[httpx.Request] = []

        def handle(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            raise httpx.ConnectError(
                f"connection refused for {API_KEY}",
                request=request,
            )

        client = self.make_client(handle)

        with self.assertRaises(DeepSeekClientError) as raised:
            await client.complete(_context())

        error = raised.exception
        self.assertIs(error.timed_out, False)
        self.assertIsNone(error.status_code)
        self.assertIn("ConnectError", str(error))
        self.assertNotIn(API_KEY, str(error))
        self.assertEqual(len(requests), 1)

    async def test_transport_error_traceback_hides_raw_exception_text(
        self,
    ) -> None:
        fake_credential = "sk-fake-transport-credential"

        def handle(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError(
                f"transport failed for {fake_credential}",
                request=request,
            )

        client = self.make_client(handle)

        formatted = ""
        try:
            await client.complete(_context())
        except DeepSeekClientError:
            formatted = traceback.format_exc()
        else:
            self.fail("DeepSeekClientError was not raised")

        self.assertNotIn(fake_credential, formatted)
        self.assertIn("ConnectError", formatted)

    async def test_total_deadline_marks_timeout_and_stops_waiting(self) -> None:
        async def handle(request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(10)
            return _success_response()

        client = self.make_client(handle, timeout_seconds=0.1)

        with self.assertRaises(DeepSeekClientError) as raised:
            await client.complete(_context())

        error = raised.exception
        self.assertIs(error.timed_out, True)
        self.assertIsNone(error.status_code)
        self.assertIn("TimeoutError", str(error))
        self.assertNotIn(API_KEY, str(error))

    async def test_httpx_read_timeout_does_not_claim_the_full_deadline(
        self,
    ) -> None:
        def handle(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout(
                f"read timed out for {API_KEY}",
                request=request,
            )

        client = self.make_client(handle, timeout_seconds=300.0)

        with self.assertRaises(DeepSeekClientError) as raised:
            await client.complete(_context())

        error = raised.exception
        self.assertIs(error.timed_out, True)
        self.assertIsNone(error.status_code)
        self.assertIn("timed out", str(error))
        self.assertIn("ReadTimeout", str(error))
        self.assertNotIn(API_KEY, str(error))
        self.assertNotIn("300", str(error))
        self.assertNotIn("second", str(error))

    async def test_external_cancellation_propagates(self) -> None:
        started = asyncio.Event()

        async def handle(request: httpx.Request) -> httpx.Response:
            started.set()
            await asyncio.sleep(3600)
            return _success_response()

        client = self.make_client(handle, timeout_seconds=60)
        task = asyncio.create_task(client.complete(_context()))
        await asyncio.wait_for(started.wait(), timeout=1)

        task.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(task.cancelled())


class DeepSeekClientLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_aclose_leaves_injected_client_open(self) -> None:
        http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: _success_response()
            )
        )
        self.addAsyncCleanup(http_client.aclose)
        client = DeepSeekClient(API_KEY, http_client=http_client)

        await client.complete(_context())
        await client.aclose()
        await client.aclose()

        self.assertIs(http_client.is_closed, False)

    async def test_async_context_manager_closes_owned_client(self) -> None:
        created: list[httpx.AsyncClient] = []
        real_async_client = httpx.AsyncClient

        def factory(*args, **kwargs) -> httpx.AsyncClient:
            kwargs["transport"] = httpx.MockTransport(
                lambda request: _success_response()
            )
            instance = real_async_client(*args, **kwargs)
            created.append(instance)
            return instance

        with mock.patch.object(
            deepseek_client_module.httpx,
            "AsyncClient",
            side_effect=factory,
        ):
            client = DeepSeekClient(API_KEY, timeout_seconds=5)
            self.assertEqual(len(created), 1)
            self.assertIs(created[0].is_closed, False)
            self.addAsyncCleanup(created[0].aclose)

            async with client as entered:
                self.assertIs(entered, client)
                await client.complete(_context())

            self.assertIs(created[0].is_closed, True)
