"""Narrow HTTPX client that validates every ADR 001 response."""

from __future__ import annotations

import rfc8785
import httpx
from pydantic import ValidationError
from uuid import UUID

from boundary.sut.contract_v1 import (
    CONTRACT_VERSION,
    CONTRACT_VERSION_HEADER,
    CONTRACT_VERSIONS_HEADER,
    MAX_TERMINAL_OUTPUT_BYTES,
    AcceptedResponse,
    CancellationAcknowledgement,
    CancellationRequest,
    EventPage,
    ProblemResponse,
    RunStatus,
    TestRunRequest,
)


MAX_PROBLEM_BYTES = 64 * 1024
MAX_EVENT_PAGE_RESPONSE_BYTES = 2 * 1024 * 1024


class SutClientError(Exception):
    """Safe base error that never includes a response body."""

    code = "SUT_CLIENT_ERROR"


class InvalidWireResponse(SutClientError):
    code = "INVALID_WIRE_RESPONSE"

    def __init__(self, reason: str, raw_bytes: bytes = b"") -> None:
        self.reason = reason
        self.raw_bytes = raw_bytes
        super().__init__(reason)


class SutRemoteError(SutClientError):
    """A validated bounded problem response from the target."""

    def __init__(self, status_code: int, problem: ProblemResponse) -> None:
        self.status_code = status_code
        self.problem = problem
        super().__init__(
            f"target rejected request with {problem.error.code}"
        )


class SutTransportError(SutClientError):
    code = "SUT_TRANSPORT_ERROR"


class SutTimeoutError(SutTransportError):
    """The target request exceeded its explicit HTTP timeout."""

    code = "SUT_TIMEOUT"


class SutClient:
    """Contract-v1-only client with explicit connect/read/write timeouts."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
        forbidden_values: tuple[str, ...] = (),
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._owns_client = client is None
        self._forbidden_bytes = tuple(
            value.encode("utf-8")
            for value in forbidden_values
            if value
        )
        self._client = client or httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(
                timeout_seconds,
                connect=timeout_seconds,
                read=timeout_seconds,
                write=timeout_seconds,
                pool=timeout_seconds,
            ),
            follow_redirects=False,
        )

    async def __aenter__(self) -> SutClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def create_run(
        self,
        request: TestRunRequest,
        *,
        timeout_seconds: float | None = None,
    ) -> AcceptedResponse:
        response = await self._request(
            "POST",
            "/test-runs",
            headers={CONTRACT_VERSIONS_HEADER: CONTRACT_VERSION},
            content=request.model_dump_json(exclude_none=True),
            timeout=timeout_seconds,
        )
        parsed = self._parse_success(response, 202, AcceptedResponse)
        self._validate_accepted(parsed, request)
        return parsed

    async def get_status(
        self,
        run_id: UUID,
        *,
        timeout_seconds: float | None = None,
    ) -> RunStatus:
        response = await self._request(
            "GET",
            f"/test-runs/{run_id}",
            timeout=timeout_seconds,
        )
        parsed = self._parse_success(response, 200, RunStatus)
        if parsed.run_id != run_id:
            raise InvalidWireResponse(
                "status response run identity mismatch",
                response.content,
            )
        if parsed.terminal_result is not None:
            try:
                output_bytes = rfc8785.dumps(
                    parsed.terminal_result.output
                )
            except (TypeError, ValueError):
                raise InvalidWireResponse(
                    "terminal output is not canonicalizable",
                    response.content,
                ) from None
            if len(output_bytes) > MAX_TERMINAL_OUTPUT_BYTES:
                raise InvalidWireResponse(
                    "terminal output exceeded 64 KiB",
                    response.content,
                )
        return parsed

    async def get_events(
        self,
        run_id: UUID,
        *,
        after_producer_seq: int,
        timeout_seconds: float | None = None,
    ) -> EventPage:
        response = await self._request(
            "GET",
            f"/test-runs/{run_id}/events",
            params={"after_producer_seq": after_producer_seq},
            timeout=timeout_seconds,
        )
        if len(response.content) > MAX_EVENT_PAGE_RESPONSE_BYTES:
            raise InvalidWireResponse(
                "event page response is oversized",
                response.content,
            )
        parsed = self._parse_success(response, 200, EventPage)
        if parsed.run_id != run_id:
            raise InvalidWireResponse(
                "event page run identity mismatch",
                response.content,
            )
        return parsed

    async def cancel_run(
        self,
        request: CancellationRequest,
        *,
        timeout_seconds: float | None = None,
    ) -> CancellationAcknowledgement:
        response = await self._request(
            "POST",
            f"/test-runs/{request.run_id}/cancel",
            content=request.model_dump_json(),
            timeout=timeout_seconds,
        )
        parsed = self._parse_success(
            response,
            200,
            CancellationAcknowledgement,
        )
        if (
            parsed.run_id != request.run_id
            or parsed.trace_id != request.trace_id
            or parsed.cancellation_id != request.cancellation_id
        ):
            raise InvalidWireResponse(
                "cancellation acknowledgement identity mismatch",
                response.content,
            )
        return parsed

    async def _request(self, method: str, path: str, **kwargs: object):
        if kwargs.get("timeout") is None:
            kwargs.pop("timeout", None)
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.TimeoutException as error:
            raise SutTimeoutError(
                "target transport request timed out"
            ) from error
        except httpx.NetworkError as error:
            raise SutTransportError(
                "target transport request failed"
            ) from error
        if response.status_code >= 400:
            self._raise_remote(response)
        return response

    def _parse_success(self, response, expected_status: int, model_type):
        if response.status_code != expected_status:
            raise InvalidWireResponse(
                "target returned an unexpected success status",
                response.content,
            )
        self._reject_forbidden_content(response.content)
        selected = response.headers.get(CONTRACT_VERSION_HEADER)
        if selected != CONTRACT_VERSION:
            raise InvalidWireResponse(
                "target contract-version header mismatch",
                response.content,
            )
        try:
            parsed = model_type.model_validate_json(response.content)
        except ValidationError:
            raise InvalidWireResponse(
                "target response failed strict contract validation",
                response.content,
            ) from None
        self._reject_forbidden_content(
            parsed.model_dump_json(exclude_none=True).encode("utf-8")
        )
        if parsed.contract_version != CONTRACT_VERSION:
            raise InvalidWireResponse(
                "target body contract version mismatch",
                response.content,
            )
        return parsed

    def _raise_remote(self, response: httpx.Response) -> None:
        if len(response.content) > MAX_PROBLEM_BYTES:
            raise InvalidWireResponse(
                "target problem response is oversized",
                response.content,
            )
        self._reject_forbidden_content(response.content)
        selected = response.headers.get(CONTRACT_VERSION_HEADER)
        if selected != CONTRACT_VERSION:
            raise InvalidWireResponse(
                "target problem contract-version header mismatch",
                response.content,
            )
        try:
            problem = ProblemResponse.model_validate_json(response.content)
        except ValidationError:
            raise InvalidWireResponse(
                "target problem response is invalid",
                response.content,
            ) from None
        self._reject_forbidden_content(
            problem.model_dump_json(exclude_none=True).encode("utf-8")
        )
        if problem.contract_version != CONTRACT_VERSION:
            raise InvalidWireResponse(
                "target problem body contract version mismatch",
                response.content,
            )
        raise SutRemoteError(response.status_code, problem)

    def _reject_forbidden_content(self, content: bytes) -> None:
        if any(value in content for value in self._forbidden_bytes):
            raise InvalidWireResponse(
                "target response contained forbidden capability content",
                content,
            )

    @staticmethod
    def _validate_accepted(
        accepted: AcceptedResponse,
        request: TestRunRequest,
    ) -> None:
        if (
            accepted.run_id != request.run_id
            or accepted.trace_id != request.trace_id
        ):
            raise InvalidWireResponse(
                "accepted response identity mismatch"
            )
        if accepted.producer_high_watermark != 0:
            raise InvalidWireResponse(
                "accepted response initial watermark is not zero"
            )
        expected_prefix = f"/test-runs/{request.run_id}"
        if (
            accepted.status_url != expected_prefix
            or accepted.events_url != f"{expected_prefix}/events"
            or accepted.cancellation_url != f"{expected_prefix}/cancel"
        ):
            raise InvalidWireResponse(
                "accepted response resource references are invalid"
            )
