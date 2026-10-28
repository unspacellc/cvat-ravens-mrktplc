"""Versioned adapter over the CVAT REST API (pinned: v2.80.0). The only module that talks HTTP to CVAT."""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

ADAPTER_VERSION = "cvat-2.80"


class CvatError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, retryable: bool = True):
        super().__init__(message)
        self.status, self.retryable = status, retryable


class CvatClient:
    def __init__(self, base_url: str, token: str, *, timeout: float = 60, transport: httpx.BaseTransport | None = None):
        self._http = httpx.Client(base_url=base_url, headers={"Authorization": f"Token {token}"}, timeout=timeout, transport=transport)

    def close(self) -> None:
        self._http.close()

    def _req(self, method: str, url: str, *, org: str | None = None, ok=(200, 201, 202, 204), **kw) -> httpx.Response:
        headers = kw.pop("headers", {})
        if org:
            headers["X-Organization"] = org
        try:
            r = self._http.request(method, url, headers=headers, **kw)
        except httpx.HTTPError as e:  # network / timeout: ambiguous outcome, caller must reconcile before retrying writes
            raise CvatError(f"{method} {url}: {e}", None, True) from e
        if r.status_code not in ok:
            raise CvatError(f"{method} {url} -> {r.status_code}: {r.text[:300]}", r.status_code, r.status_code >= 500 or r.status_code in (408, 429))
        return r

    # --- organizations / members -------------------------------------------------
    def ensure_org(self, slug: str) -> dict:
        found = self._req("GET", "/api/organizations", params={"search": slug}).json()["results"]
        for o in found:
            if o["slug"] == slug:
                return o
        return self._req("POST", "/api/organizations", json={"slug": slug, "name": slug}).json()

    def ensure_member(self, slug: str, username: str, role: str, email: str = "") -> dict:
        return self._req("PUT", f"/api/labeling-guard/organizations/{slug}/members",
                         json={"username": username, "role": role, "email": email}).json()

    def revoke_member(self, slug: str, username: str) -> None:
        self._req("DELETE", f"/api/labeling-guard/organizations/{slug}/members", json={"username": username}, ok=(204, 404))

    # --- webhooks ----------------------------------------------------------------
    def ensure_webhook(self, org: str, target_url: str, secret: str, events: list[str]) -> dict:
        for h in self._req("GET", "/api/webhooks", org=org).json()["results"]:
            if h["target_url"] == target_url and h["type"] == "organization":
                if sorted(h["events"]) != sorted(events) or not h["is_active"]:
                    return self._req("PATCH", f"/api/webhooks/{h['id']}", org=org, json={"events": events, "is_active": True, "secret": secret}).json()
                return h
        return self._req("POST", "/api/webhooks", org=org, json={
            "target_url": target_url, "type": "organization", "content_type": "application/json",
            "events": events, "secret": secret, "is_active": True, "enable_ssl": False}).json()

    # --- tasks -------------------------------------------------------------------
    def find_task_by_name(self, org: str, name: str) -> dict | None:
        flt = json.dumps({"==": [{"var": "name"}, name]})
        res = self._req("GET", "/api/tasks", org=org, params={"filter": flt}).json()["results"]
        return res[0] if res else None

    def create_task(self, org: str, name: str, labels: list[dict], segment_size: int) -> dict:
        # overlap=0 is mandatory: CVAT's default makes video jobs overlap (ADR 0001).
        return self._req("POST", "/api/tasks", org=org, json={"name": name, "labels": labels, "segment_size": segment_size, "overlap": 0}).json()

    def get_task(self, org: str, task_id: int) -> dict:
        return self._req("GET", f"/api/tasks/{task_id}", org=org).json()

    def upload_video(self, org: str, task_id: int, clip: Path, **params) -> str:
        with clip.open("rb") as fh:
            r = self._req("POST", f"/api/tasks/{task_id}/data", org=org,
                          data={"image_quality": "70", **{k: str(v) for k, v in params.items()}},
                          files={"client_files[0]": (clip.name, fh, "video/mp4")})
        return r.json()["rq_id"]

    def task_status(self, org: str, task_id: int) -> dict:
        return self._req("GET", f"/api/tasks/{task_id}/status", org=org).json()

    def wait_ingested(self, org: str, task_id: int, timeout: float = 300, poll: float = 1.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            st = self.task_status(org, task_id)
            if st["state"] == "Finished":
                return st
            if st["state"] == "Failed":
                raise CvatError(f"ingestion failed: {st.get('message')}", None, False)
            time.sleep(poll)
        raise CvatError(f"ingestion of task {task_id} timed out", None, True)

    def delete_task(self, org: str, task_id: int) -> None:
        self._req("DELETE", f"/api/tasks/{task_id}", org=org, ok=(204, 404))

    def list_jobs(self, org: str, task_id: int) -> list[dict]:
        out, page = [], 1
        while True:
            body = self._req("GET", "/api/jobs", org=org, params={"task_id": task_id, "type": "annotation", "page": page, "page_size": 100}).json()
            out += body["results"]
            if not body.get("next"):
                break
            page += 1
        return sorted(out, key=lambda j: j["start_frame"])

    def get_job(self, org: str, job_id: int) -> dict:
        return self._req("GET", f"/api/jobs/{job_id}", org=org).json()

    def set_job_assignee(self, org: str, job_id: int, cvat_user_id: int | None) -> dict:
        return self._req("PATCH", f"/api/jobs/{job_id}", org=org, json={"assignee": cvat_user_id}).json()
