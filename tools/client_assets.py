#!/usr/bin/env python3
"""List, retrieve and verify versioned client materials without running their contents.

The default manifest is ../client-materials/manifest.json in a checkout, or
../assets/client/manifest.json when this byte-identical helper is installed in
skill/scripts. Git assets then use manifest skill_path under that SkillRoot.
An explicit --manifest and --source-root also work. Release downloads use HTTPS,
GitHub asset hosts, streaming hashes and a same-directory atomic commit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import tempfile
from typing import Any
import urllib.error
import urllib.parse
import urllib.request


MANIFEST_SCHEMA = "client-assets.v1"
RESULT_SCHEMA = "client-assets-result.v1"
ALLOWED_HOSTS = frozenset({
    "github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com",
})
CHUNK_BYTES = 1024 * 1024
SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
RESERVED_NAMES = frozenset({"CON", "PRN", "AUX", "NUL"} |
                           {f"COM{i}" for i in range(1, 10)} |
                           {f"LPT{i}" for i in range(1, 10)})


class ClientAssetError(ValueError):
    """A manifest, provenance, transport or destination failed verification."""


def default_manifest(script_path: Path | None = None) -> Path:
    root = Path(script_path or __file__).resolve().parents[1]
    repository = root / "client-materials" / "manifest.json"
    installed = root / "assets" / "client" / "manifest.json"
    if repository.is_file():
        return repository
    if (root / "SKILL.md").is_file() or installed.is_file():
        return installed
    return repository


DEFAULT_MANIFEST = default_manifest()


def file_digest(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_BYTES):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def asset_path(value: Any) -> PurePosixPath:
    """Validate a portable repository-relative path, including Windows ADS cases."""
    if not isinstance(value, str) or not value or "\\" in value:
        raise ClientAssetError("asset path must be a nonempty POSIX relative path")
    components = value.split("/")
    if any(not part or part in {".", ".."} for part in components):
        raise ClientAssetError("asset path has an empty, dot or parent component")
    for part in components:
        if (any(ord(char) < 32 for char in part) or
                any(char in '<>:"|?*' for char in part) or part.endswith((".", " ")) or
                part.split(".", 1)[0].upper() in RESERVED_NAMES):
            raise ClientAssetError("asset path is not portable across team devices")
    result = PurePosixPath(value)
    if result.is_absolute():
        raise ClientAssetError("asset path must stay relative to its root")
    return result


def bounded_path(root: Path, relative: str) -> Path:
    root = root.resolve()
    path = root.joinpath(*asset_path(relative).parts).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ClientAssetError("asset path or symlink leaves the chosen root") from exc
    return path


def github_url(url: Any) -> str:
    if not isinstance(url, str) or not url:
        raise ClientAssetError("release asset requires an HTTPS URL")
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise ClientAssetError("malformed release URL") from exc
    if (parts.scheme.lower() != "https" or parts.hostname not in ALLOWED_HOSTS or
            parts.username is not None or parts.password is not None or
            port not in (None, 443) or parts.fragment or
            any(ord(char) < 32 or char.isspace() for char in url)):
        raise ClientAssetError("release URL must use HTTPS and an allowed GitHub asset host")
    return url


class GitHubRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        newurl = github_url(urllib.parse.urljoin(req.full_url, newurl))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def load_manifest(path: Path) -> dict[str, Any]:
    path = Path(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ClientAssetError("manifest is missing or is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict) or value.get("schema") != MANIFEST_SCHEMA:
        raise ClientAssetError(f"manifest schema must be {MANIFEST_SCHEMA}")
    if not isinstance(value.get("assets"), list):
        raise ClientAssetError("manifest assets must be a list")
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    for item in value["assets"]:
        if not isinstance(item, dict) or not ID_PATTERN.fullmatch(str(item.get("id", ""))):
            raise ClientAssetError("asset id must be a stable portable identifier")
        if not isinstance(item.get("id"), str):
            raise ClientAssetError("asset id must be a string")
        asset_path(item.get("path"))
        if "skill_path" in item:
            asset_path(item["skill_path"])
        if item["id"] in seen_ids or item["path"].casefold() in seen_paths:
            raise ClientAssetError("duplicate asset id or cross-platform path collision")
        seen_ids.add(item["id"])
        seen_paths.add(item["path"].casefold())
        if not SHA_PATTERN.fullmatch(str(item.get("sha256", ""))):
            raise ClientAssetError("asset requires a 64-character SHA-256")
        size = item.get("size_bytes")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ClientAssetError("asset size_bytes must be a nonnegative integer")
        for field in ("category", "status", "original_filename", "role"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                raise ClientAssetError(f"asset requires nonempty {field}")
        storage = item.get("storage")
        if not isinstance(storage, dict) or storage.get("kind") not in {"git", "github-release"}:
            raise ClientAssetError("asset storage kind must be git or github-release")
        if storage["kind"] == "github-release" and storage.get("url"):
            github_url(storage["url"])
        if item["status"] == "available" and storage["kind"] == "github-release":
            github_url(storage.get("url"))
    return value


def selected_assets(manifest: dict[str, Any], ids: list[str] | None, all_assets: bool) -> list[dict[str, Any]]:
    if all_assets and ids:
        raise ClientAssetError("choose either --all or --id, not both")
    if all_assets:
        return list(manifest["assets"])
    if not ids:
        raise ClientAssetError("fetch and verify require --id or --all")
    by_id = {item["id"]: item for item in manifest["assets"]}
    unknown = [asset_id for asset_id in ids if asset_id not in by_id]
    if unknown:
        raise ClientAssetError("unknown asset ids: " + ", ".join(unknown))
    # A repeated --id selects one asset once, preserving human-specified order.
    return [by_id[asset_id] for asset_id in dict.fromkeys(ids)]


def verify_file(path: Path, item: dict[str, Any]) -> None:
    if not path.is_file():
        raise ClientAssetError(f"asset {item['id']} is missing or is not a regular file")
    actual_hash, actual_size = file_digest(path)
    if actual_size != item["size_bytes"] or actual_hash != item["sha256"].lower():
        raise ClientAssetError(f"asset {item['id']} differs from its pinned size/SHA; existing file preserved")


def write_stream(stream, destination: Path, item: dict[str, Any], *, content_length: str | None = None) -> None:
    if content_length is not None:
        try:
            size_header = int(content_length)
        except (TypeError, ValueError) as exc:
            raise ClientAssetError("invalid Content-Length on release response") from exc
        if size_header != item["size_bytes"]:
            raise ClientAssetError("release Content-Length differs from pinned asset size")
    digest = hashlib.sha256()
    size = 0
    with destination.open("wb") as handle:
        while chunk := stream.read(CHUNK_BYTES):
            size += len(chunk)
            if size > item["size_bytes"]:
                raise ClientAssetError("asset stream exceeds pinned size")
            digest.update(chunk)
            handle.write(chunk)
        handle.flush()
        os.fsync(handle.fileno())
    if size != item["size_bytes"] or digest.hexdigest() != item["sha256"].lower():
        raise ClientAssetError("asset stream differs from pinned size/SHA")


def atomic_publish(temporary: Path, destination: Path, item: dict[str, Any]) -> bool:
    """Publish verified bytes atomically without replacing even a racing writer.

    Windows rename refuses an existing destination. POSIX rename replaces it, so
    use a same-filesystem no-clobber link followed by unlink instead. Unsupported
    filesystems fail without replacing the destination; they never fall back to
    a destructive copy. Return False when identical bytes already appeared.
    """
    try:
        if os.name == "nt":
            os.rename(temporary, destination)
        else:
            os.link(temporary, destination)
            temporary.unlink()
    except FileExistsError:
        verify_file(destination, item)
        return False
    return True


def fetch_one(item: dict[str, Any], output: Path, source_root: Path, *,
              source_relative: str | None = None, timeout: float = 60.0) -> dict[str, Any]:
    if item["status"] != "available":
        raise ClientAssetError(f"asset {item['id']} has status {item['status']}; download not published")
    destination = bounded_path(output, item["path"])
    if destination.exists():
        verify_file(destination, item)
        return result_item(item, destination, "already-verified")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Recheck after mkdir in case an existing parent is a symlink.
    destination = bounded_path(output, item["path"])
    lock_name = ".client-assets-" + hashlib.sha256(item["path"].encode("utf-8")).hexdigest()[:20] + ".lock"
    lock = destination.parent / lock_name
    try:
        lock_fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ClientAssetError(f"asset {item['id']} has another active fetch; retry after it finishes") from exc
    temporary: Path | None = None
    try:
        os.close(lock_fd)
        if destination.exists():
            verify_file(destination, item)
            return result_item(item, destination, "already-verified")
        fd, name = tempfile.mkstemp(prefix=".client-asset-", suffix=".part", dir=destination.parent)
        os.close(fd)
        temporary = Path(name)
        if item["storage"]["kind"] == "git":
            source = bounded_path(source_root, source_relative or item["path"])
            if not source.is_file():
                raise ClientAssetError(f"Git asset {item['id']} is absent from the supplied source root")
            with source.open("rb") as handle:
                write_stream(handle, temporary, item)
            status = "copied"
        else:
            url = github_url(item["storage"].get("url"))
            request = urllib.request.Request(url, headers={
                "User-Agent": "client-white-model-previs-assets/1", "Accept-Encoding": "identity",
            })
            opener = urllib.request.build_opener(GitHubRedirectHandler())
            with opener.open(request, timeout=timeout) as response:
                github_url(response.geturl())
                encoding = response.headers.get("Content-Encoding", "identity")
                if encoding.lower() != "identity":
                    raise ClientAssetError("release response content encoding must be identity")
                write_stream(response, temporary, item, content_length=response.headers.get("Content-Length"))
            status = "downloaded"
        if destination.exists():
            verify_file(destination, item)
            return result_item(item, destination, "already-verified")
        # The lock avoids duplicate work; no-clobber publication also protects
        # existing bytes created by a writer that does not use our lock.
        published = atomic_publish(temporary, destination, item)
        if published:
            temporary = None
        else:
            status = "already-verified"
        verify_file(destination, item)
        return result_item(item, destination, status)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        lock.unlink(missing_ok=True)


def result_item(item: dict[str, Any], path: Path, status: str) -> dict[str, Any]:
    return {"id": item["id"], "path": str(path), "sha256": item["sha256"].lower(),
            "size_bytes": item["size_bytes"], "status": status}


def source_location(manifest_path: Path, explicit_root: Path | None) -> tuple[Path, bool]:
    if explicit_root is not None:
        root = Path(explicit_root).resolve()
        return root, (root / "SKILL.md").is_file()
    for ancestor in manifest_path.parents:
        if (ancestor / "SKILL.md").is_file():
            return ancestor, True
    return manifest_path.parent.parent, False


def source_relative(item: dict[str, Any], installed: bool) -> str:
    if installed and item["storage"]["kind"] == "git":
        if "skill_path" not in item:
            raise ClientAssetError(f"installed Git asset {item['id']} requires a skill_path mapping")
        return item["skill_path"]
    return item["path"]


def execute(operation: str, manifest_path: Path, *, ids: list[str] | None = None,
            all_assets: bool = False, output: Path | None = None,
            source_root: Path | None = None, timeout: float = 60.0) -> dict[str, Any]:
    manifest_path = Path(manifest_path).resolve()
    if timeout <= 0:
        raise ClientAssetError("timeout must be greater than zero")
    manifest = load_manifest(manifest_path)
    source_root, installed = source_location(manifest_path, source_root)
    manifest_sha, _ = file_digest(manifest_path)
    if operation == "list":
        items = list(manifest["assets"])
    else:
        chosen = selected_assets(manifest, ids, all_assets)
        if operation == "fetch":
            if output is None:
                raise ClientAssetError("fetch requires an explicit output root")
            root = Path(output).resolve()
            root.mkdir(parents=True, exist_ok=True)
            items = [fetch_one(item, root, source_root,
                               source_relative=source_relative(item, installed), timeout=timeout) for item in chosen]
        elif operation == "verify":
            root = Path(output).resolve() if output is not None else source_root
            items = []
            for item in chosen:
                relative = source_relative(item, installed) if output is None else item["path"]
                path = bounded_path(root, relative)
                verify_file(path, item)
                items.append(result_item(item, path, "verified"))
        else:
            raise ClientAssetError("unknown operation")
    return {"schema": RESULT_SCHEMA, "operation": operation, "passed": True,
            "manifest_sha256": manifest_sha, "assets": items}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)
    for operation in ("list", "fetch", "verify"):
        subparser = subparsers.add_parser(operation)
        subparser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
        if operation != "list":
            selection = subparser.add_mutually_exclusive_group(required=True)
            selection.add_argument("--id", action="append", dest="ids")
            selection.add_argument("--all", action="store_true", dest="all_assets")
            subparser.add_argument("--output", type=Path, required=operation == "fetch")
            subparser.add_argument("--source-root", type=Path)
            subparser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args(argv)
    try:
        result = execute(args.operation, args.manifest,
                         ids=getattr(args, "ids", None), all_assets=getattr(args, "all_assets", False),
                         output=getattr(args, "output", None), source_root=getattr(args, "source_root", None),
                         timeout=getattr(args, "timeout", 60.0))
    except (ClientAssetError, OSError, urllib.error.URLError) as exc:
        print(json.dumps({"schema": RESULT_SCHEMA, "operation": args.operation,
                          "passed": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
