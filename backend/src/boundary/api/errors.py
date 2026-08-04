"""Bounded public problem details."""

from __future__ import annotations

from fastapi.responses import JSONResponse

from boundary.api.models import ProblemDetail


class PublicProblem(Exception):
    def __init__(self, status: int, code: str, detail: str) -> None:
        self.status = status
        self.code = code[:128]
        self.detail = detail[:512]
        super().__init__(self.detail)


def problem_response(status: int, code: str, detail: str) -> JSONResponse:
    titles = {
        404: "Not Found",
        409: "Conflict",
        422: "Unprocessable Content",
        500: "Internal Server Error",
        503: "Service Unavailable",
    }
    body = ProblemDetail(
        title=titles.get(status, "Request Failed"),
        status=status,
        code=code,
        detail=detail,
    )
    return JSONResponse(
        status_code=status,
        content=body.model_dump(mode="json"),
        media_type="application/problem+json",
    )
