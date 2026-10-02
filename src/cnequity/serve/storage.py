"""Storage notices and explicit, short-lived browser approvals.

GETs never plan, mark or delete. Only an approved review can invoke the existing
lifecycle executor. External readers still require operator maintenance consent.
"""

from __future__ import annotations

import copy
import secrets
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from cnequity.config import Config
from cnequity.storage.lifecycle import LifecycleError, LifecycleStore, _read, digest
from cnequity.storage.lifecycle_experiments import ExperimentRetirement
from cnequity.storage.lifecycle_purge import eligible, unfinished

Kind = Literal["revisions", "experiments"]
Phase = Literal["mark", "purge"]
APPROVAL_SECONDS = 600


class MaintenanceGate:
    """Drain this server's requests and background scans before deletion."""

    def __init__(self):
        self.condition = threading.Condition()
        self.readers = 0
        self.closed = False

    def enter(self) -> bool:
        with self.condition:
            if self.closed:
                return False
            self.readers += 1
            return True

    def leave(self) -> None:
        with self.condition:
            self.readers -= 1
            self.condition.notify_all()

    def close(self) -> None:
        with self.condition:
            self.closed = True

    def drain(self, timeout: float = 30) -> None:
        with self.condition:
            if not self.condition.wait_for(lambda: self.readers == 0, timeout=timeout):
                raise LifecycleError("面板仍有读取任务，请等待结束后重新确认。")

    def reopen(self) -> None:
        with self.condition:
            self.closed = False
            self.condition.notify_all()


class StorageMaintenance:
    def __init__(self, config: Config, *, invalidate=lambda: None):
        self.config = config
        self.store = LifecycleStore(config.meta_root)
        self.gate = MaintenanceGate()
        self.csrf = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.jobs: dict[str, dict] = {}
        self.busy = False
        self.invalidate = invalidate
        self.last_summary: dict | None = None

    def summary(self) -> dict:
        # A page reload during deletion must show progress without scanning a
        # directory tree that the executor is currently moving/removing.
        if not self.gate.enter():
            with self.lock:
                if self.last_summary is None:
                    raise LifecycleError("维护正在执行，请通过操作记录查看进度。")
                return copy.deepcopy(self.last_summary) | {
                    "active_jobs": [
                        self._public(j)
                        for j in self.jobs.values()
                        if j["status"] in {"checking", "executing"}
                    ],
                    "inventory_paused": True,
                }
        try:
            result = self._summary()
            with self.lock:
                self.last_summary = copy.deepcopy(result)
            return result
        finally:
            self.gate.leave()

    def _summary(self) -> dict:
        now = datetime.now(timezone.utc)
        report = self.store.inspect()
        registry = self.store.registry()
        objects = []
        for obj in report["objects"]:
            pending = obj["pending"]
            # Overview only: don't hash generations or create a cleanup plan.
            candidate = {
                **obj,
                "tree_signature": pending.get("tree_signature") if pending else None,
            }
            status = (
                "protected"
                if obj["blocked_reasons"]
                else ("due" if eligible(candidate, now) else "observing" if pending else "unmarked")
            )
            objects.append(
                {
                    "object_id": obj["object_id"],
                    "kind": "revisions",
                    "label": f"{obj['dataset']} · 第 {obj['revision']} 版",
                    "status": status,
                    "logical_bytes": obj["logical_bytes"],
                    "not_before": pending.get("not_before") if pending else None,
                    "reasons": obj["blocked_reasons"],
                }
            )
        for obj in report["experiments"]:
            oid = obj["object_id"]
            pending = report["experiment_pending"].get(oid)
            reasons = []
            if obj.get("status") == "active":
                reasons.append("active_experiment")
            if obj.get("references"):
                reasons.append("referenced_path")
            if oid in report["resource_holds"]:
                reasons.append("explicit_hold")
            if not any(a["source_object_id"] == oid for a in report["artifacts"].values()):
                reasons.append("archive_required")
            if not Path(obj["path"]).is_dir():
                reasons.append("source_missing")
            status = (
                "protected"
                if reasons
                else (
                    "due"
                    if pending
                    and ExperimentRetirement._mature(
                        obj | {"source_signature": pending["source_signature"]}, {oid: pending}
                    )
                    else "observing"
                    if pending
                    else "unmarked"
                )
            )
            objects.append(
                {
                    "object_id": oid,
                    "kind": "experiments",
                    "label": Path(obj["path"]).name,
                    "status": status,
                    "logical_bytes": obj.get("logical_bytes", 0),
                    "not_before": pending.get("not_before") if pending else None,
                    "reasons": reasons,
                }
            )
        events = [
            {
                "kind": "experiments" if p.parent.name == "experiment-purges" else "revisions",
                "plan_id": p.stem,
            }
            for p in unfinished(self.store)
        ]
        root = self.config.data_root
        while not root.exists() and root != root.parent:
            root = root.parent
        usage = shutil.disk_usage(root)
        with self.lock:
            active = [
                self._public(j)
                for j in self.jobs.values()
                if j["status"] in {"checking", "executing"}
            ]
        return {
            "mode": "web_confirmation_only",
            "registered": registry is not None,
            "csrf_token": self.csrf,
            "objects": objects,
            "unfinished": events,
            "active_jobs": active,
            "disk_free_bytes": usage.free,
            "disk_total_bytes": usage.total,
            "keep": 5,
            "grace_days": 7,
            "generated_at": now.isoformat(),
        }

    @staticmethod
    def _public(job: dict) -> dict:
        return copy.deepcopy({k: v for k, v in job.items() if not k.startswith("_")})

    def job(self, job_id: str) -> dict:
        with self.lock:
            if job_id not in self.jobs:
                raise KeyError(job_id)
            return self._public(self.jobs[job_id])

    def _start(self, state: dict, work) -> dict:
        with self.lock:
            if self.busy:
                raise LifecycleError("已有存储检查或清理正在执行，请等待结束。")
            # Completed reviews are short lived and never become an unbounded queue.
            for key, job in list(self.jobs.items()):
                if time.monotonic() - job["_created"] > APPROVAL_SECONDS and job["status"] not in {
                    "checking",
                    "executing",
                }:
                    del self.jobs[key]
            if len(self.jobs) >= 64:
                raise LifecycleError("检查请求过多，请稍后重试。")
            job_id = secrets.token_hex(16)
            job = {"job_id": job_id, "_created": time.monotonic(), **state}
            self.jobs[job_id] = job
            self.busy = True
            initial = self._public(job)

        def run():
            try:
                result = work()
                with self.lock:
                    job.update(result)
            except Exception as exc:
                with self.lock:
                    job.update(
                        status="error",
                        error=str(exc),
                        message="操作停止。执行失败时可能有部分内容已删除，请检查记录并重新审核原计划。"
                        if state["status"] == "executing"
                        else "检查未通过，没有执行删除。",
                    )
            finally:
                with self.lock:
                    try:
                        if state["status"] == "executing":
                            self.invalidate()
                    finally:
                        self.gate.reopen()
                        self.busy = False

        try:
            threading.Thread(target=run, name="storage-maintenance", daemon=True).start()
        except BaseException:
            with self.lock:
                self.busy = False
                del self.jobs[job_id]
            self.gate.reopen()
            raise
        return initial

    def review(self, kind: Kind, phase: Phase, resume_plan_id: str | None = None) -> dict:
        def prepare():
            if resume_plan_id:
                if phase != "purge" or not any(
                    p.stem == resume_plan_id
                    and (p.parent.name == "experiment-purges") == (kind == "experiments")
                    for p in unfinished(self.store)
                ):
                    raise LifecycleError("只能重新审核同一未完成删除记录。")
                if kind == "revisions":
                    plan = self.store.read_plan(resume_plan_id)
                else:
                    plan = _read(self.store.root / "experiment-plans" / f"{resume_plan_id}.json")
                    if digest(plan) != resume_plan_id or plan.get("meta_root") != str(
                        self.store.meta
                    ):
                        raise LifecycleError("试验清理计划已改变。")
                if plan["phase"] != "purge":
                    raise LifecycleError("无效的恢复计划。")
                plan = {**plan, "plan_id": resume_plan_id}
            else:
                plan = (
                    self.store.plan(phase=phase)
                    if kind == "revisions"
                    else ExperimentRetirement(self.store).plan(phase=phase)
                )
            if kind == "revisions":
                selected = set(plan["purge_ids"] if phase == "purge" else plan["candidate_ids"])
                objects = [o for o in plan["objects"] if o["object_id"] in selected]
            else:
                objects = plan["objects"]
            return {
                "status": "ready",
                "plan_id": plan["plan_id"],
                "objects": [
                    {
                        "object_id": o["object_id"],
                        "label": f"{o['dataset']} · 第 {o['revision']} 版"
                        if kind == "revisions"
                        else Path(o["path"]).name,
                        "logical_bytes": o["logical_bytes"],
                    }
                    for o in objects
                ],
                "logical_bytes": sum(o["logical_bytes"] for o in objects),
                "confirmation_token": secrets.token_urlsafe(32),
                "_expires": time.monotonic() + APPROVAL_SECONDS,
                "expires_in_seconds": APPROVAL_SECONDS,
                "resuming": bool(resume_plan_id),
            }

        return self._start({"status": "checking", "kind": kind, "phase": phase}, prepare)

    def confirm(
        self, review_id: str, confirmation_token: str, *, confirmed: bool, maintenance: bool
    ) -> dict:
        with self.lock:
            review = self.jobs.get(review_id)
            if not review or review["status"] != "ready" or review.get("_used"):
                raise LifecycleError("检查已失效或已经确认，请重新检查。")
            if not confirmed or not secrets.compare_digest(
                confirmation_token, review["confirmation_token"]
            ):
                raise LifecycleError("缺少与本次检查匹配的网页确认。")
            if time.monotonic() > review["_expires"]:
                raise LifecycleError("确认已过期，请重新检查。")
            if not review["objects"]:
                raise LifecycleError("本次没有可执行的对象。")
            if review["phase"] == "purge" and not maintenance:
                raise LifecycleError("请先确认外部查询、服务、调度和试验写入已停止。")
            if self.busy:
                raise LifecycleError("已有存储操作正在执行。")
            review["_used"] = True
            self.gate.close()

            def execute():
                self.gate.drain()
                plan_id = review["plan_id"]
                if review["kind"] == "revisions":
                    result = (
                        self.store.purge(
                            plan_id, maintenance=True, manifest=self.config.manifest_path
                        )
                        if review["phase"] == "purge"
                        else self.store.mark(plan_id)
                    )
                else:
                    result = ExperimentRetirement(self.store).apply(
                        plan_id, maintenance=maintenance, manifest=self.config.manifest_path
                    )
                return {"status": "complete", "result": result}

            try:
                return self._start(
                    {
                        "status": "executing",
                        "kind": review["kind"],
                        "phase": review["phase"],
                        "plan_id": review["plan_id"],
                    },
                    execute,
                )
            except BaseException:
                review["_used"] = False
                self.gate.reopen()
                raise
