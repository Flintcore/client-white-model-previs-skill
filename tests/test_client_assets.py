"""Local fixtures and mocked HTTPS streams; these tests publish no client data."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import urllib.request


TOOL = Path(__file__).resolve().parents[1] / "tools" / "client_assets.py"
SPEC = importlib.util.spec_from_file_location("client_assets_test_module", TOOL)
assets = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(assets)


class MockResponse(io.BytesIO):
    def __init__(self, content, *, url="https://release-assets.githubusercontent.com/test/model.blend",
                 length=None, encoding=None):
        super().__init__(content)
        self.url = url
        self.headers = {}
        if length is not None:
            self.headers["Content-Length"] = str(length)
        if encoding is not None:
            self.headers["Content-Encoding"] = encoding

    def geturl(self):
        return self.url


class ClientAssetsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="client-assets-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "repository"
        self.repo.mkdir()
        self.manifest = self.repo / "client-materials" / "manifest.json"
        self.manifest.parent.mkdir()
        self.output = self.root / "team-output"
        self.body = b"isolated-fixture-not-a-production-model\x00\x01"
        self.item = {"id": "model-fixture", "category": "models", "path": "client-materials/models/fixture.blend",
                     "sha256": hashlib.sha256(self.body).hexdigest(), "size_bytes": len(self.body),
                     "status": "available", "storage": {"kind": "git"},
                     "original_filename": "fixture.blend", "role": "isolated-test"}
        self.write_manifest()
        source = self.repo / self.item["path"]
        source.parent.mkdir(parents=True)
        source.write_bytes(self.body)

    def write_manifest(self, items=None):
        value = {"schema": "client-assets.v1", "assets": items if items is not None else [self.item]}
        self.manifest.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def release_item(self):
        item = dict(self.item)
        item["storage"] = {"kind": "github-release", "url": "https://github.com/example/repo/releases/download/v1/fixture.blend"}
        return item

    def assert_no_partial(self):
        if self.output.exists():
            self.assertFalse(list(self.output.rglob("*.part")))
            self.assertFalse(list(self.output.rglob("*.lock")))

    def test_list_never_downloads_or_creates_output(self):
        self.write_manifest([self.release_item()])
        with mock.patch.object(assets.urllib.request, "build_opener") as opener:
            result = assets.execute("list", self.manifest)
        opener.assert_not_called()
        self.assertEqual(result["assets"][0]["id"], "model-fixture")
        self.assertFalse(self.output.exists())

    def test_git_copy_then_verify_reuses_exact_bytes(self):
        first = assets.execute("fetch", self.manifest, ids=[self.item["id"]], output=self.output)
        self.assertEqual(first["assets"][0]["status"], "copied")
        self.assertEqual((self.output / self.item["path"]).read_bytes(), self.body)
        second = assets.execute("fetch", self.manifest, all_assets=True, output=self.output)
        self.assertEqual(second["assets"][0]["status"], "already-verified")
        checked = assets.execute("verify", self.manifest, all_assets=True, output=self.output)
        self.assertTrue(checked["passed"])
        self.assert_no_partial()

    def test_verify_git_defaults_to_manifest_repository(self):
        result = assets.execute("verify", self.manifest, ids=[self.item["id"]])
        self.assertEqual(result["assets"][0]["status"], "verified")

    def test_explicit_source_root_for_installed_mapping(self):
        copy = self.root / "installed-index" / "client-materials" / "manifest.json"
        copy.parent.mkdir(parents=True)
        copy.write_bytes(self.manifest.read_bytes())
        result = assets.execute("fetch", copy, ids=[self.item["id"]], output=self.output, source_root=self.repo)
        self.assertEqual(result["assets"][0]["status"], "copied")

    def test_installed_skill_default_manifest_and_git_mapping(self):
        skill = self.root / "installed-skill"
        manifest = skill / "assets" / "client" / "manifest.json"
        manifest.parent.mkdir(parents=True)
        (skill / "SKILL.md").write_text("isolated-fixture", encoding="utf-8")
        item = dict(self.item, skill_path="assets/client/models/fixture.blend")
        manifest.write_text(json.dumps({"schema": "client-assets.v1", "assets": [item]}), encoding="utf-8")
        source = skill / item["skill_path"]
        source.parent.mkdir()
        source.write_bytes(self.body)
        self.assertEqual(assets.default_manifest(skill / "scripts" / "client_assets.py"), manifest)
        result = assets.execute("fetch", manifest, all_assets=True, output=self.output)
        self.assertEqual(result["assets"][0]["status"], "copied")
        self.assertTrue(assets.execute("verify", manifest, all_assets=True)["passed"])
        self.assertTrue(assets.execute("verify", manifest, all_assets=True, output=self.output)["passed"])

    def test_installed_missing_or_escaping_mapping_fails(self):
        skill = self.root / "installed-skill"
        manifest = skill / "assets" / "client" / "manifest.json"
        manifest.parent.mkdir(parents=True)
        (skill / "SKILL.md").write_text("isolated-fixture", encoding="utf-8")
        manifest.write_bytes(self.manifest.read_bytes())
        with self.assertRaisesRegex(assets.ClientAssetError, "skill_path"):
            assets.execute("fetch", manifest, all_assets=True, output=self.output)
        item = dict(self.item, skill_path="../escape.blend")
        manifest.write_text(json.dumps({"schema": "client-assets.v1", "assets": [item]}), encoding="utf-8")
        with self.assertRaises(assets.ClientAssetError):
            assets.execute("fetch", manifest, all_assets=True, output=self.output)

    def test_real_installed_helper_subprocess_finds_bundled_manifest(self):
        skill = self.root / "installed-skill"
        script = skill / "scripts" / "client_assets.py"
        script.parent.mkdir(parents=True)
        script.write_bytes(TOOL.read_bytes())
        (skill / "SKILL.md").write_text("isolated-fixture", encoding="utf-8")
        manifest = skill / "assets" / "client" / "manifest.json"
        manifest.parent.mkdir(parents=True)
        item = dict(self.item, skill_path="assets/client/models/fixture.blend")
        manifest.write_text(json.dumps({"schema": "client-assets.v1", "assets": [item]}), encoding="utf-8")
        source = skill / item["skill_path"]
        source.parent.mkdir()
        source.write_bytes(self.body)
        result = subprocess.run([sys.executable, "-X", "utf8", str(script), "fetch", "--id", item["id"],
                                 "--output", str(self.output)], cwd=self.root,
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["assets"][0]["status"], "copied")
        self.assertEqual((self.output / item["path"]).read_bytes(), self.body)

    def test_default_repository_manifest(self):
        self.assertEqual(assets.default_manifest(self.repo / "tools" / "client_assets.py"), self.manifest)

    def test_release_download_verifies_stream_and_length(self):
        item = self.release_item()
        self.write_manifest([item])
        opener = mock.Mock()
        opener.open.return_value = MockResponse(self.body, length=len(self.body))
        with mock.patch.object(assets.urllib.request, "build_opener", return_value=opener):
            result = assets.execute("fetch", self.manifest, all_assets=True, output=self.output)
        self.assertEqual(result["assets"][0]["status"], "downloaded")
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_header("Accept-encoding"), "identity")
        self.assertEqual((self.output / item["path"]).read_bytes(), self.body)
        self.assert_no_partial()

    def test_release_without_length_uses_actual_stream(self):
        self.write_manifest([self.release_item()])
        opener = mock.Mock()
        opener.open.return_value = MockResponse(self.body)
        with mock.patch.object(assets.urllib.request, "build_opener", return_value=opener):
            assets.execute("fetch", self.manifest, all_assets=True, output=self.output)
        self.assert_no_partial()

    def test_existing_wrong_content_is_preserved_without_network(self):
        self.write_manifest([self.release_item()])
        existing = self.output / self.item["path"]
        existing.parent.mkdir(parents=True)
        existing.write_bytes(b"existing-work")
        with mock.patch.object(assets.urllib.request, "build_opener") as opener:
            with self.assertRaisesRegex(assets.ClientAssetError, "preserved"):
                assets.execute("fetch", self.manifest, all_assets=True, output=self.output)
        opener.assert_not_called()
        self.assertEqual(existing.read_bytes(), b"existing-work")

    def test_wrong_stream_sha_never_publishes_partial(self):
        self.write_manifest([self.release_item()])
        opener = mock.Mock()
        opener.open.return_value = MockResponse(b"X" * len(self.body))
        with mock.patch.object(assets.urllib.request, "build_opener", return_value=opener):
            with self.assertRaisesRegex(assets.ClientAssetError, "size/SHA"):
                assets.execute("fetch", self.manifest, all_assets=True, output=self.output)
        self.assertFalse((self.output / self.item["path"]).exists())
        self.assert_no_partial()

    def test_wrong_size_header_short_and_oversized_streams_fail(self):
        self.write_manifest([self.release_item()])
        cases = [(self.body, len(self.body) + 1, "Content-Length"),
                 (self.body[:-1], None, "size/SHA"),
                 (self.body + b"X", None, "exceeds")]
        for body, length, message in cases:
            with self.subTest(message=message):
                opener = mock.Mock()
                opener.open.return_value = MockResponse(body, length=length)
                with mock.patch.object(assets.urllib.request, "build_opener", return_value=opener):
                    with self.assertRaisesRegex(assets.ClientAssetError, message):
                        assets.execute("fetch", self.manifest, all_assets=True, output=self.output)
                self.assert_no_partial()

    def test_invalid_length_and_content_encoding_fail(self):
        self.write_manifest([self.release_item()])
        for kwargs, message in [({"length": "invalid"}, "Content-Length"), ({"encoding": "gzip"}, "encoding")]:
            opener = mock.Mock()
            opener.open.return_value = MockResponse(self.body, **kwargs)
            with mock.patch.object(assets.urllib.request, "build_opener", return_value=opener):
                with self.assertRaisesRegex(assets.ClientAssetError, message):
                    assets.execute("fetch", self.manifest, all_assets=True, output=self.output)
            self.assert_no_partial()

    def test_final_response_host_is_checked(self):
        self.write_manifest([self.release_item()])
        opener = mock.Mock()
        opener.open.return_value = MockResponse(self.body, url="https://unrelated.invalid/payload")
        with mock.patch.object(assets.urllib.request, "build_opener", return_value=opener):
            with self.assertRaisesRegex(assets.ClientAssetError, "allowed GitHub"):
                assets.execute("fetch", self.manifest, all_assets=True, output=self.output)
        self.assert_no_partial()

    def test_redirect_rejects_downgrade_or_unrelated_domain(self):
        handler = assets.GitHubRedirectHandler()
        request = urllib.request.Request("https://github.com/example/repo/releases/download/v1/file")
        for url in ("http://github.com/file", "https://evil.github.com/file", "https://example.com/file"):
            with self.subTest(url=url), self.assertRaises(assets.ClientAssetError):
                handler.redirect_request(request, None, 302, "Found", {}, url)
        valid = handler.redirect_request(request, None, 302, "Found", {},
                                        "https://objects.githubusercontent.com/asset?id=123")
        self.assertEqual(valid.full_url, "https://objects.githubusercontent.com/asset?id=123")
        relative = handler.redirect_request(request, None, 302, "Found", {}, "/example/repo/file")
        self.assertEqual(relative.full_url, "https://github.com/example/repo/file")

    def test_rejects_url_credentials_port_fragment_and_deceptive_host(self):
        for url in ("http://github.com/x", "https://github.com.evil.example/x", "https://a@github.com/x",
                    "https://github.com:444/x", "https://github.com/x#fragment", "https://github.com/x\n"):
            with self.subTest(url=url), self.assertRaises(assets.ClientAssetError):
                assets.github_url(url)

    def test_rejects_nonportable_and_parent_paths(self):
        for path in ("../outside", "/absolute", "C:/drive", "a/../b", "a//b", "a/./b",
                     "a\\b", "a/file:ads", "a/NUL.mp4", "a/end. ", "a/end."):
            with self.subTest(path=path), self.assertRaises(assets.ClientAssetError):
                assets.asset_path(path)

    def test_symlink_escape_is_rejected(self):
        self.output.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        try:
            (self.output / "client-materials").symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("host does not permit isolated symlink creation")
        with self.assertRaisesRegex(assets.ClientAssetError, "leaves"):
            assets.execute("fetch", self.manifest, all_assets=True, output=self.output)
        self.assertEqual(list(outside.iterdir()), [])

    def test_manifest_collision_and_bad_metadata_rejected(self):
        duplicate = dict(self.item, id="other", path=self.item["path"].upper())
        self.write_manifest([self.item, duplicate])
        with self.assertRaisesRegex(assets.ClientAssetError, "collision"):
            assets.load_manifest(self.manifest)
        for change in ({"size_bytes": True}, {"size_bytes": -1}, {"sha256": "bad"},
                       {"role": ""}, {"storage": {"kind": "other"}}, {"id": "bad/id"}):
            with self.subTest(change=change):
                self.write_manifest([dict(self.item, **change)])
                with self.assertRaises(assets.ClientAssetError):
                    assets.load_manifest(self.manifest)

    def test_pending_release_lists_but_is_not_fetched(self):
        pending = self.release_item()
        pending["status"] = "pending-upload"
        pending["storage"].pop("url")
        self.write_manifest([pending])
        self.assertTrue(assets.execute("list", self.manifest)["passed"])
        with self.assertRaisesRegex(assets.ClientAssetError, "not published"):
            assets.execute("fetch", self.manifest, all_assets=True, output=self.output)

    def test_selection_unknown_and_duplicate_ids(self):
        manifest = assets.load_manifest(self.manifest)
        with self.assertRaisesRegex(assets.ClientAssetError, "unknown"):
            assets.selected_assets(manifest, ["missing"], False)
        self.assertEqual(len(assets.selected_assets(manifest, [self.item["id"], self.item["id"]], False)), 1)
        with self.assertRaises(assets.ClientAssetError):
            assets.selected_assets(manifest, [self.item["id"]], True)

    def test_tamper_verify_and_missing_file_fail(self):
        target = self.repo / self.item["path"]
        target.write_bytes(b"changed")
        with self.assertRaisesRegex(assets.ClientAssetError, "differs"):
            assets.execute("verify", self.manifest, all_assets=True)
        target.unlink()
        with self.assertRaisesRegex(assets.ClientAssetError, "missing"):
            assets.execute("verify", self.manifest, all_assets=True)

    def test_git_source_changed_rejected_without_partial(self):
        (self.repo / self.item["path"]).write_bytes(b"changed")
        with self.assertRaises(assets.ClientAssetError):
            assets.execute("fetch", self.manifest, all_assets=True, output=self.output)
        self.assertFalse((self.output / self.item["path"]).exists())
        self.assert_no_partial()

    def test_exclusive_lock_never_removed_by_other_fetch(self):
        parent = (self.output / self.item["path"]).parent
        parent.mkdir(parents=True)
        lock = parent / (".client-assets-" + hashlib.sha256(self.item["path"].encode()).hexdigest()[:20] + ".lock")
        lock.write_bytes(b"other-active-download")
        with self.assertRaisesRegex(assets.ClientAssetError, "active fetch"):
            assets.execute("fetch", self.manifest, all_assets=True, output=self.output)
        self.assertEqual(lock.read_bytes(), b"other-active-download")

    def test_atomic_publication_preserves_noncooperating_writer(self):
        destination = self.root / "race.blend"
        temporary = self.root / "verified.part"
        temporary.write_bytes(self.body)
        destination.write_bytes(b"different-concurrent-work")
        with self.assertRaisesRegex(assets.ClientAssetError, "preserved"):
            assets.atomic_publish(temporary, destination, self.item)
        self.assertEqual(destination.read_bytes(), b"different-concurrent-work")
        self.assertEqual(temporary.read_bytes(), self.body)
        destination.write_bytes(self.body)
        self.assertFalse(assets.atomic_publish(temporary, destination, self.item))

    def test_download_connection_failure_cleans_temp_and_lock(self):
        self.write_manifest([self.release_item()])
        opener = mock.Mock()
        opener.open.side_effect = assets.urllib.error.URLError("isolated simulated disconnect")
        with mock.patch.object(assets.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(assets.urllib.error.URLError):
                assets.execute("fetch", self.manifest, all_assets=True, output=self.output)
        self.assertFalse((self.output / self.item["path"]).exists())
        self.assert_no_partial()

    def test_large_stream_is_read_in_bounded_chunks(self):
        body = b"L" * (assets.CHUNK_BYTES * 2 + 3)
        item = self.release_item()
        item.update(sha256=hashlib.sha256(body).hexdigest(), size_bytes=len(body))
        self.write_manifest([item])
        response = MockResponse(body, length=len(body))
        original_read = response.read
        sizes = []
        def read(size):
            sizes.append(size)
            return original_read(size)
        response.read = read
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch.object(assets.urllib.request, "build_opener", return_value=opener):
            assets.execute("fetch", self.manifest, all_assets=True, output=self.output)
        self.assertGreaterEqual(len(sizes), 3)
        self.assertEqual(set(sizes), {assets.CHUNK_BYTES})

    def test_nonpositive_timeout_fails(self):
        with self.assertRaisesRegex(assets.ClientAssetError, "timeout"):
            assets.execute("fetch", self.manifest, all_assets=True, output=self.output, timeout=0)

    def test_cli_real_subprocess_list_fetch_verify_and_error_json(self):
        def invoke(*argv):
            result = subprocess.run([sys.executable, "-X", "utf8", str(TOOL), *argv,
                                     "--manifest", str(self.manifest)], capture_output=True, text=True, encoding="utf-8")
            return result.returncode, json.loads(result.stdout)
        code, result = invoke("list")
        self.assertEqual(code, 0)
        self.assertEqual(result["schema"], assets.RESULT_SCHEMA)
        code, result = invoke("fetch", "--id", self.item["id"], "--output", str(self.output))
        self.assertEqual(code, 0)
        code, result = invoke("verify", "--all", "--output", str(self.output))
        self.assertEqual(code, 0)
        code, result = invoke("fetch", "--id", "unknown", "--output", str(self.output))
        self.assertEqual(code, 1)
        self.assertFalse(result["passed"])


if __name__ == "__main__":
    unittest.main()
