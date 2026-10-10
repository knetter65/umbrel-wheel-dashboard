from __future__ import annotations

import gzip
import importlib.util
import io
import json
import tarfile
import tempfile
import unittest
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "verify-private-ghcr.yml"
CI = ROOT / ".github" / "workflows" / "ci.yml"
VERIFIER = ROOT / "scripts" / "verify_private_ghcr.py"

spec = importlib.util.spec_from_file_location("verify_private_ghcr", VERIFIER)
assert spec and spec.loader
verify = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = verify
spec.loader.exec_module(verify)


class PrivateVerificationWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.workflow = WORKFLOW.read_text(encoding="utf-8")
        self.ci = CI.read_text(encoding="utf-8")

    def test_trigger_guards_and_exact_upstream_sha(self):
        for value in (
            "workflow_run:",
            "workflows: [ci]",
            "github.repository == 'knetter65/umbrel-wheel-dashboard'",
            "github.event.workflow_run.name == 'ci'",
            "github.event.workflow_run.event == 'push'",
            "github.event.workflow_run.head_branch == 'main'",
            "github.event.workflow_run.conclusion == 'success'",
            "ref: ${{ github.event.workflow_run.head_sha }}",
            "persist-credentials: false",
        ):
            self.assertIn(value, self.workflow)

    def test_job_permissions_are_minimal_and_explicit(self):
        self.assertIn("permissions: {}", self.workflow)
        permission_block = "permissions:\n      actions: read\n      packages: read\n      contents: write"
        self.assertEqual(self.workflow.count(permission_block), 1)
        self.assertNotIn("packages: write", self.workflow)
        self.assertNotIn("actions: write", self.workflow)

    def test_runner_context_is_used_only_in_step_env(self):
        runner_lines = [
            line
            for line in self.workflow.splitlines()
            if "EVIDENCE_DIR:" in line and "runner.temp" in line
        ]
        self.assertEqual(
            runner_lines,
            ["          EVIDENCE_DIR: ${{ runner.temp }}/private-ghcr-evidence"] * 2,
        )

    def test_evidence_branch_is_dedicated_and_non_force(self):
        self.assertIn("EVIDENCE_BRANCH: verification/private-0.3.3", self.workflow)
        self.assertIn('HEAD:refs/heads/$EVIDENCE_BRANCH', self.workflow)
        self.assertNotIn("--force", self.workflow)
        self.assertIn('"evidence.json evidence.md "', self.workflow)
        self.assertNotIn("git add .", self.workflow)

    def test_no_loop_and_ci_publishes_only_from_main(self):
        self.assertNotIn("push:\n", self.workflow)
        self.assertIn("push:\n    branches: [main]", self.ci)
        self.assertIn("github.ref == 'refs/heads/main'", self.ci)
        self.assertIn("github.event_name == 'push'", self.ci)
        self.assertIn("detect-image-changes:", self.ci)
        self.assertIn("needs.detect-image-changes.outputs.changed == 'true'", self.ci)
        self.assertIn("Dockerfile pyproject.toml uv.lock src schema assets fixtures", self.ci)

    def test_all_third_party_actions_are_commit_pinned(self):
        for path in (WORKFLOW, CI):
            for line in path.read_text(encoding="utf-8").splitlines():
                if "uses:" not in line:
                    continue
                reference = line.split("uses:", 1)[1].split("#", 1)[0].strip()
                self.assertRegex(reference, r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}$")


class PrivateVerificationScannerTests(unittest.TestCase):
    def make_layer(self, entries: list[tuple[str, bytes]]) -> bytes:
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode="w") as archive:
            for name, payload in entries:
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
        return gzip.compress(raw.getvalue(), mtime=0)

    def test_sanitized_scanner_returns_category_not_secret(self):
        secret = b"access_" + b"token" + b"=" + b"abcdefghijklmnop"
        findings = verify.scan_bytes(secret)
        self.assertEqual(findings, {"credential_assignment"})
        self.assertNotIn(secret.decode(), json.dumps(sorted(findings)))

    def test_cross_origin_redirect_strips_authorization(self):
        request = urllib.request.Request("https://api.github.com/source", headers={"Authorization": "Bearer example"})
        redirected = verify.SafeRedirectHandler().redirect_request(
            request, None, 302, "Found", {}, "https://objects.example/download"
        )
        self.assertIsNotNone(redirected)
        self.assertFalse(redirected.has_header("Authorization"))

    def test_same_origin_redirect_keeps_authorization(self):
        request = urllib.request.Request("https://ghcr.io/source", headers={"Authorization": "Bearer example"})
        redirected = verify.SafeRedirectHandler().redirect_request(
            request, None, 307, "Temporary Redirect", {}, "https://ghcr.io/destination"
        )
        self.assertIsNotNone(redirected)
        self.assertTrue(redirected.has_header("Authorization"))

    def test_descriptor_size_and_digest_are_both_required(self):
        payload = b"descriptor payload"
        descriptor = {"digest": verify.sha256_bytes(payload), "size": len(payload)}
        verify.verify_descriptor_payload(payload, descriptor, "TEST")
        with self.assertRaises(verify.VerificationError) as caught:
            verify.verify_descriptor_payload(payload, {**descriptor, "size": len(payload) + 1}, "TEST")
        self.assertEqual(caught.exception.code, "TEST_SIZE")

    def test_repository_visibility_uses_full_repository_metadata(self):
        class RepositoryClient:
            def __init__(self, metadata: dict[str, object]):
                self.metadata = metadata

            def github(self, path: str):
                self.test_path = path
                return verify.HTTPResult(200, {}, json.dumps(self.metadata).encode())

        client = RepositoryClient(
            {"full_name": verify.REPOSITORY, "private": False, "visibility": "public"}
        )
        self.assertEqual(
            verify.repository_metadata(client),
            {"full_name": verify.REPOSITORY, "visibility": "public"},
        )
        self.assertEqual(client.test_path, f"/repos/{verify.REPOSITORY}")

        incomplete = RepositoryClient({"full_name": verify.REPOSITORY, "private": False})
        with self.assertRaises(verify.VerificationError) as caught:
            verify.repository_metadata(incomplete)
        self.assertEqual(caught.exception.code, "REPOSITORY_NOT_PUBLIC")

    def test_package_metadata_uses_repository_scoped_endpoint(self):
        class PackageClient:
            def __init__(self, metadata: dict[str, object]):
                self.metadata = metadata

            def github(self, path: str):
                self.test_path = path
                return verify.HTTPResult(200, {}, json.dumps(self.metadata).encode())

        encoded = urllib.parse.quote(verify.PACKAGE, safe="")
        client = PackageClient(
            {"name": verify.PACKAGE, "package_type": "container", "visibility": "public"}
        )
        self.assertEqual(
            verify.package_metadata(client),
            {
                "name": verify.PACKAGE,
                "type": "container",
                "visibility": "public",
                "repository": verify.REPOSITORY,
            },
        )
        self.assertEqual(client.test_path, f"/users/{verify.OWNER}/packages/container/{encoded}")

        mismatched = PackageClient(
            {
                "name": verify.PACKAGE,
                "package_type": "container",
                "visibility": "public",
                "repository": {"full_name": "other/repo", "name": "repo"},
            }
        )
        with self.assertRaises(verify.VerificationError) as caught:
            verify.package_metadata(mismatched)
        self.assertEqual(caught.exception.code, "PACKAGE_REPOSITORY_MISMATCH")

    def test_layer_scan_counts_whiteouts_and_scans_deleted_history(self):
        first = self.make_layer([("app/deleted.txt", b"safe synthetic content")])
        overlay: dict[str, str] = {}
        first_result = verify.scan_layer(first, verify.sha256_bytes(first), overlay)
        self.assertEqual(first_result["regular_files"], 1)
        self.assertIn("app/deleted.txt", overlay)

        second = self.make_layer([("app/.wh.deleted.txt", b"")])
        second_result = verify.scan_layer(second, verify.sha256_bytes(second), overlay)
        self.assertEqual(second_result["whiteouts"], 1)
        self.assertNotIn("app/deleted.txt", overlay)
        self.assertEqual(first_result["bytes_scanned"], len(b"safe synthetic content"))

    def test_layer_policy_failure_never_contains_match(self):
        secret = b"pass" + b"word" + b"=" + b"abcdefghijklmnop"
        layer = self.make_layer([("app/src/app.py", secret)])
        with self.assertRaises(verify.VerificationError) as caught:
            verify.scan_layer(layer, verify.sha256_bytes(layer), {})
        self.assertEqual(caught.exception.code, "IMAGE_LAYER_POLICY_MATCH")
        self.assertNotIn(secret.decode(), str(caught.exception))
        os_layer = self.make_layer([("usr/share/doc/example.txt", secret)])
        os_result = verify.scan_layer(os_layer, verify.sha256_bytes(os_layer), {})
        self.assertEqual(os_result["regular_files"], 1)
        pem = b"-----BEGIN PRIVATE KEY-----\nABCDEFGHIJKLMNOP\n-----END PRIVATE KEY-----\n"
        os_pem = self.make_layer([("usr/share/doc/openssl/examples/key.pem", pem)])
        os_pem_result = verify.scan_layer(os_pem, verify.sha256_bytes(os_pem), {})
        self.assertEqual(os_pem_result["regular_files"], 1)
        app_pem = self.make_layer([("app/src/app.py", pem)])
        with self.assertRaises(verify.VerificationError) as pem_caught:
            verify.scan_layer(app_pem, verify.sha256_bytes(app_pem), {})
        self.assertEqual(pem_caught.exception.code, "IMAGE_LAYER_POLICY_MATCH")
        self.assertIn("credential_pem", pem_caught.exception.categories)

    def test_dependency_write_route_is_not_treated_as_application_capability(self):
        payload = b"@app.route('/_dash-update-component', methods=['POST'])"
        layer = self.make_layer([(".venv/lib/python3.13/site-packages/dash/dash.py", payload)])
        result = verify.scan_layer(layer, verify.sha256_bytes(layer), {})
        self.assertEqual(result["regular_files"], 1)
        nested = self.make_layer([("app/.venv/lib/python3.13/site-packages/dash/dash.py", payload)])
        nested_result = verify.scan_layer(nested, verify.sha256_bytes(nested), {})
        self.assertEqual(nested_result["regular_files"], 1)
        identity_token = b"l" + b"so"
        broker_token = b"t" + b"ws"
        os_tokens = self.make_layer([("usr/share/dict/words", identity_token + b"\n" + broker_token + b"\n")])
        os_result = verify.scan_layer(os_tokens, verify.sha256_bytes(os_tokens), {})
        self.assertEqual(os_result["regular_files"], 1)

    def test_application_identity_token_still_fails_closed(self):
        payload = b"project = '" + b"l" + b"so" + b"-wheel-dashboard'\n"
        layer = self.make_layer([("app/src/app.py", payload)])
        with self.assertRaises(verify.VerificationError) as caught:
            verify.scan_layer(layer, verify.sha256_bytes(layer), {})
        self.assertEqual(caught.exception.code, "IMAGE_LAYER_POLICY_MATCH")
        self.assertIn("forbidden_identity", caught.exception.categories)

    def test_application_write_route_still_fails_closed(self):
        payload = b"@app.route('/orders', methods=['POST'])"
        layer = self.make_layer([("app/src/app.py", payload)])
        with self.assertRaises(verify.VerificationError) as caught:
            verify.scan_layer(layer, verify.sha256_bytes(layer), {})
        self.assertEqual(caught.exception.code, "IMAGE_LAYER_POLICY_MATCH")
        self.assertEqual(caught.exception.categories, ("order_capability", "write_route"))

    def test_failure_evidence_is_compact_and_sanitized(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            evidence = {
                "schema": 1,
                "result": "FAIL",
                "repository": verify.REPOSITORY,
                "run_id": "123",
                "commit": "a" * 40,
                "failure_code": "NETWORK_FAILURE",
                "sanitized": True,
            }
            verify.write_evidence(output, evidence)
            self.assertEqual(set(path.name for path in output.iterdir()), {"evidence.json", "evidence.md"})
            combined = (output / "evidence.json").read_text() + (output / "evidence.md").read_text()
            self.assertNotIn("token", combined.lower())
            self.assertNotIn("abcdefghijklmnop", combined)

    def test_verifier_declares_full_digest_and_layer_coverage(self):
        source = VERIFIER.read_text(encoding="utf-8")
        for value in (
            "all_index_descriptors_classified",
            "all_runnable_configs",
            "all_runnable_layers_including_whiteouts",
            "controlled_release_source_compared",
            "provenance_for_all_runnable_manifests",
            "TAG_DRIFT_DETECTED",
            "ANONYMOUS_NETWORK_FAILURE",
            "https://github.com/",
            "ATTESTATION_BUILDER_MISMATCH",
            "ATTESTATION_BUILD_TYPE_MISMATCH",
            verify.PINNED_INDEX_DIGEST,
            verify.PINNED_MANIFESTS["linux/amd64"],
            verify.PINNED_MANIFESTS["linux/arm64"],
            "COMPOSE_REFERENCE_MISMATCH",
        ):
            self.assertIn(value, source)

    def test_compose_reference_is_exactly_the_approved_index(self):
        self.assertEqual(
            verify.compose_reference(ROOT),
            f"{verify.IMAGE}@{verify.PINNED_INDEX_DIGEST}",
        )


if __name__ == "__main__":
    unittest.main()
