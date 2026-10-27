"""Единый формат ошибок: application/problem+json (RFC 9457)."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("psytrainer")

TITLES = {
    400: "Некорректный запрос", 401: "Требуется аутентификация", 403: "Недостаточно прав", 404: "Не найдено",
    405: "Метод не поддерживается", 409: "Конфликт состояния", 412: "Не выполнено предусловие",
    422: "Ошибка валидации", 429: "Слишком много запросов", 500: "Внутренняя ошибка",
    502: "LLM недоступна", 503: "Сервис не готов",
}


class ApiError(Exception):
    def __init__(self, status: int, code: str, detail: str | None = None, *, title: str | None = None,
                 errors: list[dict[str, Any]] | None = None, headers: dict[str, str] | None = None) -> None:
        super().__init__(detail or code)
        self.status, self.code, self.detail = status, code, detail
        self.title = title or TITLES.get(status, "Ошибка")
        self.errors, self.headers = errors, headers or {}


def problem(status: int, code: str, title: str, detail: str | None = None, errors: Any = None,
            headers: dict[str, str] | None = None) -> JSONResponse:
    body: dict[str, Any] = {"type": f"https://psytrainer.example/problems/{code.replace('_', '-')}",
                            "title": title, "status": status, "code": code}
    if detail:
        body["detail"] = detail
    if errors:
        body["errors"] = errors
    return JSONResponse(body, status_code=status, media_type="application/problem+json", headers=headers)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api(_: Request, e: ApiError):
        return problem(e.status, e.code, e.title, e.detail, e.errors, e.headers)

    @app.exception_handler(RequestValidationError)
    async def _val(_: Request, e: RequestValidationError):
        errs = [{"field": ".".join(str(p) for p in x["loc"] if p != "body"), "message": x["msg"]} for x in e.errors()]
        return problem(422, "validation_failed", TITLES[422], "Проверьте поля запроса", errs)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, e: StarletteHTTPException):
        code = {404: "not_found", 405: "method_not_allowed"}.get(e.status_code, "bad_request")
        return problem(e.status_code, code, TITLES.get(e.status_code, "Ошибка"), str(e.detail))

    @app.exception_handler(Exception)
    async def _any(_: Request, e: Exception):
        log.exception("unhandled error", exc_info=e)
        return problem(500, "internal_error", TITLES[500])
