"""Same-origin browser confirmation endpoints for lifecycle maintenance."""

from __future__ import annotations

import secrets
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, StrictBool

from cnequity.file_lock import LockUnavailable
from cnequity.serve.storage import StorageMaintenance
from cnequity.storage.lifecycle import LifecycleError
from cnequity.storage.revisions import RevisionConsistencyError


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["revisions", "experiments"]
    phase: Literal["mark", "purge"] = "purge"
    resume_plan_id: str | None = None


class Confirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review_id: str
    confirmation_token: str
    confirmed: StrictBool
    maintenance_confirmed: StrictBool = False


def check_browser(request: Request, service: StorageMaintenance, *, mutation: bool) -> None:
    # Restrict anonymous local access by Host as well as bind address to prevent
    # a hostile page from obtaining the CSRF token through DNS rebinding.
    host = request.headers.get("host", "")
    if not request.app.state.token and urlsplit("//" + host).hostname not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise HTTPException(403, "存储运维请从 localhost 或 127.0.0.1 打开；远程访问须配置令牌。")
    if mutation:
        origin = request.headers.get("origin", "")
        expected = f"{request.url.scheme}://{host}"
        if origin != expected or request.headers.get("sec-fetch-site") not in (None, "same-origin"):
            raise HTTPException(403, "仅允许从当前运维网页提交确认。")
        supplied = request.headers.get("x-cne-storage-csrf", "")
        if not secrets.compare_digest(supplied, service.csrf):
            raise HTTPException(403, "页面确认凭据已失效，请刷新页面。")


def install_storage_routes(app: FastAPI, service: StorageMaintenance) -> None:
    @app.get("/api/storage")
    def storage_summary(request: Request) -> dict:
        check_browser(request, service, mutation=False)
        try:
            return service.summary()
        except (LifecycleError, RevisionConsistencyError, OSError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/storage/jobs/{job_id}")
    def storage_job(job_id: str, request: Request) -> dict:
        check_browser(request, service, mutation=False)
        try:
            return service.job(job_id)
        except KeyError as exc:
            raise HTTPException(404, "检查记录已过期，请重新检查。") from exc

    @app.post("/api/storage/reviews", status_code=202)
    def storage_review(body: ReviewRequest, request: Request) -> dict:
        check_browser(request, service, mutation=True)
        try:
            return service.review(body.kind, body.phase, body.resume_plan_id)
        except (LifecycleError, LockUnavailable) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/storage/confirm", status_code=202)
    def storage_confirm(body: Confirmation, request: Request) -> dict:
        check_browser(request, service, mutation=True)
        try:
            return service.confirm(
                body.review_id,
                body.confirmation_token,
                confirmed=body.confirmed,
                maintenance=body.maintenance_confirmed,
            )
        except (LifecycleError, LockUnavailable) as exc:
            raise HTTPException(409, str(exc)) from exc
