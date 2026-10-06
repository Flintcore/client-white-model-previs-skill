#!/usr/bin/env python3
"""Small authenticated, single-coordinator queue; Python 3.10+, stdlib only.

SQLite is coordinator-local, never a shared/network filesystem database. Worker
completion only enters awaiting_review. This server validates evidence metadata,
not Blender geometry, video pixels, or a reviewer's actual viewing of media.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import secrets
import sqlite3
import struct
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid

TASK_KEYS = ("source_sha256", "template_sha256", "standards_sha256", "skill_revision", "spec", "timeline")
COMPLETION_SCHEMA = "client-previs-completion/v1"
GATE_SCHEMA = "client-white-model-gate.v1"
MAX_BODY = 1024 * 1024


class QueueError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def task_id(task: dict) -> str:
    """Stable ID: source/template/rules/skill pin + exact specification/timeline.

    Paths/URIs outside spec and timeline do not change the ID. Callers must keep
    machine-local paths out of the two identity-bearing objects.
    """
    if not isinstance(task, dict) or any(k not in task for k in TASK_KEYS):
        raise QueueError(400, "task_identity", "Task is missing an identity field.")
    return hashlib.sha256(canonical({k: task[k] for k in TASK_KEYS}).encode("utf-8")).hexdigest()


def _text(value, name: str, max_length=4096) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise QueueError(400, "invalid_field", f"{name} must be a nonempty string.")
    return value


def _sha(value, name: str) -> str:
    value = _text(value, name, 64)
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise QueueError(400, "invalid_digest", f"{name} must be a lowercase SHA-256 digest.")
    return value


def _evidence(value, name: str) -> dict:
    if not isinstance(value, dict):
        raise QueueError(400, "missing_evidence", f"{name} requires sha256 and uri.")
    uri = _text(value.get("uri"), name + ".uri")
    if urlparse(uri).scheme not in {"file", "https", "http", "s3", "gs", "az"} or any(c.isspace() for c in uri):
        raise QueueError(400, "invalid_uri", f"{name}.uri must be an absolute asset-store URI.")
    return {"sha256": _sha(value.get("sha256"), name + ".sha256"), "uri": uri,
            **({"kind": _text(value["kind"], name + ".kind", 100)} if "kind" in value else {})}


def normalize_task(task: dict) -> dict:
    if not isinstance(task, dict):
        raise QueueError(400, "task_type", "task must be an object.")
    for k in ("source_sha256", "template_sha256", "standards_sha256"):
        _sha(task.get(k), k)
    _text(task.get("skill_revision"), "skill_revision", 256)
    if not isinstance(task.get("spec"), dict) or not task["spec"]:
        raise QueueError(400, "task_spec", "spec must be a nonempty object.")
    if not isinstance(task.get("timeline"), dict) or not task["timeline"]:
        raise QueueError(400, "task_timeline", "timeline must be a nonempty object.")
    try:
        identity = task_id(task)
        canonical(task)
    except (ValueError, TypeError) as exc:
        raise QueueError(400, "task_json", "Task contains a noncanonical JSON value.") from exc
    if "job_id" in task and task["job_id"] != identity:
        raise QueueError(400, "job_id_mismatch", "job_id does not match the pinned task identity.")
    return {**task, "job_id": identity}


class Queue:
    """Each operation uses its own connection and BEGIN IMMEDIATE transaction."""
    def __init__(self, db: str | Path, lease_seconds=300, now=time.time):
        if not 3 <= lease_seconds <= 86400:
            raise ValueError("lease_seconds must be between 3 and 86400")
        if str(db).startswith(("\\\\", "//")):
            raise ValueError("Coordinator SQLite must be on local disk, not a UNC/shared-network path")
        self.db = Path(db).expanduser().resolve()
        self.db.parent.mkdir(parents=True, exist_ok=True)
        self.lease_seconds, self.now = lease_seconds, now
        with self.connection() as con:
            con.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY, payload TEXT NOT NULL,
                    standards_sha256 TEXT NOT NULL, skill_revision TEXT NOT NULL,
                    state TEXT NOT NULL, owner TEXT, lease_id TEXT, expires REAL,
                    attempt INTEGER NOT NULL DEFAULT 0, completion TEXT, review TEXT,
                    created REAL NOT NULL, updated REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS eligible_jobs
                    ON jobs(state, standards_sha256, skill_revision, created);
                CREATE TABLE IF NOT EXISTS retries (
                    identity TEXT NOT NULL, operation TEXT NOT NULL, retry_key TEXT NOT NULL,
                    request_digest TEXT NOT NULL, result TEXT NOT NULL,
                    PRIMARY KEY(identity, operation, retry_key)
                );
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL NOT NULL,
                    job_id TEXT, identity TEXT NOT NULL, operation TEXT NOT NULL, details TEXT NOT NULL
                );
            """)

    @contextlib.contextmanager
    def connection(self):
        con = sqlite3.connect(self.db, timeout=30, isolation_level=None)
        con.row_factory = sqlite3.Row
        try:
            con.execute("PRAGMA busy_timeout=30000")
            con.execute("PRAGMA foreign_keys=ON")
            yield con
        finally:
            con.close()

    @contextlib.contextmanager
    def transaction(self):
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                yield con
            except BaseException:
                con.rollback()
                raise
            else:
                con.commit()

    def _event(self, con, identity, op, job_id=None, details=None):
        con.execute("INSERT INTO events(timestamp, job_id, identity, operation, details) VALUES(?,?,?,?,?)",
                    (self.now(), job_id, identity, op, canonical(details or {})))

    def _expire(self, con):
        expired = con.execute("SELECT job_id, owner, lease_id FROM jobs WHERE state='leased' AND expires<=?", (self.now(),)).fetchall()
        for row in expired:
            self._event(con, row["owner"], "lease_expired", row["job_id"], {"lease_id": row["lease_id"]})
        con.execute("UPDATE jobs SET state='queued', owner=NULL, lease_id=NULL, expires=NULL, updated=? WHERE state='leased' AND expires<=?",
                    (self.now(), self.now()))

    def _retry(self, con, identity, operation, body):
        key = _text(body.get("retry_key"), "retry_key", 256)
        digest = hashlib.sha256(canonical(body).encode()).hexdigest()
        row = con.execute("SELECT * FROM retries WHERE identity=? AND operation=? AND retry_key=?", (identity, operation, key)).fetchone()
        if row:
            if row["request_digest"] != digest:
                raise QueueError(409, "retry_payload_changed", "A retry_key was reused with a different request.")
            return json.loads(row["result"]), digest
        return None, digest

    def _cache(self, con, identity, operation, body, digest, result):
        con.execute("INSERT INTO retries VALUES(?,?,?,?,?)", (identity, operation, body["retry_key"], digest, canonical(result)))
        return result

    def _job(self, con, job_id):
        row = con.execute("SELECT * FROM jobs WHERE job_id=?", (_sha(job_id, "job_id"),)).fetchone()
        if row is None:
            raise QueueError(404, "job_missing", "Job was not found.")
        return row

    def _lease(self, con, identity, body):
        row = self._job(con, body.get("job_id"))
        if row["state"] != "leased" or row["owner"] != identity or row["lease_id"] != body.get("lease_id") or row["expires"] <= self.now():
            raise QueueError(409, "stale_lease", "Lease is expired, replaced, or owned by a different identity.")
        return row

    @staticmethod
    def _view(row, include_task=False):
        result = {k: row[k] for k in ("job_id", "state", "owner", "lease_id", "expires", "attempt", "standards_sha256", "skill_revision")}
        if include_task:
            result["task"] = json.loads(row["payload"])
        if row["completion"]:
            result["completion"] = json.loads(row["completion"])
        if row["review"]:
            result["review"] = json.loads(row["review"])
        return result

    def submit(self, identity, body):
        task = normalize_task(body.get("task"))
        with self.transaction() as con:
            cached, digest = self._retry(con, identity, "submit", body)
            if cached is not None:
                return cached
            existing = con.execute("SELECT * FROM jobs WHERE job_id=?", (task["job_id"],)).fetchone()
            if existing is None:
                now = self.now()
                con.execute("INSERT INTO jobs(job_id,payload,standards_sha256,skill_revision,state,created,updated) VALUES(?,?,?,?,?,?,?)",
                            (task["job_id"], canonical(task), task["standards_sha256"], task["skill_revision"], "queued", now, now))
                self._event(con, identity, "submit", task["job_id"])
            result = {"job_id": task["job_id"], "created": existing is None,
                      "state": "queued" if existing is None else existing["state"]}
            return self._cache(con, identity, "submit", body, digest, result)

    def claim(self, identity, body):
        standards = _sha(body.get("standards_sha256"), "standards_sha256")
        revision = _text(body.get("skill_revision"), "skill_revision", 256)
        with self.transaction() as con:
            self._expire(con)
            cached, digest = self._retry(con, identity, "claim", body)
            if cached is not None:
                if cached.get("lease"):
                    old = self._job(con, cached["lease"]["job_id"])
                    if old["owner"] != identity or old["lease_id"] != cached["lease"]["lease_id"] or old["state"] != "leased":
                        raise QueueError(409, "stale_retry", "Claim retry refers to a finished or expired lease; use a new retry_key.")
                    return {"lease": self._view(old, True)}
                return cached
            params = [standards, revision]
            sql = "SELECT * FROM jobs WHERE state IN ('queued','rework') AND standards_sha256=? AND skill_revision=?"
            if body.get("job_id"):
                sql += " AND job_id=?"
                params.append(_sha(body["job_id"], "job_id"))
            row = con.execute(sql + " ORDER BY created, job_id LIMIT 1", params).fetchone()
            result = {"lease": None}
            if row:
                lease_id = str(uuid.uuid4())
                con.execute("UPDATE jobs SET state='leased',owner=?,lease_id=?,expires=?,attempt=attempt+1,completion=NULL,review=NULL,updated=? WHERE job_id=?",
                            (identity, lease_id, self.now() + self.lease_seconds, self.now(), row["job_id"]))
                self._event(con, identity, "claim", row["job_id"], {"lease_id": lease_id})
                result = {"lease": self._view(self._job(con, row["job_id"]), True)}
            return self._cache(con, identity, "claim", body, digest, result)

    def heartbeat(self, identity, body):
        with self.transaction() as con:
            self._expire(con)
            row = self._lease(con, identity, body)
            cached, digest = self._retry(con, identity, "heartbeat", body)
            if cached is not None:
                return cached
            expires = self.now() + self.lease_seconds
            con.execute("UPDATE jobs SET expires=?,updated=? WHERE job_id=?", (expires, self.now(), row["job_id"]))
            result = {"job_id": row["job_id"], "lease_id": row["lease_id"], "state": "leased", "expires": expires}
            self._event(con, identity, "heartbeat", row["job_id"], {"lease_id": row["lease_id"], "expires": expires})
            return self._cache(con, identity, "heartbeat", body, digest, result)

    def complete(self, identity, body):
        report = body.get("completion")
        if not isinstance(report, dict) or report.get("schema") != COMPLETION_SCHEMA or report.get("gate_passed") is not True:
            raise QueueError(400, "completion_gate", "Completion requires its schema and a passed local gate.")
        _sha(report.get("gate_sha256"), "gate_sha256")
        evidence = _evidence(report.get("report"), "report")
        if evidence["sha256"] != report["gate_sha256"]:
            raise QueueError(400, "gate_report_digest", "report must reference the exact passed gate file digest.")
        if not isinstance(report.get("artifacts"), list) or not report["artifacts"]:
            raise QueueError(400, "missing_artifacts", "Completion requires immutable artifact evidence.")
        artifacts = [_evidence(v, f"artifacts[{i}]") for i, v in enumerate(report["artifacts"])]
        with self.transaction() as con:
            self._expire(con)
            cached, digest = self._retry(con, identity, "complete", body)
            if cached is not None:
                current = self._job(con, body.get("job_id"))
                if current["owner"] != identity or current["lease_id"] != body.get("lease_id") or current["state"] not in {"awaiting_review", "accepted", "rework"}:
                    raise QueueError(409, "stale_completion_retry", "Completion retry refers to a replaced lease.")
                return {**cached, "state": current["state"]}
            row = self._lease(con, identity, body)
            if any(report.get(k) != row[k] for k in ("job_id", "standards_sha256", "skill_revision")):
                raise QueueError(409, "completion_pin_mismatch", "Completion evidence does not match this job's pinned identity.")
            record = {**report, "report": evidence, "artifacts": artifacts, "producer": identity,
                      "lease_id": row["lease_id"], "completed_at": self.now()}
            con.execute("UPDATE jobs SET state='awaiting_review',completion=?,expires=NULL,updated=? WHERE job_id=?",
                        (canonical(record), self.now(), row["job_id"]))
            result = {"job_id": row["job_id"], "lease_id": row["lease_id"], "state": "awaiting_review", "gate_sha256": report["gate_sha256"]}
            self._event(con, identity, "complete", row["job_id"], record)
            return self._cache(con, identity, "complete", body, digest, result)

    def review(self, identity, body):
        decision = body.get("decision")
        if decision not in {"accepted", "rework"} or body.get("evidence_reviewed") is not True:
            raise QueueError(400, "review_evidence", "Review requires a decision and evidence_reviewed=true.")
        evidence = _evidence(body.get("review_report"), "review_report")
        gate_sha = _sha(body.get("gate_sha256"), "gate_sha256")
        if decision == "rework":
            _text(body.get("reason"), "reason")
        with self.transaction() as con:
            cached, digest = self._retry(con, identity, "review", body)
            if cached is not None:
                return cached
            row = self._job(con, body.get("job_id"))
            if row["owner"] == identity:
                raise QueueError(403, "self_approval", "The producer identity must differ from the reviewer identity.")
            if row["state"] != "awaiting_review" or row["lease_id"] != body.get("lease_id"):
                raise QueueError(409, "review_state", "Review is restricted to the current awaiting_review evidence.")
            completion = json.loads(row["completion"])
            if gate_sha != completion["gate_sha256"]:
                raise QueueError(409, "review_gate_mismatch", "Review gate digest differs from the completed immutable evidence.")
            record = {"reviewer": identity, "decision": decision, "review_report": evidence,
                      "gate_sha256": gate_sha, "lease_id": row["lease_id"], "reviewed_at": self.now(),
                      "evidence_reviewed": True, "reason": body.get("reason", "")}
            con.execute("UPDATE jobs SET state=?,review=?,updated=? WHERE job_id=?", (decision, canonical(record), self.now(), row["job_id"]))
            self._event(con, identity, "review", row["job_id"], record)
            result = {"job_id": row["job_id"], "lease_id": row["lease_id"], "state": decision, "reviewer": identity}
            return self._cache(con, identity, "review", body, digest, result)

    def status(self, identity, body):
        with self.transaction() as con:
            self._expire(con)
            if body.get("job_id"):
                return {"job": self._view(self._job(con, body["job_id"]), True)}
            counts = {r["state"]: r["n"] for r in con.execute("SELECT state,COUNT(*) AS n FROM jobs GROUP BY state")}
            limit = body.get("limit", 100)
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
                raise QueueError(400, "status_limit", "limit must be an integer between 1 and 1000.")
            rows = con.execute("SELECT * FROM jobs ORDER BY updated DESC,job_id LIMIT ?", (limit,)).fetchall()
            return {"counts": counts, "jobs": [self._view(r) for r in rows], "returned": len(rows), "limit": limit}


def authenticate(auth_path, header):
    if not isinstance(header, str) or not header.startswith("Bearer "):
        raise QueueError(401, "authentication_required", "A bearer credential is required.")
    token = header[7:]
    try:
        identities = json.loads(Path(auth_path).read_text(encoding="utf-8"))
        if not isinstance(identities, dict) or not identities:
            raise ValueError()
        seen = set()
        match = None
        for name, data in identities.items():
            if not isinstance(name, str) or not name or not isinstance(data, dict) or data.get("role") not in {"admin", "worker", "reviewer"}:
                raise ValueError()
            secret = data.get("token")
            if not isinstance(secret, str) or len(secret) < 32 or secret in seen:
                raise ValueError()
            seen.add(secret)
            if hmac.compare_digest(token.encode(), secret.encode()):
                match = {"name": name, "role": data["role"]}
        if match is None:
            raise QueueError(401, "invalid_credential", "Credential was not recognized.")
        return match
    except QueueError:
        raise
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise QueueError(503, "authentication_configuration", "Authentication configuration requires operator attention.") from exc


ROLES = {"submit": {"admin"}, "claim": {"worker"}, "heartbeat": {"worker"},
         "complete": {"worker"}, "review": {"reviewer"}, "status": {"admin", "worker", "reviewer"}}


def create_server(queue: Queue, auth_path, host="127.0.0.1", port=8765):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ClientPrevisQueue/1"

        def setup(self):
            super().setup()
            self.connection.settimeout(30)

        def log_message(self, format, *args):
            # Deliberately omit request bodies, credentials, and asset URIs.
            pass

        def respond(self, status, payload):
            encoded = canonical(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            if status == 401:
                self.send_header("WWW-Authenticate", "Bearer")
            self.end_headers()
            self.wfile.write(encoded)

        def do_POST(self):
            try:
                operation = self.path.strip("/")
                if operation not in ROLES:
                    raise QueueError(404, "endpoint_missing", "Endpoint was not found.")
                if self.headers.get("Transfer-Encoding"):
                    raise QueueError(400, "request_encoding", "Chunked requests are not supported.")
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError as exc:
                    raise QueueError(400, "request_length", "Content-Length must be an integer.") from exc
                if not 0 < length <= MAX_BODY:
                    raise QueueError(413, "request_size", "JSON request must be between 1 byte and 1 MiB.")
                # Drain the bounded body before returning an authentication error.
                # Closing a Windows socket with unread POST data can reset it
                # before the client receives the JSON denial response.
                raw_body = self.rfile.read(length)
                actor = authenticate(auth_path, self.headers.get("Authorization"))
                if actor["role"] not in ROLES[operation]:
                    raise QueueError(403, "role_denied", "This identity's role does not permit the operation.")
                try:
                    body = json.loads(raw_body.decode("utf-8"),
                                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
                except (ValueError, UnicodeDecodeError) as exc:
                    raise QueueError(400, "request_json", "Request must contain valid finite UTF-8 JSON.") from exc
                if not isinstance(body, dict):
                    raise QueueError(400, "request_object", "Request JSON must be an object.")
                self.respond(200, getattr(queue, operation)(actor["name"], body))
            except QueueError as exc:
                self.respond(exc.status, {"error": exc.code, "message": exc.message})
            except Exception:
                self.respond(500, {"error": "internal_error", "message": "Coordinator requires operator attention."})

        def do_GET(self):
            self.respond(405, {"error": "method", "message": "Use an authenticated POST request."})

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def api_request(server, operation, body, token, timeout=30):
    parsed = urlparse(server)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("server must be an http(s) URL")
    request = Request(server.rstrip("/") + "/" + operation, data=canonical(body).encode("utf-8"),
                      headers={"Content-Type": "application/json", "Authorization": "Bearer " + token}, method="POST")
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            # A proxy/server redirect must not forward a bearer token elsewhere.
            return None

    try:
        with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            payload = {"error": "http_error", "message": str(exc.code)}
        raise QueueError(exc.code, payload.get("error", "http_error"), payload.get("message", "HTTP error")) from exc


def init_auth(path, workers=None, reviewers=None, admins=None):
    path = Path(path).expanduser().resolve()
    repository = Path(__file__).resolve().parents[3]
    if path == repository or repository in path.parents:
        raise ValueError("Authentication files must be stored outside the skill repository")
    groups = {"worker": workers or ["worker-1"], "reviewer": reviewers or ["reviewer-1"], "admin": admins or ["admin-1"]}
    identities = {}
    for role, names in groups.items():
        for name in names:
            _text(name, "identity", 100)
            if name in identities:
                raise ValueError("Each identity must have one distinct name and one role")
            identities[name] = {"role": role, "token": secrets.token_urlsafe(32)}
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic exclusive creation: never overwrite the existing credentials.
    with path.open("x", encoding="utf-8") as file:
        file.write(json.dumps(identities, indent=2) + "\n")
    if os.name != "nt":
        path.chmod(0o600)
    return {"auth_file": str(path), "identities": {n: v["role"] for n, v in identities.items()},
            "note": "Distribute only each member's own credential through a private channel."}


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_render_manifest(manifest_path, lease, expected_sha256):
    """Recheck every immutable native PNG; do not expand the HTTP payload.

    The expected frame range and dimensions come from the claimed canonical
    task, not solely from the manifest's self-reported values.
    """
    path = Path(manifest_path).resolve()
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("Render manifest changed after the passed local gate")
    manifest = json.loads(raw.decode("utf-8"))
    if (manifest.get("schema") != "client-white-model-render.v1" or
            manifest.get("status") != "COMPLETE" or
            manifest.get("resolution_percentage") != 100 or
            manifest.get("upscaled") is not False):
        raise ValueError("Native rendering must be complete, unscaled, and at 100 percent")
    for key in ("job_id", "standards_sha256", "skill_revision"):
        if manifest.get(key) != lease.get(key):
            raise ValueError(f"Render manifest {key} differs from the claimed lease")
    task = lease.get("task", {})
    timeline = task.get("timeline", {})
    spec = task.get("spec", {})
    start = timeline.get("frame_start", timeline.get("start_frame"))
    end = timeline.get("frame_end")
    if end is None and isinstance(start, int) and isinstance(timeline.get("frame_count"), int):
        end = start + timeline["frame_count"] - 1
    if (isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, int) or
            not isinstance(end, int) or start < 0 or end < start):
        raise ValueError("Claimed task must pin its exact render frame range")
    count = end - start + 1
    render_spec = spec.get("render", spec)
    dims = (render_spec.get("width"), render_spec.get("height"))
    if any(isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in dims):
        raise ValueError("Claimed task must pin positive native render dimensions")
    if (manifest.get("frame_start"), manifest.get("frame_end")) != (start, end):
        raise ValueError("Render manifest frame range differs from the claimed task")
    if (manifest.get("width"), manifest.get("height")) != dims:
        raise ValueError("Render manifest dimensions differ from the claimed task")
    completed = manifest.get("completed")
    if not isinstance(completed, list) or len(completed) != count:
        raise ValueError("Render manifest completed count differs from the assigned frame count")
    root = path.parent
    verified = set()
    for frame, entry in zip(range(start, end + 1), completed):
        if (not isinstance(entry, dict) or type(entry.get("frame")) is not int or
                entry.get("frame") != frame):
            raise ValueError("Render manifest frame sequence has a gap, duplicate, or wrong order")
        relative = entry.get("path")
        if not isinstance(relative, str) or not relative or "\x00" in relative:
            raise ValueError("Native PNG requires a bounded relative path")
        portable = PurePosixPath(relative.replace("\\", "/"))
        if portable.is_absolute() or PureWindowsPath(relative).drive or ".." in portable.parts:
            raise ValueError("Native PNG path must stay within the render directory")
        if portable.name != f"frame_{frame:04d}.png":
            raise ValueError("Native PNG filename does not match its assigned frame")
        target = root.joinpath(*portable.parts).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ValueError("Native PNG resolved outside the render directory") from exc
        if target in verified or not target.is_file():
            raise ValueError("Native PNG is missing or duplicates another completed entry")
        if (entry.get("width"), entry.get("height")) != dims:
            raise ValueError("Native PNG entry dimensions differ from the claimed task")
        expected = _sha(entry.get("sha256"), "native_png.sha256")
        digest = hashlib.sha256()
        with target.open("rb") as file:
            header = file.read(24)
            digest.update(header)
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
        if (len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or
                header[8:16] != b"\x00\x00\x00\x0dIHDR" or struct.unpack(">II", header[16:24]) != dims):
            raise ValueError("Actual native PNG header/dimensions differ from the claimed task")
        if digest.hexdigest() != expected:
            raise ValueError("Native PNG changed after the passed local gate; rerun the gate")
        verified.add(target)
    # This is a dedicated render directory. Extra PNGs indicate mixed output.
    discovered = [p.resolve() for p in root.rglob("*") if p.is_file() and p.suffix.lower() == ".png"]
    if len(discovered) != count or set(discovered) != verified:
        raise ValueError("Actual PNG inventory differs from the exact manifest sequence")
    if _file_sha256(path) != expected_sha256:
        raise ValueError("Render manifest changed while verifying its PNG inventory")
    return {"verified_png_count": count, "frame_start": start, "frame_end": end}


def completion_from_gate(gate_path, lease, report_uri=None, artifact_uris=None):
    """Adapter for the local real gate, not a replacement for running that gate."""
    raw = Path(gate_path).read_bytes()
    gate = json.loads(raw.decode("utf-8"))
    if gate.get("schema") != GATE_SCHEMA or gate.get("passed") is not True:
        raise ValueError("Run the local production gate to a passed report before completing a lease")
    for key in ("job_id", "standards_sha256", "skill_revision"):
        if gate.get(key) != lease.get(key):
            raise ValueError(f"Gate {key} differs from the claimed lease")
    if not gate.get("artifacts"):
        raise ValueError("Passed gate must include artifact digest evidence")
    artifacts = []
    for entry in gate["artifacts"]:
        if entry.get("kind") == "renders" and not entry.get("path"):
            raise ValueError("Native render manifest requires a local path for pre-submission PNG verification")
        if entry.get("path"):
            local = Path(entry["path"])
            if not local.is_absolute() or not local.is_file():
                raise ValueError("Local artifact path must exist before completion")
            if _file_sha256(local) != entry.get("sha256"):
                raise ValueError("Artifact changed after the passed local gate; rerun the gate")
            if entry.get("kind") == "renders":
                verify_render_manifest(local, lease, entry["sha256"])
        override = (artifact_uris or {}).get(entry.get("kind"))
        uri = override or entry.get("uri")
        if not uri:
            path = Path(entry.get("path", ""))
            if not path.is_absolute():
                raise ValueError("Artifact needs an absolute path or shared-store URI")
            uri = path.resolve().as_uri()
        artifacts.append(_evidence({"kind": entry.get("kind", "artifact"), "sha256": entry.get("sha256"), "uri": uri}, "artifact"))
    digest = hashlib.sha256(raw).hexdigest()
    return {"schema": COMPLETION_SCHEMA, "job_id": gate["job_id"], "standards_sha256": gate["standards_sha256"],
            "skill_revision": gate["skill_revision"], "gate_passed": True, "gate_sha256": digest,
            "report": {"sha256": digest, "uri": report_uri or Path(gate_path).resolve().as_uri()}, "artifacts": artifacts}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    default_home = Path.home() / ".client-white-model-previs"
    p = commands.add_parser("init-auth")
    p.add_argument("--auth-file", default=str(default_home / "auth.json"))
    for role in ("worker", "reviewer", "admin"):
        p.add_argument("--" + role, action="append")
    p = commands.add_parser("server")
    p.add_argument("--db", default=str(default_home / "queue.sqlite3"))
    p.add_argument("--auth-file", default=str(default_home / "auth.json"))
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--lease-seconds", type=int, default=300)
    p.add_argument("--allow-insecure-lan", action="store_true", help="Explicitly opt into direct HTTP on a trusted isolated LAN; prefer a TLS proxy")
    for command in ("enqueue", "claim", "heartbeat", "complete", "review", "status"):
        p = commands.add_parser(command)
        p.add_argument("--server", default=os.environ.get("CLIENT_PREVIS_SERVER", "http://127.0.0.1:8765"))
        p.add_argument("--token-env", default="CLIENT_PREVIS_TOKEN")
        p.add_argument("--output")
        if command != "status":
            p.add_argument("--retry-key", help="Reuse the same key AND payload after an uncertain network outcome")
        if command == "enqueue":
            p.add_argument("--task", required=True)
        elif command == "claim":
            p.add_argument("--standards-sha256", required=True)
            p.add_argument("--skill-revision", required=True)
            p.add_argument("--job-id")
        elif command in {"heartbeat", "complete", "review"}:
            p.add_argument("--lease", required=True)
            if command == "complete":
                choice = p.add_mutually_exclusive_group(required=True)
                choice.add_argument("--completion")
                choice.add_argument("--gate")
                p.add_argument("--report-uri")
                p.add_argument("--artifact-uris", help="JSON object mapping artifact kind to a shared-store URI")
            if command == "review":
                p.add_argument("--review", required=True, help="Review JSON with decision, gate_sha256, review_report, evidence_reviewed, and reason for rework")
        elif command == "status":
            p.add_argument("--job-id")
            p.add_argument("--limit", type=int, default=100)
    args = parser.parse_args(argv)
    body = None
    try:
        if args.command == "init-auth":
            result = init_auth(args.auth_file, args.worker, args.reviewer, args.admin)
        elif args.command == "server":
            try:
                loopback = ipaddress.ip_address(args.host).is_loopback
            except ValueError:
                loopback = args.host.lower() == "localhost"
            if not loopback and not args.allow_insecure_lan:
                raise ValueError("Keep the server on loopback behind an HTTPS proxy, or explicitly opt into an isolated HTTP LAN")
            # Validate the auth file before binding, without publishing any token.
            data = load_json(args.auth_file)
            if not isinstance(data, dict) or not data:
                raise ValueError("Authentication configuration must contain distinct identities")
            authenticate(args.auth_file, "Bearer " + next(iter(data.values()))["token"])
            queue = Queue(args.db, args.lease_seconds)
            server = create_server(queue, args.auth_file, args.host, args.port)
            print(canonical({"listening": f"http://{args.host}:{server.server_address[1]}", "db": str(queue.db),
                             "note": "Local metadata coordinator; workers and reviewers must inspect private asset evidence."}), flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
            return 0
        else:
            token = os.environ.get(args.token_env)
            if not token:
                raise ValueError(f"Set {args.token_env} to this identity's private bearer credential")
            body = {}
            if args.command != "status":
                body["retry_key"] = args.retry_key or str(uuid.uuid4())
            if args.command == "enqueue":
                body["task"] = load_json(args.task)
            elif args.command == "claim":
                body.update(standards_sha256=args.standards_sha256, skill_revision=args.skill_revision)
                if args.job_id:
                    body["job_id"] = args.job_id
            elif args.command in {"heartbeat", "complete", "review"}:
                loaded = load_json(args.lease)
                lease = loaded.get("lease", loaded.get("job", loaded))
                if not isinstance(lease, dict):
                    raise ValueError("Lease file contains no claimed job")
                body.update(job_id=lease["job_id"], lease_id=lease["lease_id"])
                if args.command == "complete":
                    body["completion"] = (load_json(args.completion) if args.completion else
                                          completion_from_gate(args.gate, lease, args.report_uri,
                                                               load_json(args.artifact_uris) if args.artifact_uris else None))
                if args.command == "review":
                    body.update(load_json(args.review))
                    # A review document must not redirect its lease or retry identity.
                    body.update(job_id=lease["job_id"], lease_id=lease["lease_id"], retry_key=args.retry_key or body["retry_key"])
            else:
                body.update(limit=args.limit)
                if args.job_id:
                    body["job_id"] = args.job_id
            result = api_request(args.server, "submit" if args.command == "enqueue" else args.command, body, token)
            if args.command != "status":
                result["request_retry_key"] = body["retry_key"]
            if args.output:
                output = Path(args.output)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except QueueError as exc:
        print(canonical({"error": exc.code, "message": exc.message, "http_status": exc.status,
                         **({"request_retry_key": body["retry_key"]} if body and "retry_key" in body else {})}), file=sys.stderr)
    except (OSError, ValueError, KeyError, URLError) as exc:
        print(canonical({"error": "command_failed", "message": str(exc),
                         **({"request_retry_key": body["retry_key"]} if body and "retry_key" in body else {})}), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
