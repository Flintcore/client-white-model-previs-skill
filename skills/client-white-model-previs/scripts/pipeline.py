"""Deterministic stage cache and measured attempt ledger, not a motion solver.

External programs produce artifacts; this module never launches a shell, invents
animation, or grants client acceptance. Cache hits require pinned algorithms,
current input bytes, current dependency receipts, and every output digest.
Preview, native-render, media, and final-review reports have additional checks.
All persisted paths are job-relative. CLI output is intentionally short.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import socket
import sys
import time
import uuid

import common

SCHEMA = "client-white-model-pipeline.v1"
RECEIPT_SCHEMA = "client-white-model-stage-cache.v1"
ATTEMPT_SCHEMA = "client-white-model-stage-attempt.v1"
STAGES = ("observations", "camera", "pose", "contact", "scene", "light",
          "preview", "render", "media", "review")
DEPENDENCIES = {
    "observations": (), "camera": ("observations",),
    "pose": ("observations", "camera"), "contact": ("pose", "camera"),
    "scene": ("contact", "camera"), "light": ("scene",),
    "preview": ("scene", "light"), "render": ("preview",),
    "media": ("render",), "review": ("media",),
}
BOUNDARY = "CACHE_AND_TIMING_ONLY_NOT_A_SOLVER_OR_CLIENT_ACCEPTANCE"
MAX_RANGE_PREVIEW = 12
_SHA = re.compile(r"[0-9a-f]{64}")
_RESERVED = re.compile(r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", re.I)


def _portable(value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError("Expected portable job-relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(p in ("", ".", "..") for p in value.split("/")):
        raise ValueError("Expected bounded job-relative path")
    if any(_RESERVED.fullmatch(p) or p.endswith((".", " ")) for p in path.parts):
        raise ValueError("Path is not portable across team devices")
    return path.as_posix()


def _path(root, value, exists=True):
    value = _portable(value)
    root = Path(root).resolve()
    target = root.joinpath(*PurePosixPath(value).parts)
    target.resolve().relative_to(root)
    if exists and not target.is_file():
        raise FileNotFoundError(value)
    return target


def _relative(root, value):
    target = Path(value)
    if not target.is_absolute():
        target = _path(root, str(value))
    target = target.resolve()
    return _portable(target.relative_to(Path(root).resolve()).as_posix())


def _sha(value):
    return isinstance(value, str) and bool(_SHA.fullmatch(value))


def _clock_host():
    # Keep literal device/account names out of portable receipts. Completed
    # receipts may travel; an unfinished monotonic timer stays on its host.
    return hashlib.sha256(socket.gethostname().casefold().encode("utf-8")).hexdigest()


def _atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".writing-" + uuid.uuid4().hex)
    try:
        with open(temp, "x", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


@contextmanager
def _lock(folder):
    folder.mkdir(parents=True, exist_ok=True)
    lock = folder / "runtime.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise ValueError("Runtime busy or stale lock; inspect the owning attempt first") from error
    try:
        os.write(descriptor, str(os.getpid()).encode("ascii"))
        os.close(descriptor)
        yield
    finally:
        lock.unlink(missing_ok=True)


def _binding(job):
    return {key: job[key] for key in ("job_id", "skill_revision", "standards_version",
                                     "standards_sha256", "source", "template",
                                     "render", "timeline", "profile", "palette_decision")}


def _read(job_path, config_path=None):
    job, root = common.load_job(job_path)
    config_path = Path(config_path or root / "pipeline.json").resolve()
    config_path.relative_to(root)
    config = common.read_json(config_path)
    if config.get("schema") != SCHEMA or config.get("binding") != _binding(job):
        raise ValueError("Pipeline belongs to another job/revision/rule set")
    if set(config.get("stages", {})) != set(STAGES):
        raise ValueError("Complete fixed stage graph required")
    folder = _path(root, config.get("cache_dir", ".pipeline"), exists=False)
    return job, root, config, folder


def init_pipeline(job_path, config_path=None, cache_dir=".pipeline"):
    """Create an unconfigured worksheet; no guessed algorithms or pass flags."""
    job, root = common.load_job(job_path)
    config_path = Path(config_path or root / "pipeline.json").resolve()
    config_path.relative_to(root)
    if config_path.exists():
        raise ValueError("Existing pipeline preserved; use plan and edit explicit stage inputs")
    _path(root, cache_dir, exists=False)
    config = {"schema": SCHEMA, "binding": _binding(job), "cache_dir": _portable(cache_dir),
              "stages": {name: {"algorithm": None, "parameters": {}, "inputs": []}
                         for name in STAGES}, "scope": BOUNDARY}
    _atomic(config_path, config)
    return {"config": _relative(root, config_path), "state": "CONFIGURATION_REQUIRED",
            "next_action": "pin each actual algorithm and add its independent input files",
            "client_acceptance": False, "scope": BOUNDARY}


def _algorithm(spec, root, memo=None):
    algorithm = spec.get("algorithm")
    if (not isinstance(algorithm, dict) or not isinstance(algorithm.get("name"), str)
            or not algorithm["name"].strip()):
        raise ValueError("Actual algorithm pin required")
    # An external Git revision is allowed only alongside code/weight digest(s).
    if not _sha(algorithm.get("sha256")):
        raise ValueError("Algorithm must include an actual sha256, not latest/main")
    if not algorithm.get("path"):
        raise ValueError("Actual job-relative algorithm file path required")
    actual = _fingerprint(root, [algorithm["path"]], memo)[0]
    if actual["sha256"] != algorithm["sha256"]:
        raise ValueError("Actual algorithm bytes differ from their declared SHA pin")
    revision = algorithm.get("revision")
    if revision is not None and not re.fullmatch("[0-9a-f]{40}", str(revision)):
        raise ValueError("Algorithm revision must be a full Git SHA")
    json.dumps(algorithm, sort_keys=True, allow_nan=False)
    return algorithm


def _fingerprint(root, paths, memo=None):
    memo = memo if memo is not None else {}
    normalized = [_portable(value) for value in paths]
    if len(normalized) != len(set(normalized)):
        raise ValueError("Duplicate artifact/input paths")
    rows = []
    for value in sorted(normalized):
        path = _path(root, value)
        if value not in memo:
            size = path.stat().st_size
            if size <= 0:
                raise ValueError("Empty/partial artifact: " + value)
            memo[value] = {"path": value, "size_bytes": size, "sha256": common.sha256(path)}
        rows.append(memo[value])
    return rows


def _ranges(values, frame_count):
    if not isinstance(values, list):
        raise ValueError("failed_ranges must be a list")
    rows = []
    for value in values:
        if not isinstance(value, dict):
            raise ValueError("Failure range needs start/end/reason")
        start, end = value.get("start"), value.get("end")
        reason = value.get("reason")
        if (type(start) is not int or type(end) is not int or
                not 1 <= start <= end <= frame_count or not isinstance(reason, str) or not reason.strip()):
            raise ValueError("Failure range outside assigned frames or missing reason")
        rows.append({"start": start, "end": end, "reason": reason.strip()[:240]})
    rows.sort(key=lambda row: (row["start"], row["end"], row["reason"]))
    return rows


def _report_paths(root, report):
    return [_relative(root, row["path"]) for row in report.get("artifacts", [])]


def _validate_evidence(stage, job_path, root, outputs, report_path, cache=False):
    """Validate special gates, without replacing independent visual acceptance."""
    if stage not in ("preview", "render", "media", "review"):
        if report_path:
            report = common.read_json(_path(root, report_path))
            if report.get("passed") is not True:
                raise ValueError("Stage report failed/unverified")
        return {}
    if not report_path:
        raise ValueError("Actual current " + stage + " report required")
    report_file = _path(root, report_path)
    report = common.read_json(report_file)
    job, unused = common.load_job(job_path, check_inputs=False)
    if report.get("job_id") != job["job_id"]:
        raise ValueError("Report belongs to another job")
    if stage == "preview":
        from match_check import validate_report
        # The matching module rechecks artifact hashes and recomputes metrics.
        current = validate_report(job_path, report_file)
        if not isinstance(current, dict) or current.get("passed") is not True:
            raise ValueError("Recomputed preview evidence is not passed")
        return {}
    if stage == "render":
        if report.get("schema") != "client-white-model-render.v1" or report.get("status") != "COMPLETE":
            raise ValueError("Full native-render report required")
        blends = [row for row in outputs if row["path"].endswith(".blend") and
                  row["sha256"] == report.get("blend_sha256")]
        if len(blends) != 1:
            raise ValueError("Record the actual rendered blend alongside its manifest")
        from media import verify_native
        verify_native(job, _path(root, blends[0]["path"]), report_file.parent)
        # Native image files must be in the receipt, not only a manifest flag.
        expected = {_relative(root, report_file.parent / row["path"])
                    for row in report.get("completed", [])}
        if not expected <= {row["path"] for row in outputs}:
            raise ValueError("Missing native output frames in cache receipt")
        return {}
    if stage == "media":
        if (report.get("schema") != "client-white-model-media.v1" or report.get("passed") is not True
                or report.get("standards_sha256") != job["standards_sha256"]):
            raise ValueError("Actual current media verification required")
        rows = report.get("artifacts", [])
        if {row.get("kind") for row in rows} != {"original", "white", "comparison", "blend"}:
            raise ValueError("Media report requires exactly four actual delivery artifacts")
        actual = {row["path"]: row["sha256"] for row in outputs}
        for row in rows:
            value = _relative(root, row["path"])
            if actual.get(value) != row.get("sha256"):
                raise ValueError("Media output digest differs from actual report")
        return {}
    # Runtime records the pre-existing gate, rather than granting a new review.
    if report.get("schema") != "client-white-model-gate.v1" or report.get("passed") is not True:
        raise ValueError("Existing independently reviewed final gate required")
    by_kind = {}
    for row in report.get("artifacts", []):
        by_kind.setdefault(row["kind"], []).append(_path(root, _relative(root, row["path"])))
    if any(len(by_kind.get(name, [])) != 1 for name in ("blender", "media", "visual")):
        raise ValueError("Missing independent final gate evidence")
    from gate import verify_bundle
    current = verify_bundle(job_path, by_kind["blender"][0], by_kind["media"][0], by_kind["visual"][0])
    if current.get("passed") is not True:
        raise ValueError("Final gate no longer matches actual independent evidence")
    if report.get("client_acceptance") is not False:
        raise ValueError("Internal gate is not client acceptance")
    return {}


def _expanded_outputs(stage, root, values, report_path):
    paths = {_relative(root, value) for value in values}
    if report_path:
        paths.add(_relative(root, report_path))
        report = common.read_json(_path(root, _relative(root, report_path)))
        if stage == "render":
            parent = _path(root, _relative(root, report_path)).parent
            paths.update(_relative(root, parent / row["path"]) for row in report.get("completed", []))
        if stage in ("media", "review"):
            paths.update(_report_paths(root, report))
    return sorted(paths)


def _receipt_digest(receipt):
    # Exclude timings from dependency keys: rerunning identical output bytes
    # must not force unrelated downstream work just because it took longer.
    return common.digest({"key": receipt["key"], "outputs": receipt["outputs"],
                          "report_path": receipt.get("report_path")})


def _check_receipt(receipt, stage, key, job_path, root, memo):
    if (receipt.get("schema") != RECEIPT_SCHEMA or receipt.get("stage") != stage or
            receipt.get("key") != key or receipt.get("status") != "passed" or
            receipt.get("client_acceptance") is not False or receipt.get("failed_ranges") != []):
        raise ValueError("Wrong/incomplete stage receipt")
    outputs = receipt.get("outputs", [])
    if (not isinstance(outputs, list) or any(not isinstance(row, dict) or
            type(row.get("size_bytes")) is not int or not _sha(row.get("sha256")) for row in outputs)):
        raise ValueError("Output receipt needs actual path/size/SHA rows")
    if not outputs or _fingerprint(root, [row["path"] for row in outputs], memo) != outputs:
        raise ValueError("Missing/changed/partial output bytes")
    report_path = receipt.get("report_path")
    if report_path and report_path not in {row["path"] for row in outputs}:
        raise ValueError("Evidence report is not hash-bound")
    _validate_evidence(stage, job_path, root, outputs, report_path, cache=True)


def plan(job_path, config_path=None):
    job, root, config, folder = _read(job_path, config_path)
    memo, rows, receipts = {}, [], {}
    runtime_sha = common.sha256(__file__)
    for stage in STAGES:
        spec = config["stages"][stage]
        row = {"stage": stage, "key": None, "status": "blocked", "reason": "dependency_not_verified"}
        if not isinstance(spec, dict):
            row.update(status="configuration_required", reason="stage_spec_required")
            rows.append(row)
            continue
        try:
            algorithm = _algorithm(spec, root, memo)
            params = spec.get("parameters", {})
            if not isinstance(params, dict):
                raise ValueError("Stage parameters must be an object")
            # JSON serialization with finite floats is part of key stability.
            json.dumps(params, sort_keys=True, allow_nan=False)
            inputs = _fingerprint(root, spec.get("inputs", []), memo)
        except (ValueError, KeyError, TypeError, FileNotFoundError, OSError) as error:
            row.update(status="configuration_required", reason=str(error)[:300])
            rows.append(row)
            continue
        deps = DEPENDENCIES[stage]
        if any(name not in receipts for name in deps):
            rows.append(row)
            continue
        payload = {"binding": config["binding"], "runtime_sha256": runtime_sha,
                   "stage": stage, "algorithm": algorithm, "parameters": params,
                   "inputs": inputs,
                   "dependencies": {name: _receipt_digest(receipts[name]) for name in deps}}
        key = common.digest(payload)
        row.update(key=key, status="run", reason="no_verified_receipt")
        path = _path(root, (folder / "cache" / stage / (key + ".json")).relative_to(root).as_posix(), exists=False)
        if path.exists():
            try:
                receipt = common.read_json(path)
                if receipt.get("status") == "failed":
                    if (receipt.get("schema") != RECEIPT_SCHEMA or receipt.get("key") != key or
                            receipt.get("stage") != stage):
                        raise ValueError("Invalid failure receipt")
                    ranges = _ranges(receipt.get("failed_ranges", []), job["source"]["frame_count"])
                    row.update(status="repair", reason=receipt.get("message") or "previous_attempt_failed",
                               failed_ranges=ranges[:MAX_RANGE_PREVIEW], failed_range_count=len(ranges))
                else:
                    _check_receipt(receipt, stage, key, job_path, root, memo)
                    row.update(status="cache_hit", reason="current_pins_and_all_actual_output_digests_verified")
                    receipts[stage] = receipt
            except (ValueError, KeyError, TypeError, FileNotFoundError, OSError, RuntimeError,
                    ImportError) as error:
                row.update(status="repair", reason="invalid_cached_artifacts: " + str(error)[:260])
        rows.append(row)
    next_row = next((row for row in rows if row["status"] != "cache_hit"), None)
    if next_row is None:
        action = {"stage": None, "action": "internal_gate_recorded", "reason": "client_signoff_is_separate"}
    else:
        action = {"stage": next_row["stage"], "action": next_row["status"], "reason": next_row["reason"]}
        for name in ("failed_ranges", "failed_range_count"):
            if name in next_row:
                action[name] = next_row[name]
    return {"schema": "client-white-model-pipeline-plan.v1", "job_id": job["job_id"],
            "stages": rows, "next_action": action, "client_acceptance": False,
            "scope": BOUNDARY}


def start_stage(job_path, stage, config_path=None):
    job, root, config, folder = _read(job_path, config_path)
    if stage not in STAGES:
        raise ValueError("Unknown stage")
    with _lock(folder):
        row = next(item for item in plan(job_path, config_path)["stages"] if item["stage"] == stage)
        if row["status"] not in ("run", "repair") or row["key"] is None:
            raise ValueError("Stage is cached/unconfigured or dependencies are not verified")
        for path in (folder / "attempts").glob("*.json"):
            path = _path(root, path.relative_to(root).as_posix())
            current = common.read_json(path)
            if (current.get("status") == "running" and current.get("stage") == stage
                    and current.get("key") == row["key"]):
                raise ValueError("Same stage/key already has a measured running attempt")
        token = uuid.uuid4().hex
        attempt = {"schema": ATTEMPT_SCHEMA, "token": token, "stage": stage, "key": row["key"],
                   "job_id": job["job_id"], "status": "running", "started_epoch": time.time(),
                   "started_monotonic_ns": time.monotonic_ns(),
                   "clock_host_sha256": _clock_host(),
                   "started_utc": datetime.now(timezone.utc).isoformat(), "scope": BOUNDARY}
        path = _path(root, (folder / "attempts" / (token + ".json")).relative_to(root).as_posix(), exists=False)
        _atomic(path, attempt)
    return {"token": token, "stage": stage, "key": row["key"], "status": "running",
            "timer": "actual_elapsed_since_start_not_agent_estimate", "scope": BOUNDARY}


def record_stage(job_path, token, status, outputs=(), report_path=None, failed_ranges=None,
                 message="", config_path=None):
    """Complete one externally executed attempt; failures never become hits."""
    job, root, config, folder = _read(job_path, config_path)
    if not re.fullmatch("[0-9a-f]{32}", str(token)) or status not in ("passed", "failed"):
        raise ValueError("Valid attempt token and passed/failed outcome required")
    with _lock(folder):
        path = _path(root, (folder / "attempts" / (token + ".json")).relative_to(root).as_posix())
        attempt = common.read_json(path)
        if (attempt.get("schema") != ATTEMPT_SCHEMA or attempt.get("status") != "running" or
                attempt.get("job_id") != job["job_id"]):
            raise ValueError("Attempt is not the current unfinished job attempt")
        if attempt.get("clock_host_sha256") != _clock_host():
            raise ValueError("Running timer belongs to another host; preserve its unfinished ledger")
        now_epoch, now_monotonic = time.time(), time.monotonic_ns()
        elapsed = (now_monotonic - attempt["started_monotonic_ns"]) / 1e9
        wall = now_epoch - attempt["started_epoch"]
        if elapsed < 0 or wall < 0 or abs(wall - elapsed) > max(5, elapsed * .01):
            raise ValueError("Clock discontinuity/reboot; preserve attempt and start a measured replacement")
        stage = attempt["stage"]
        row = next(item for item in plan(job_path, config_path)["stages"] if item["stage"] == stage)
        if row["key"] != attempt["key"] or row["status"] not in ("run", "repair"):
            raise ValueError("Input/dependency/config changed during attempt or stage already completed")
        ranges = _ranges(failed_ranges or [], job["source"]["frame_count"])
        errors = []
        actual = []
        normalized_report = None
        if status == "passed":
            try:
                if ranges:
                    raise ValueError("Passing receipt may not contain failed ranges")
                normalized_report = _relative(root, report_path) if report_path else None
                actual = _fingerprint(root, _expanded_outputs(stage, root, outputs, normalized_report))
                if not actual:
                    raise ValueError("At least one nonempty actual output is required")
                _validate_evidence(stage, job_path, root, actual, normalized_report)
            except (ValueError, KeyError, TypeError, FileNotFoundError, OSError, RuntimeError, ImportError) as error:
                status = "failed"
                errors.append(str(error)[:600])
        receipt = {"schema": RECEIPT_SCHEMA, "stage": stage, "key": attempt["key"], "status": status,
                   "outputs": actual if status == "passed" else [], "report_path": normalized_report,
                   "failed_ranges": ranges, "message": (message.strip()[:600] or "; ".join(errors)),
                   "attempt_token": token, "measured_elapsed_seconds": elapsed,
                   "client_acceptance": False, "scope": BOUNDARY}
        cache_path = _path(root, (folder / "cache" / stage / (attempt["key"] + ".json")).relative_to(root).as_posix(), exists=False)
        _atomic(cache_path, receipt)
        attempt.update(status=status, ended_epoch=now_epoch, ended_utc=datetime.now(timezone.utc).isoformat(),
                       measured_elapsed_seconds=elapsed, timer_basis="same_host_monotonic_ns",
                       errors=errors, failed_ranges=ranges)
        _atomic(path, attempt)
    return {"stage": stage, "status": status, "measured_elapsed_seconds": elapsed,
            "errors": errors, "failed_range_count": len(ranges),
            "next_action": plan(job_path, config_path)["next_action"],
            "client_acceptance": False, "scope": BOUNDARY}


def timing_summary(job_path, config_path=None):
    job, root, config, folder = _read(job_path, config_path)
    totals = {stage: {"attempts": 0, "passed": 0, "failed": 0, "running": 0,
                      "measured_elapsed_seconds": 0.0} for stage in STAGES}
    for path in sorted((folder / "attempts").glob("*.json")):
        path = _path(root, path.relative_to(root).as_posix())
        row = common.read_json(path)
        if (row.get("schema") != ATTEMPT_SCHEMA or row.get("job_id") != job["job_id"] or
                row.get("stage") not in totals or row.get("status") not in ("passed", "failed", "running")):
            raise ValueError("Invalid measured attempt ledger")
        values = totals[row["stage"]]
        values["attempts"] += 1
        values[row["status"]] += 1
        if row["status"] != "running":
            seconds = row.get("measured_elapsed_seconds")
            if not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds < 0:
                raise ValueError("Actual measured attempt duration required")
            values["measured_elapsed_seconds"] += seconds
    return {"schema": "client-white-model-pipeline-timing.v1", "job_id": job["job_id"],
            "stages": totals, "measured_total_attempt_seconds": sum(v["measured_elapsed_seconds"] for v in totals.values()),
            "scope": "ACTUAL_ATTEMPT_ELAPSED_NOT_GPU_OR_AGENT_TOKEN_TIME",
            "client_acceptance": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "plan", "start", "record", "timing-summary"):
        command = sub.add_parser(name)
        command.add_argument("--job", required=True)
        command.add_argument("--config")
        if name == "init":
            command.add_argument("--cache-dir", default=".pipeline")
        if name == "start":
            command.add_argument("--stage", choices=STAGES, required=True)
        if name == "record":
            command.add_argument("--token", required=True)
            command.add_argument("--status", choices=("passed", "failed"), required=True)
            command.add_argument("--output", action="append", default=[])
            command.add_argument("--report")
            command.add_argument("--failed-ranges", help="job-relative JSON list of start/end/reason")
            command.add_argument("--message", default="")
    args = parser.parse_args(argv)
    if args.command == "init":
        result = init_pipeline(args.job, args.config, args.cache_dir)
    elif args.command == "plan":
        result = plan(args.job, args.config)
    elif args.command == "start":
        result = start_stage(args.job, args.stage, args.config)
    elif args.command == "record":
        ranges = []
        if args.failed_ranges:
            unused, root = common.load_job(args.job)
            ranges = common.read_json(_path(root, args.failed_ranges))
        result = record_stage(args.job, args.token, args.status, args.output, args.report,
                              ranges, args.message, args.config)
    else:
        result = timing_summary(args.job, args.config)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 1 if result.get("status") == "failed" else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, KeyError, TypeError, FileNotFoundError, OSError, RuntimeError, ImportError) as error:
        print(json.dumps({"passed": False, "error": str(error), "client_acceptance": False,
                          "scope": BOUNDARY}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
