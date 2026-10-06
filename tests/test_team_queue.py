"""Real localhost HTTP tests; no client assets or shared database are used."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
import zlib
from urllib.error import HTTPError
from urllib.request import Request, urlopen

SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "client-white-model-previs" / "scripts" / "team_queue.py"
spec = importlib.util.spec_from_file_location("client_previs_team_queue", SCRIPT)
queue_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(queue_module)
Q = queue_module


class Clock:
    def __init__(self):
        self.value = 1000.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class TeamQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.auth_file = self.root / "auth.json"
        self.auth = {
            "coordinator": {"token": "a" * 40, "role": "admin"},
            "worker-one": {"token": "b" * 40, "role": "worker"},
            "worker-two": {"token": "c" * 40, "role": "worker"},
            "reviewer-one": {"token": "d" * 40, "role": "reviewer"},
        }
        self.write_auth()
        self.clock = Clock()
        self.queue = Q.Queue(self.root / "queue.sqlite3", lease_seconds=30, now=self.clock)
        self.server = Q.create_server(self.queue, self.auth_file, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:" + str(self.server.server_address[1])
        self.counter = 0

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temp.cleanup()

    def write_auth(self):
        self.auth_file.write_text(json.dumps(self.auth), encoding="utf-8")

    def task(self, source="1" * 64, revision="immutable-revision-1", standards="3" * 64):
        result = {
            "source_sha256": source, "template_sha256": "2" * 64,
            "standards_sha256": standards, "skill_revision": revision,
            "spec": {"width": 3840, "height": 2160, "samples": 64, "render_percent": 100},
            "timeline": {"start_frame": 0, "frame_count": 240, "fps": "24/1"},
            "source_uri": "file:///private-assets/source.mp4",
        }
        result["job_id"] = Q.task_id(result)
        return result

    def call(self, operation, body, actor="worker-one", retry=None):
        if operation != "status":
            self.counter += 1
            body = {"retry_key": retry or f"request-{self.counter}", **body}
        return Q.api_request(self.url, operation, body, self.auth[actor]["token"])

    def submit(self, task=None):
        task = task or self.task()
        return self.call("submit", {"task": task}, "coordinator")["job_id"]

    def claim(self, actor="worker-one", revision="immutable-revision-1", standards="3" * 64, **kwargs):
        return self.call("claim", {"skill_revision": revision, "standards_sha256": standards}, actor, **kwargs)["lease"]

    def completion(self, lease):
        return {
            "schema": Q.COMPLETION_SCHEMA, "job_id": lease["job_id"],
            "standards_sha256": lease["standards_sha256"], "skill_revision": lease["skill_revision"],
            "gate_passed": True, "gate_sha256": "4" * 64,
            "report": {"sha256": "4" * 64, "uri": "file:///private-assets/gate.json"},
            "artifacts": [{"kind": "blend", "sha256": "5" * 64, "uri": "file:///private-assets/1.blend"}],
        }

    def complete(self, lease, actor="worker-one", completion=None, **kwargs):
        return self.call("complete", {"job_id": lease["job_id"], "lease_id": lease["lease_id"],
                                     "completion": completion or self.completion(lease)}, actor, **kwargs)

    def review(self, lease, actor="reviewer-one", decision="accepted", **extra):
        body = {"job_id": lease["job_id"], "lease_id": lease["lease_id"], "decision": decision,
                "evidence_reviewed": True, "gate_sha256": "4" * 64,
                "review_report": {"sha256": "6" * 64, "uri": "file:///private-assets/review.json"}}
        body.update(extra)
        return self.call("review", body, actor)

    def assert_error(self, code, function, *args, **kwargs):
        with self.assertRaises(Q.QueueError) as caught:
            function(*args, **kwargs)
        self.assertEqual(code, caught.exception.code)

    @staticmethod
    def png(width=4, height=2):
        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
        rows = b"".join(b"\0" + b"\0\0\0" * width for _ in range(height))
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) +
                chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))

    def render_fixture(self):
        task = self.task(source="a" * 64)
        task["spec"] = {"render": {"width": 4, "height": 2, "samples": 64, "percentage": 100}}
        task["timeline"] = {"frame_start": 1, "frame_end": 2}
        task["job_id"] = Q.task_id(task)
        self.submit(task)
        lease = self.claim()
        render_dir = self.root / "renders"
        render_dir.mkdir()
        completed = []
        for frame in (1, 2):
            image = render_dir / f"frame_{frame:04d}.png"
            image.write_bytes(self.png())
            completed.append({"frame": frame, "path": image.name, "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
                              "width": 4, "height": 2})
        manifest = {"schema": "client-white-model-render.v1", "status": "COMPLETE", "job_id": lease["job_id"],
                    "standards_sha256": lease["standards_sha256"], "skill_revision": lease["skill_revision"],
                    "width": 4, "height": 2, "resolution_percentage": 100, "upscaled": False,
                    "frame_start": 1, "frame_end": 2, "completed": completed}
        manifest_path = render_dir / "render_manifest.json"
        gate_path = self.root / "render_gate.json"
        self.write_render_gate(manifest_path, manifest, gate_path, lease)
        return lease, manifest_path, manifest, gate_path

    def write_render_gate(self, manifest_path, manifest, gate_path, lease):
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        gate = {"schema": Q.GATE_SCHEMA, "passed": True, "job_id": lease["job_id"],
                "standards_sha256": lease["standards_sha256"], "skill_revision": lease["skill_revision"],
                "artifacts": [{"kind": "renders", "path": str(manifest_path),
                               "sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest()}]}
        gate_path.write_text(json.dumps(gate), encoding="utf-8")

    def test_deterministic_identity_and_independent_spec_pins(self):
        task = self.task()
        self.assertEqual(task["job_id"], Q.task_id(dict(reversed(list(task.items())))))
        task["source_uri"] = "file:///another-machine/source.mp4"
        self.assertEqual(task["job_id"], Q.task_id(task))
        task["spec"]["width"] = 1920
        self.assertNotEqual(task["job_id"], Q.task_id(task))
        self.assert_error("job_id_mismatch", self.submit, task)

    def test_submission_deduplicates_and_never_overwrites_payload(self):
        task = self.task()
        first = self.call("submit", {"task": task}, "coordinator", retry="first")
        other = {**task, "source_uri": "file:///different-device/source.mp4"}
        second = self.call("submit", {"task": other}, "coordinator", retry="second")
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["job_id"], second["job_id"])
        record = self.call("status", {"job_id": first["job_id"]})["job"]
        self.assertEqual(task["source_uri"], record["task"]["source_uri"])

    def test_two_http_clients_claim_single_job_atomically(self):
        self.submit()
        barrier = threading.Barrier(3)
        results, errors = [], []

        def client(actor):
            try:
                barrier.wait(timeout=3)
                result = Q.api_request(self.url, "claim", {"retry_key": "parallel-claim",
                    "standards_sha256": "3" * 64, "skill_revision": "immutable-revision-1"}, self.auth[actor]["token"])
                results.append(result["lease"])
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=client, args=(actor,)) for actor in ("worker-one", "worker-two")]
        for thread in threads:
            thread.start()
        barrier.wait(timeout=3)
        for thread in threads:
            thread.join(timeout=5)
        self.assertEqual([], errors)
        self.assertEqual(2, len(results))
        self.assertEqual(1, sum(item is not None for item in results))
        self.assertEqual(1, self.call("status", {})["counts"]["leased"])

    def test_claim_retry_is_idempotent_and_changed_payload_fails(self):
        self.submit()
        first = self.claim(retry="claim-retry")
        second = self.claim(retry="claim-retry")
        self.assertEqual(first["lease_id"], second["lease_id"])
        self.assertEqual(1, second["attempt"])
        self.assert_error("retry_payload_changed", self.claim, revision="revision-2", retry="claim-retry")

    def test_version_and_ruleset_pin_never_claim_wrong_job(self):
        self.submit()
        self.assertIsNone(self.claim(revision="different-commit"))
        self.assertIsNone(self.claim(standards="9" * 64))
        self.assertIsNotNone(self.claim())
        self.assert_error("invalid_digest", self.call, "claim", {"skill_revision": "x", "standards_sha256": "missing"})

    def test_expiry_reassigns_and_old_owner_cannot_complete_or_heartbeat(self):
        self.submit()
        original = self.claim()
        self.clock.advance(31)
        replacement = self.claim("worker-two")
        self.assertNotEqual(original["lease_id"], replacement["lease_id"])
        self.assertEqual(2, replacement["attempt"])
        self.assert_error("stale_lease", self.complete, original)
        self.assert_error("stale_lease", self.call, "heartbeat", {"job_id": original["job_id"], "lease_id": original["lease_id"]})
        self.assertEqual("worker-two", self.call("status", {"job_id": original["job_id"]})["job"]["owner"])

    def test_expired_claim_retry_does_not_acquire_new_lease(self):
        self.submit()
        self.claim(retry="old-claim")
        self.clock.advance(31)
        self.assert_error("stale_retry", self.claim, retry="old-claim")
        self.assertEqual(2, self.claim(retry="fresh-claim")["attempt"])

    def test_expired_lease_without_reassignment_is_still_rejected(self):
        self.submit()
        lease = self.claim()
        self.clock.advance(30)
        self.assert_error("stale_lease", self.complete, lease)
        self.assertEqual("queued", self.call("status", {"job_id": lease["job_id"]})["job"]["state"])

    def test_heartbeat_retry_does_not_extend_twice(self):
        self.submit()
        lease = self.claim()
        body = {"job_id": lease["job_id"], "lease_id": lease["lease_id"]}
        self.clock.advance(10)
        first = self.call("heartbeat", body, retry="heartbeat-retry")
        self.clock.advance(5)
        second = self.call("heartbeat", body, retry="heartbeat-retry")
        self.assertEqual(first["expires"], second["expires"])
        third = self.call("heartbeat", body, retry="next-heartbeat")
        self.assertGreater(third["expires"], second["expires"])

    def test_wrong_worker_cannot_use_lease(self):
        self.submit()
        lease = self.claim()
        self.assert_error("stale_lease", self.complete, lease, actor="worker-two")

    def test_completion_is_pending_review_and_idempotent(self):
        self.submit()
        lease = self.claim()
        first = self.complete(lease, retry="complete-retry")
        self.assertEqual("awaiting_review", first["state"])
        second = self.complete(lease, retry="complete-retry")
        self.assertEqual(first, second)
        record = self.call("status", {"job_id": lease["job_id"]})["job"]
        self.assertEqual("worker-one", record["completion"]["producer"])
        self.assertIsNone(record["expires"])
        self.assertIsNone(self.claim("worker-two"))

    def test_missing_or_failing_gate_and_evidence_are_rejected(self):
        self.submit()
        lease = self.claim()
        for field, value, code in (("gate_passed", False, "completion_gate"),
                                   ("artifacts", [], "missing_artifacts"),
                                   ("report", {}, "invalid_field"),
                                   ("report", {"sha256": "8" * 64, "uri": "file:///wrong-gate.json"}, "gate_report_digest"),
                                   ("gate_sha256", "bad", "invalid_digest")):
            report = self.completion(lease)
            report[field] = value
            self.assert_error(code, self.complete, lease, completion=report)

    def test_completion_wrong_pinned_job_is_rejected(self):
        self.submit()
        lease = self.claim()
        for field in ("job_id", "standards_sha256", "skill_revision"):
            report = self.completion(lease)
            report[field] = "wrong-value"
            self.assert_error("completion_pin_mismatch", self.complete, lease, completion=report)

    def test_authenticated_independent_review_accepts_and_records_evidence(self):
        self.submit()
        lease = self.claim()
        self.complete(lease)
        result = self.review(lease)
        self.assertEqual("accepted", result["state"])
        record = self.call("status", {"job_id": lease["job_id"]})["job"]
        self.assertEqual("reviewer-one", record["review"]["reviewer"])
        self.assertEqual("6" * 64, record["review"]["review_report"]["sha256"])
        self.assertIsNone(self.claim())

    def test_worker_role_denies_approval_and_role_rotation_still_denies_self_review(self):
        self.submit()
        lease = self.claim()
        self.complete(lease)
        self.assert_error("role_denied", self.review, lease, actor="worker-one")
        # Even if an operator later rotates this identity to reviewer, it remains
        # the recorded producer. The second layer forbids self-approval.
        self.auth["worker-one"]["role"] = "reviewer"
        self.write_auth()
        self.assert_error("self_approval", self.review, lease, actor="worker-one")

    def test_review_requires_exact_gate_and_current_lease(self):
        self.submit()
        lease = self.claim()
        self.complete(lease)
        self.assert_error("review_gate_mismatch", self.review, lease, gate_sha256="7" * 64)
        self.assert_error("review_evidence", self.review, lease, evidence_reviewed=False)
        self.assert_error("missing_evidence", self.review, lease, review_report=None)
        wrong = {**lease, "lease_id": "old-lease"}
        self.assert_error("review_state", self.review, wrong)

    def test_rework_requires_reason_then_new_lease_rejects_old_completion(self):
        self.submit()
        old = self.claim()
        self.complete(old, retry="old-completion")
        self.assert_error("invalid_field", self.review, old, decision="rework")
        self.review(old, decision="rework", reason="Support and occlusion need correction at frames 85–121")
        new = self.claim("worker-two")
        self.assertEqual(2, new["attempt"])
        self.assert_error("stale_completion_retry", self.complete, old, retry="old-completion")
        self.assertEqual("worker-two", self.call("status", {"job_id": new["job_id"]})["job"]["owner"])

    def test_authentication_and_state_change_roles_fail_closed(self):
        self.assert_error("invalid_credential", Q.api_request, self.url, "status", {}, "unknown-token")
        request = Request(self.url + "/status", data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
        with self.assertRaises(HTTPError) as caught:
            urlopen(request)
        self.assertEqual(401, caught.exception.code)
        caught.exception.close()
        for actor, operation, body in (("worker-one", "submit", {"task": self.task()}),
                                       ("reviewer-one", "claim", {}), ("coordinator", "complete", {})):
            self.assert_error("role_denied", self.call, operation, body, actor)
        self.auth["reviewer-one"]["token"] = self.auth["worker-one"]["token"]
        self.write_auth()
        self.assert_error("authentication_configuration", self.call, "status", {})

    def test_malformed_auth_file_fails_closed(self):
        self.auth_file.write_text("not-json", encoding="utf-8")
        self.assert_error("authentication_configuration", self.call, "status", {})

    def test_state_survives_coordinator_reopen(self):
        job_id = self.submit()
        lease = self.claim()
        reloaded = Q.Queue(self.root / "queue.sqlite3", lease_seconds=30, now=self.clock)
        record = reloaded.status("coordinator", {"job_id": job_id})["job"]
        self.assertEqual(lease["lease_id"], record["lease_id"])
        self.assertEqual("leased", record["state"])

    def test_local_gate_adapter_pins_report_and_artifact_uris(self):
        self.submit()
        lease = self.claim()
        artifact = self.root / "1.blend"
        artifact.write_bytes(b"local synthetic artifact fixture")
        artifact_sha = hashlib.sha256(artifact.read_bytes()).hexdigest()
        gate = {"schema": Q.GATE_SCHEMA, "passed": True, "job_id": lease["job_id"],
                "standards_sha256": lease["standards_sha256"], "skill_revision": lease["skill_revision"],
                "artifacts": [{"kind": "blend", "path": str(artifact), "sha256": artifact_sha}]}
        path = self.root / "gate.json"
        path.write_text(json.dumps(gate), encoding="utf-8")
        completion = Q.completion_from_gate(path, lease, "https://private-store/gate.json", {"blend": "https://private-store/1.blend"})
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), completion["gate_sha256"])
        self.assertEqual("https://private-store/1.blend", completion["artifacts"][0]["uri"])
        self.assertEqual("awaiting_review", self.complete(lease, completion=completion)["state"])
        artifact.write_bytes(b"changed after gate")
        with self.assertRaises(ValueError):
            Q.completion_from_gate(path, lease)
        gate["passed"] = False
        path.write_text(json.dumps(gate), encoding="utf-8")
        with self.assertRaises(ValueError):
            Q.completion_from_gate(path, lease)

    def test_init_auth_is_outside_repo_unique_and_never_overwrites(self):
        path = self.root / "new-auth.json"
        result = Q.init_auth(path, ["worker-a", "worker-b"], ["reviewer-c"], ["admin-d"])
        self.assertEqual(4, len(result["identities"]))
        self.assertNotIn("token", json.dumps(result))
        values = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(4, len({v["token"] for v in values.values()}))
        with self.assertRaises(FileExistsError):
            Q.init_auth(path)
        with self.assertRaises(ValueError):
            Q.init_auth(SCRIPT.parent / "auth.json")

    def test_render_manifest_rechecks_all_pngs_and_rejects_missing_or_modified(self):
        lease, manifest_path, manifest, gate_path = self.render_fixture()
        complete = Q.completion_from_gate(gate_path, lease)
        self.assertEqual("renders", complete["artifacts"][0]["kind"])
        image = manifest_path.parent / "frame_0002.png"
        original = image.read_bytes()
        image.write_bytes(original + b"changed after gate")
        with self.assertRaisesRegex(ValueError, "Native PNG changed"):
            Q.completion_from_gate(gate_path, lease)
        image.write_bytes(original)
        image.unlink()
        with self.assertRaisesRegex(ValueError, "missing"):
            Q.completion_from_gate(gate_path, lease)
        image.write_bytes(original)
        self.assertEqual("renders", Q.completion_from_gate(gate_path, lease)["artifacts"][0]["kind"])

    def test_render_manifest_rejects_count_sequence_path_escape_and_wrong_pin(self):
        lease, manifest_path, original, gate_path = self.render_fixture()
        mutations = [
            ("missing entry", lambda m: m["completed"].pop()),
            ("duplicate frame", lambda m: m["completed"][1].update(frame=1)),
            ("wrong order", lambda m: m["completed"].reverse()),
            ("parent escape", lambda m: m["completed"][0].update(path="../frame_0001.png")),
            ("absolute path", lambda m: m["completed"][0].update(path=str(self.root / "frame_0001.png"))),
            ("windows drive", lambda m: m["completed"][0].update(path="C:\\outside\\frame_0001.png")),
            ("wrong job pin", lambda m: m.update(job_id="9" * 64)),
            ("wrong sequence bounds", lambda m: m.update(frame_end=3)),
            ("wrong dimensions", lambda m: m["completed"][0].update(width=8)),
        ]
        for description, mutate in mutations:
            with self.subTest(description=description):
                manifest = json.loads(json.dumps(original))
                mutate(manifest)
                self.write_render_gate(manifest_path, manifest, gate_path, lease)
                with self.assertRaises(ValueError):
                    Q.completion_from_gate(gate_path, lease)

    def test_render_manifest_rejects_extra_png_inventory(self):
        lease, manifest_path, manifest, gate_path = self.render_fixture()
        extra = manifest_path.parent / "frame_0003.png"
        extra.write_bytes(self.png())
        with self.assertRaisesRegex(ValueError, "inventory"):
            Q.completion_from_gate(gate_path, lease)

    def test_complete_cli_checks_render_png_changes_before_server_submission(self):
        lease, manifest_path, manifest, gate_path = self.render_fixture()
        lease_path = self.root / "lease.json"
        lease_path.write_text(json.dumps({"lease": lease}), encoding="utf-8")
        env = {**os.environ, "CLIENT_PREVIS_TOKEN": self.auth["worker-one"]["token"]}
        command = [sys.executable, str(SCRIPT), "complete", "--server", self.url, "--lease", str(lease_path),
                   "--gate", str(gate_path), "--retry-key", "actual-cli-complete"]
        image = manifest_path.parent / "frame_0002.png"
        original = image.read_bytes()
        image.unlink()
        missing = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(1, missing.returncode, missing.stdout + missing.stderr)
        self.assertIn("missing", missing.stderr)
        image.write_bytes(original + b"modified")
        changed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(1, changed.returncode, changed.stdout + changed.stderr)
        self.assertIn("Native PNG changed", changed.stderr)
        self.assertEqual("leased", self.call("status", {"job_id": lease["job_id"]})["job"]["state"])
        image.write_bytes(original)
        passed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(0, passed.returncode, passed.stdout + passed.stderr)
        self.assertEqual("awaiting_review", json.loads(passed.stdout)["state"])
        self.assertEqual("awaiting_review", self.call("status", {"job_id": lease["job_id"]})["job"]["state"])


if __name__ == "__main__":
    unittest.main()
