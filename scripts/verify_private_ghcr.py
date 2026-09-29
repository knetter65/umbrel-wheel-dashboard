from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import os
import re
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

REPOSITORY = "knetter65/umbrel-wheel-dashboard"
OWNER = "knetter65"
PACKAGE = "wheel-dashboard"
IMAGE = f"ghcr.io/{OWNER}/{PACKAGE}"
TAG = "0.3.0"
EVIDENCE_BRANCH = "verification/private-0.3.0"
EXPECTED_PLATFORMS = {("linux", "amd64"), ("linux", "arm64")}
MAX_API_BYTES = 128 * 1024 * 1024
MANIFEST_ACCEPT = ", ".join(
    (
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    )
)
INDEX_TYPES = {
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
}
MANIFEST_TYPES = {
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
}
PRIVATE_IDENTITY = b"l" + b"so"
PRIVATE_IDENTITY_PATTERN = rb"(?i)(?:\b" + PRIVATE_IDENTITY + rb"\b|options-" + PRIVATE_IDENTITY + rb"|ken-" + PRIVATE_IDENTITY + rb"-wheel-dashboard|" + PRIVATE_IDENTITY + rb"-wheel-dashboard)"
BROKER_MARKER_PATTERN = (
    rb"(?i)(?:interactive\s*" + b"bro" + b"kers" + rb"|\b" + b"ib" + b"kr" + rb"\b|\b" + b"t" + b"ws" + rb"\b|\bDU[0-9]{5,}\b|account[_ -]?(?:id|number)\s*[:=]\s*[A-Za-z0-9-]{5,})"
)
PRIVATE_DEPLOYMENT_PATTERN = (
    rb"(?i)(?:/home/" + b"umb" + b"rel/|/opt/" + b"data/" + b"profiles/|" + b"tail" + b"scale" + rb"|100\.(?:6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.[0-9]{1,3}\.[0-9]{1,3})"
)
POLICY_PATTERNS: tuple[tuple[str, re.Pattern[bytes]], ...] = (
    ("forbidden_identity", re.compile(PRIVATE_IDENTITY_PATTERN)),
    ("broker_or_account_data", re.compile(BROKER_MARKER_PATTERN)),
    ("private_deployment", re.compile(PRIVATE_DEPLOYMENT_PATTERN)),
    ("proprietary_training", re.compile(rb"(?i)(?:proprietary[_ -]?(?:training|dataset)|private[_ -]?training[_ -]?data)")),
    ("credential_value", re.compile(rb"(?i)(?:password|secret|api[_-]?key|access[_-]?token)\s*[:=]\s*['\"]?(?!\*+\b|x+\b|none\b|null\b|example\b|placeholder\b)[A-Za-z0-9_./+:-]{12,}")),
    ("order_capability", re.compile(rb"(?i)(?:place[_ -]?order|submit[_ -]?order|transmit[_ -]?order|/orders?(?:/|\b).{0,32}(?:post|put|patch|delete))")),
    ("write_route", re.compile(rb"(?i)(?:route\s*\([^\n]{0,160}methods\s*=\s*[^\n]{0,80}(?:post|put|patch|delete))")),
)


class VerificationError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def fail(condition: bool, code: str) -> None:
    if condition:
        raise VerificationError(code)


def sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def digest_ok(data: bytes, digest: str) -> bool:
    return digest.startswith("sha256:") and sha256_bytes(data) == digest


def verify_descriptor_payload(data: bytes, descriptor: dict[str, Any], code: str) -> None:
    digest = descriptor.get("digest")
    size = descriptor.get("size")
    fail(not isinstance(digest, str) or not digest_ok(data, digest), f"{code}_DIGEST")
    fail(not isinstance(size, int) or size < 0 or size != len(data), f"{code}_SIZE")


def safe_json(data: bytes, code: str) -> dict[str, Any]:
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError(code) from exc
    if not isinstance(value, dict):
        raise VerificationError(code)
    return value


@dataclass
class HTTPResult:
    status: int
    headers: Any
    body: bytes


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is None:
            return None
        old = urllib.parse.urlsplit(req.full_url)
        new = urllib.parse.urlsplit(newurl)
        if (old.scheme, old.hostname, old.port) != (new.scheme, new.hostname, new.port):
            redirected.remove_header("Authorization")
        return redirected


class Client:
    def __init__(self, github_token: str, actor: str):
        fail(not github_token or not actor, "MISSING_EPHEMERAL_CREDENTIAL")
        self.github_token = github_token
        self.actor = actor
        self.registry_token = ""
        self.opener = urllib.request.build_opener(SafeRedirectHandler())

    def request(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        expected: tuple[int, ...] = (200,),
        max_bytes: int = MAX_API_BYTES,
    ) -> HTTPResult:
        request = urllib.request.Request(url, headers=headers or {})
        try:
            with self.opener.open(request, timeout=60) as response:
                body = response.read(max_bytes + 1)
                fail(len(body) > max_bytes, "HTTP_BODY_LIMIT")
                result = HTTPResult(response.status, response.headers, body)
        except urllib.error.HTTPError as exc:
            body = exc.read(4096)
            result = HTTPResult(exc.code, exc.headers, body)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise VerificationError("NETWORK_FAILURE") from exc
        fail(result.status not in expected, f"HTTP_STATUS_{result.status}")
        return result

    def github(self, path: str, *, expected: tuple[int, ...] = (200,), max_bytes: int = MAX_API_BYTES) -> HTTPResult:
        return self.request(
            "https://api.github.com" + path,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.github_token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "wheel-dashboard-private-verifier",
            },
            expected=expected,
            max_bytes=max_bytes,
        )

    def acquire_registry_token(self) -> None:
        query = urllib.parse.urlencode({"service": "ghcr.io", "scope": f"repository:{OWNER}/{PACKAGE}:pull"})
        basic = base64.b64encode(f"{self.actor}:{self.github_token}".encode()).decode()
        result = self.request(
            f"https://ghcr.io/token?{query}",
            headers={"Authorization": f"Basic {basic}", "User-Agent": "wheel-dashboard-private-verifier"},
        )
        payload = safe_json(result.body, "REGISTRY_TOKEN_JSON")
        token = payload.get("token") or payload.get("access_token")
        fail(not isinstance(token, str) or not token, "REGISTRY_TOKEN_MISSING")
        self.registry_token = token

    def registry(self, reference: str, *, manifest: bool = False) -> HTTPResult:
        fail(not self.registry_token, "REGISTRY_TOKEN_NOT_ACQUIRED")
        kind = "manifests" if manifest else "blobs"
        headers = {"Authorization": f"Bearer {self.registry_token}", "User-Agent": "wheel-dashboard-private-verifier"}
        if manifest:
            headers["Accept"] = MANIFEST_ACCEPT
        return self.request(f"https://ghcr.io/v2/{OWNER}/{PACKAGE}/{kind}/{reference}", headers=headers)


def scan_bytes(data: bytes) -> set[str]:
    return {name for name, pattern in POLICY_PATTERNS if pattern.search(data)}


def scan_stream(stream: BinaryIO, size: int) -> tuple[int, set[str]]:
    remaining = size
    scanned = 0
    overlap = b""
    findings: set[str] = set()
    while remaining:
        chunk = stream.read(min(1024 * 1024, remaining))
        fail(not chunk, "TRUNCATED_LAYER_MEMBER")
        remaining -= len(chunk)
        scanned += len(chunk)
        window = overlap + chunk
        findings.update(scan_bytes(window))
        overlap = window[-512:]
    return scanned, findings


def scan_logs(payload: bytes) -> dict[str, Any]:
    fail(len(payload) > MAX_API_BYTES, "LOG_ARCHIVE_LIMIT")
    findings: set[str] = set()
    files = 0
    scanned = 0
    names: set[str] = set()
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            for item in archive.infolist():
                fail(item.is_dir(), "LOG_ARCHIVE_DIRECTORY")
                fail(item.file_size > MAX_API_BYTES, "LOG_FILE_LIMIT")
                path = PurePosixPath(item.filename)
                fail(path.is_absolute() or ".." in path.parts or item.filename in names, "LOG_ARCHIVE_PATH_INVALID")
                names.add(item.filename)
                findings.update(scan_bytes(item.filename.encode()))
                files += 1
                with archive.open(item) as source:
                    count, item_findings = scan_stream(source, item.file_size)
                scanned += count
                fail(scanned > MAX_API_BYTES, "LOG_ARCHIVE_EXPANDED_LIMIT")
                findings.update(item_findings)
    except (zipfile.BadZipFile, RuntimeError) as exc:
        raise VerificationError("LOG_ARCHIVE_INVALID") from exc
    fail(bool(findings), "UPSTREAM_LOG_POLICY_MATCH")
    return {"archive_files": files, "bytes_scanned": scanned, "policy_categories_matched": []}


def repository_metadata(client: Client) -> dict[str, str]:
    repository = safe_json(client.github(f"/repos/{REPOSITORY}").body, "REPOSITORY_JSON")
    fail(
        repository.get("full_name") != REPOSITORY
        or repository.get("private") is not True
        or repository.get("visibility") != "private",
        "REPOSITORY_NOT_PRIVATE",
    )
    return {"full_name": REPOSITORY, "visibility": "private"}


def github_run_and_jobs(client: Client, run_id: int, sha: str) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    run = safe_json(client.github(f"/repos/{REPOSITORY}/actions/runs/{run_id}").body, "RUN_JSON")
    fail(run.get("id") != run_id, "RUN_ID_MISMATCH")
    fail(run.get("name") != "ci", "RUN_WORKFLOW_MISMATCH")
    fail(run.get("event") != "push", "RUN_EVENT_MISMATCH")
    fail(run.get("head_branch") != "main", "RUN_BRANCH_MISMATCH")
    fail(run.get("head_sha") != sha, "RUN_SHA_MISMATCH")
    fail(run.get("conclusion") != "success" or run.get("status") != "completed", "RUN_NOT_SUCCESSFUL")
    run_repository = run.get("repository") or {}
    fail(run_repository.get("full_name") != REPOSITORY or run_repository.get("private") is not True, "RUN_REPOSITORY_MISMATCH")
    attempt = run.get("run_attempt")
    fail(not isinstance(attempt, int) or attempt < 1, "RUN_ATTEMPT_INVALID")

    jobs: list[dict[str, Any]] = []
    page = 1
    while True:
        result = safe_json(
            client.github(f"/repos/{REPOSITORY}/actions/runs/{run_id}/attempts/{attempt}/jobs?filter=all&per_page=100&page={page}").body,
            "JOBS_JSON",
        )
        batch = result.get("jobs")
        fail(not isinstance(batch, list), "JOBS_INVALID")
        for job in batch:
            fail(not isinstance(job, dict), "JOB_INVALID")
            jobs.append({"id": job.get("id"), "name": job.get("name"), "status": job.get("status"), "conclusion": job.get("conclusion")})
        if len(batch) < 100:
            break
        page += 1
        fail(page > 20, "JOBS_PAGINATION_LIMIT")
    fail(not jobs, "JOBS_EMPTY")
    fail(any(job["status"] != "completed" or job["conclusion"] not in {"success", "skipped"} for job in jobs), "UPSTREAM_JOB_FAILURE")
    required = {"test", "publish-private-ghcr"}
    successful = {str(job["name"]).split(" / ")[-1] for job in jobs if job["conclusion"] == "success"}
    fail(not required.issubset(successful), "REQUIRED_JOBS_MISSING")

    logs = scan_logs(client.github(f"/repos/{REPOSITORY}/actions/runs/{run_id}/attempts/{attempt}/logs").body)
    summary = {
        "id": run_id,
        "attempt": attempt,
        "event": run["event"],
        "head_branch": run["head_branch"],
        "head_sha": run["head_sha"],
        "status": run["status"],
        "conclusion": run["conclusion"],
    }
    return summary, jobs, logs


def package_metadata(client: Client) -> dict[str, Any]:
    encoded = urllib.parse.quote(PACKAGE, safe="")
    package = safe_json(
        client.github(f"/users/{OWNER}/packages/container/{encoded}").body,
        "PACKAGE_JSON",
    )
    fail(package.get("name") != PACKAGE or package.get("package_type") != "container", "PACKAGE_IDENTITY_MISMATCH")
    fail(package.get("visibility") != "private", "PACKAGE_NOT_PRIVATE")
    owner = package.get("owner") or {}
    fail(owner.get("login") not in {None, OWNER}, "PACKAGE_OWNER_MISMATCH")
    repository = package.get("repository") or {}
    if repository:
        fail(
            repository.get("full_name") not in {None, REPOSITORY} or repository.get("name") not in {None, "umbrel-wheel-dashboard"},
            "PACKAGE_REPOSITORY_MISMATCH",
        )
    return {"name": PACKAGE, "type": "container", "visibility": "private", "repository": REPOSITORY}


def anonymous_denial(index_digest: str) -> dict[str, Any]:
    url = f"https://ghcr.io/v2/{OWNER}/{PACKAGE}/manifests/{index_digest}"
    request = urllib.request.Request(url, headers={"Accept": MANIFEST_ACCEPT, "User-Agent": "wheel-dashboard-private-verifier"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            response.read(4096)
            status = response.status
    except urllib.error.HTTPError as exc:
        exc.read(4096)
        status = exc.code
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise VerificationError("ANONYMOUS_NETWORK_FAILURE") from exc
    fail(status not in {401, 403}, "ANONYMOUS_ARTIFACT_ACCESSIBLE")
    return {"reference": index_digest, "result": "auth_denied", "http_status": status}


def controlled_files(root: Path) -> dict[str, str]:
    expected: dict[str, str] = {}
    for relative in ("pyproject.toml", "uv.lock"):
        expected[f"app/{relative}"] = sha256_bytes((root / relative).read_bytes())
    for directory in ("src", "schema", "assets", "fixtures"):
        for path in sorted((root / directory).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.name != "phase1-fixture.db":
                expected[(PurePosixPath("app") / path.relative_to(root)).as_posix()] = sha256_bytes(path.read_bytes())
    return expected


def scan_layer(data: bytes, digest: str, overlay: dict[str, str], expected_diff_id: str | None = None) -> dict[str, Any]:
    fail(not digest_ok(data, digest), "LAYER_DIGEST_MISMATCH")
    try:
        uncompressed = gzip.decompress(data) if data.startswith(b"\x1f\x8b") else data
    except (gzip.BadGzipFile, EOFError, OSError) as exc:
        raise VerificationError("LAYER_COMPRESSION_INVALID") from exc
    diff_id = sha256_bytes(uncompressed)
    fail(expected_diff_id is not None and diff_id != expected_diff_id, "LAYER_DIFF_ID_MISMATCH")
    findings: set[str] = set()
    members = regular = non_regular = whiteouts = bytes_scanned = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(uncompressed), mode="r:") as archive:
            for member in archive:
                members += 1
                raw_path = PurePosixPath(member.name)
                fail(raw_path.is_absolute() or ".." in raw_path.parts, "UNSAFE_LAYER_PATH")
                path = PurePosixPath(member.name[2:] if member.name.startswith("./") else member.name)
                normalized = path.as_posix()
                findings.update(scan_bytes(normalized.encode()))
                findings.update(scan_bytes(member.linkname.encode()))
                for key, value in sorted(member.pax_headers.items()):
                    findings.update(scan_bytes(f"{key}={value}".encode()))
                name = path.name
                if name == ".wh..wh..opq":
                    whiteouts += 1
                    prefix = path.parent.as_posix().rstrip("/") + "/"
                    for existing in list(overlay):
                        if existing.startswith(prefix):
                            overlay.pop(existing)
                    continue
                if name.startswith(".wh."):
                    whiteouts += 1
                    target = (path.parent / name[4:]).as_posix()
                    overlay.pop(target, None)
                    continue
                if member.isfile():
                    regular += 1
                    source = archive.extractfile(member)
                    fail(source is None, "LAYER_MEMBER_UNREADABLE")
                    payload = source.read()
                    fail(len(payload) != member.size, "LAYER_MEMBER_SIZE_MISMATCH")
                    bytes_scanned += len(payload)
                    findings.update(scan_bytes(payload))
                    prefix = normalized.rstrip("/") + "/"
                    for existing in list(overlay):
                        if existing.startswith(prefix):
                            overlay.pop(existing)
                    overlay[normalized] = sha256_bytes(payload)
                else:
                    non_regular += 1
                    if not member.isdir():
                        prefix = normalized.rstrip("/") + "/"
                        for existing in list(overlay):
                            if existing.startswith(prefix):
                                overlay.pop(existing)
                    overlay[normalized] = "nonregular:" + member.type.hex()
    except (tarfile.TarError, OSError) as exc:
        raise VerificationError("LAYER_TAR_INVALID") from exc
    fail(bool(findings), "IMAGE_LAYER_POLICY_MATCH")
    return {
        "digest": digest,
        "diff_id": diff_id,
        "members": members,
        "regular_files": regular,
        "non_regular_entries": non_regular,
        "whiteouts": whiteouts,
        "bytes_scanned": bytes_scanned,
        "policy_categories_matched": [],
    }


def verify_attestation(client: Client, descriptor: dict[str, Any]) -> dict[str, Any]:
    digest = descriptor.get("digest")
    fail(not isinstance(digest, str), "ATTESTATION_DIGEST_MISSING")
    manifest_data = client.registry(digest, manifest=True).body
    verify_descriptor_payload(manifest_data, descriptor, "ATTESTATION_MANIFEST")
    manifest = safe_json(manifest_data, "ATTESTATION_MANIFEST_JSON")
    fail(manifest.get("mediaType") not in MANIFEST_TYPES, "ATTESTATION_MANIFEST_TYPE")
    layers = manifest.get("layers")
    fail(not isinstance(layers, list) or not layers, "ATTESTATION_LAYERS_MISSING")
    subjects: set[str] = set()
    predicate_types: set[str] = set()
    builder_ids: set[str] = set()
    build_types: set[str] = set()
    for layer in layers:
        fail(not isinstance(layer, dict) or not isinstance(layer.get("digest"), str), "ATTESTATION_LAYER_INVALID")
        payload = client.registry(layer["digest"]).body
        verify_descriptor_payload(payload, layer, "ATTESTATION_LAYER")
        fail(bool(scan_bytes(payload)), "ATTESTATION_POLICY_MATCH")
        for line in payload.splitlines():
            if not line.strip():
                continue
            statement = safe_json(line, "ATTESTATION_STATEMENT_JSON")
            fail(statement.get("_type") not in {"https://in-toto.io/Statement/v0.1", "https://in-toto.io/Statement/v1"}, "ATTESTATION_STATEMENT_TYPE")
            predicate_type = statement.get("predicateType")
            fail(predicate_type not in {"https://slsa.dev/provenance/v0.2", "https://slsa.dev/provenance/v1"}, "ATTESTATION_NOT_SLSA")
            predicate_types.add(predicate_type)
            predicate = statement.get("predicate")
            fail(not isinstance(predicate, dict), "ATTESTATION_PREDICATE_MISSING")
            if predicate_type == "https://slsa.dev/provenance/v0.2":
                builder = predicate.get("builder")
                build_type = predicate.get("buildType")
            else:
                run_details = predicate.get("runDetails")
                build_definition = predicate.get("buildDefinition")
                fail(not isinstance(run_details, dict) or not isinstance(build_definition, dict), "ATTESTATION_V1_STRUCTURE")
                builder = run_details.get("builder")
                build_type = build_definition.get("buildType")
            fail(not isinstance(builder, dict), "ATTESTATION_BUILDER_MISSING")
            builder_id = builder.get("id")
            fail(not isinstance(builder_id, str) or not builder_id.startswith("https://github.com/docker/build-push-action"), "ATTESTATION_BUILDER_MISMATCH")
            fail(build_type != "https://mobyproject.org/buildkit@v1", "ATTESTATION_BUILD_TYPE_MISMATCH")
            builder_ids.add(builder_id)
            build_types.add(build_type)
            statement_subjects = statement.get("subject")
            fail(not isinstance(statement_subjects, list) or not statement_subjects, "ATTESTATION_SUBJECT_MISSING")
            for subject in statement_subjects:
                value = (subject.get("digest") or {}).get("sha256") if isinstance(subject, dict) else None
                if isinstance(value, str):
                    subjects.add("sha256:" + value)
    annotations = descriptor.get("annotations") or {}
    referenced = annotations.get("vnd.docker.reference.digest")
    fail(not isinstance(referenced, str) or referenced not in subjects, "ATTESTATION_SUBJECT_MISMATCH")
    return {
        "manifest_digest": digest,
        "subject_digest": referenced,
        "predicate_types": sorted(predicate_types),
        "builder_ids": sorted(builder_ids),
        "build_types": sorted(build_types),
    }


def verify_image(
    client: Client,
    descriptor: dict[str, Any],
    sha: str,
    expected_files: dict[str, str],
) -> dict[str, Any]:
    digest = descriptor.get("digest")
    platform = descriptor.get("platform") or {}
    pair = (platform.get("os"), platform.get("architecture"))
    fail(pair not in EXPECTED_PLATFORMS, "UNEXPECTED_RUNNABLE_PLATFORM")
    manifest_data = client.registry(digest, manifest=True).body
    verify_descriptor_payload(manifest_data, descriptor, "MANIFEST")
    manifest = safe_json(manifest_data, "MANIFEST_JSON")
    fail(manifest.get("mediaType") not in MANIFEST_TYPES, "IMAGE_MANIFEST_TYPE")
    config_descriptor = manifest.get("config") or {}
    config_digest = config_descriptor.get("digest")
    fail(not isinstance(config_digest, str), "CONFIG_DIGEST_MISSING")
    config_data = client.registry(config_digest).body
    verify_descriptor_payload(config_data, config_descriptor, "CONFIG")
    fail(bool(scan_bytes(config_data)), "IMAGE_CONFIG_POLICY_MATCH")
    config = safe_json(config_data, "CONFIG_JSON")
    runtime = config.get("config") or {}
    labels = runtime.get("Labels") or {}
    fail(labels.get("org.opencontainers.image.revision") != sha, "REVISION_LABEL_MISMATCH")
    fail(labels.get("org.opencontainers.image.source") != f"https://github.com/{REPOSITORY}", "SOURCE_LABEL_MISMATCH")
    fail(labels.get("org.opencontainers.image.version") != TAG, "VERSION_LABEL_MISMATCH")
    fail(runtime.get("User") != "1000:1000", "RUNTIME_USER_MISMATCH")
    fail(runtime.get("Cmd") != ["/app/.venv/bin/python", "-m", "src.app"], "RUNTIME_CMD_MISMATCH")
    fail(runtime.get("Entrypoint") not in (None, []), "RUNTIME_ENTRYPOINT_UNEXPECTED")
    layers = manifest.get("layers")
    fail(not isinstance(layers, list) or not layers, "LAYERS_MISSING")
    diff_ids = (config.get("rootfs") or {}).get("diff_ids")
    fail(not isinstance(diff_ids, list) or len(diff_ids) != len(layers), "ROOTFS_LAYER_COUNT_MISMATCH")
    overlay: dict[str, str] = {}
    layer_evidence: list[dict[str, Any]] = []
    for index, layer in enumerate(layers):
        fail(not isinstance(layer, dict) or not isinstance(layer.get("digest"), str), "LAYER_DESCRIPTOR_INVALID")
        layer_data = client.registry(layer["digest"]).body
        verify_descriptor_payload(layer_data, layer, "LAYER")
        layer_evidence.append(scan_layer(layer_data, layer["digest"], overlay, diff_ids[index]))
    actual = {path: overlay.get(path) for path in expected_files}
    fail(actual != expected_files, "CONTROLLED_SOURCE_MISMATCH")
    return {
        "platform": {"os": pair[0], "architecture": pair[1]},
        "manifest_digest": digest,
        "config_digest": config_digest,
        "layers": layer_evidence,
        "controlled_files_compared": len(expected_files),
        "labels": {
            "source": labels["org.opencontainers.image.source"],
            "revision": labels["org.opencontainers.image.revision"],
            "version": labels["org.opencontainers.image.version"],
        },
        "runtime": {"user": runtime["User"], "entrypoint": runtime.get("Entrypoint"), "cmd": runtime["Cmd"]},
    }


def classify_descriptor_tree(
    client: Client,
    descriptor: dict[str, Any],
    classified: list[dict[str, Any]],
    runnable: list[dict[str, Any]],
    attestations: list[dict[str, Any]],
    seen: set[str],
) -> None:
    digest = descriptor.get("digest")
    media_type = descriptor.get("mediaType")
    fail(not isinstance(digest, str) or digest in seen, "DUPLICATE_OR_INVALID_DESCRIPTOR")
    fail(not isinstance(media_type, str), "DESCRIPTOR_MEDIA_TYPE_MISSING")
    seen.add(digest)
    platform = descriptor.get("platform") or {}
    pair = (platform.get("os"), platform.get("architecture"))

    if media_type in INDEX_TYPES:
        classified.append(
            {
                "digest": digest,
                "media_type": media_type,
                "platform": {"os": pair[0], "architecture": pair[1]},
                "classification": "nested_index",
            }
        )
        payload = client.registry(digest, manifest=True).body
        verify_descriptor_payload(payload, descriptor, "NESTED_INDEX")
        nested = safe_json(payload, "NESTED_INDEX_JSON")
        fail(nested.get("mediaType") not in INDEX_TYPES, "NESTED_INDEX_TYPE_MISMATCH")
        children = nested.get("manifests")
        fail(not isinstance(children, list) or not children, "NESTED_INDEX_EMPTY")
        for child in children:
            fail(not isinstance(child, dict), "NESTED_DESCRIPTOR_INVALID")
            classify_descriptor_tree(client, child, classified, runnable, attestations, seen)
        return

    fail(media_type not in MANIFEST_TYPES, "UNCLASSIFIED_DESCRIPTOR_MEDIA_TYPE")
    annotations = descriptor.get("annotations") or {}
    if pair in EXPECTED_PLATFORMS:
        kind = "runnable"
        runnable.append(descriptor)
    elif pair == ("unknown", "unknown") and annotations.get("vnd.docker.reference.type") == "attestation-manifest":
        kind = "provenance"
        attestations.append(descriptor)
    else:
        raise VerificationError("UNCLASSIFIED_INDEX_DESCRIPTOR")
    classified.append(
        {
            "digest": digest,
            "media_type": media_type,
            "platform": {"os": pair[0], "architecture": pair[1]},
            "classification": kind,
        }
    )


def registry_evidence(client: Client, sha: str, root: Path) -> dict[str, Any]:
    client.acquire_registry_token()
    first = client.registry(TAG, manifest=True)
    index_digest = first.headers.get("Docker-Content-Digest")
    fail(not isinstance(index_digest, str) or not digest_ok(first.body, index_digest), "INDEX_DIGEST_MISMATCH")
    index = safe_json(first.body, "INDEX_JSON")
    fail(index.get("mediaType") not in INDEX_TYPES, "ROOT_NOT_INDEX")
    descriptors = index.get("manifests")
    fail(not isinstance(descriptors, list) or not descriptors, "INDEX_DESCRIPTORS_MISSING")

    classified: list[dict[str, Any]] = []
    runnable: list[dict[str, Any]] = []
    attestations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for descriptor in descriptors:
        fail(not isinstance(descriptor, dict), "INDEX_DESCRIPTOR_INVALID")
        classify_descriptor_tree(client, descriptor, classified, runnable, attestations, seen)
    platforms = {(item.get("platform") or {}).get("os") + "/" + (item.get("platform") or {}).get("architecture") for item in runnable}
    fail(platforms != {"linux/amd64", "linux/arm64"} or len(runnable) != 2, "RUNNABLE_PLATFORM_SET_MISMATCH")
    fail(len(attestations) != 2, "PROVENANCE_SET_MISMATCH")

    expected = controlled_files(root)
    images = [verify_image(client, descriptor, sha, expected) for descriptor in runnable]
    provenance = [verify_attestation(client, descriptor) for descriptor in attestations]
    image_digests = {image["manifest_digest"] for image in images}
    fail({item["subject_digest"] for item in provenance} != image_digests, "PROVENANCE_COVERAGE_MISMATCH")

    anonymous = anonymous_denial(index_digest)
    second = client.registry(TAG, manifest=True)
    second_digest = second.headers.get("Docker-Content-Digest")
    fail(second_digest != index_digest or second.body != first.body, "TAG_DRIFT_DETECTED")
    return {
        "image": f"{IMAGE}:{TAG}",
        "authorized_read": "success",
        "anonymous_read": anonymous,
        "index_digest": index_digest,
        "descriptors": classified,
        "images": sorted(images, key=lambda item: item["platform"]["architecture"]),
        "provenance": sorted(provenance, key=lambda item: item["subject_digest"]),
        "tag_drift_check": "stable",
    }


def markdown(evidence: dict[str, Any]) -> str:
    if evidence["result"] != "PASS":
        return "\n".join(
            (
                "# Private GHCR verification",
                "",
                f"- Result: **FAIL**",
                f"- Failure code: `{evidence['failure_code']}`",
                f"- Repository: `{REPOSITORY}`",
                f"- Upstream run: `{evidence['run_id']}`",
                f"- Commit: `{evidence['commit']}`",
                "- Evidence is sanitized; no raw log or matched content is included.",
                "",
            )
        )
    registry = evidence["registry"]
    lines = [
        "# Private GHCR verification",
        "",
        "- Result: **PASS**",
        f"- Repository: `{REPOSITORY}` (PRIVATE)",
        f"- Upstream run: `{evidence['upstream']['id']}` attempt `{evidence['upstream']['attempt']}`",
        f"- Commit: `{evidence['commit']}`",
        f"- Artifact: `{registry['image']}`",
        f"- Index digest: `{registry['index_digest']}`",
        "- Anonymous concrete-digest read: auth denied",
        "- Platforms: `linux/amd64`, `linux/arm64` (exact runnable set)",
        f"- Upstream logs scanned: `{evidence['logs']['archive_files']}` files / `{evidence['logs']['bytes_scanned']}` bytes",
        "- Every runnable manifest config and layer was digest-verified and scanned, including whiteouts and deleted-content history.",
        "- Controlled application files, OCI revision/source/version labels, SLSA provenance subjects and upstream run metadata agree.",
        "",
        "## Runnable images",
        "",
    ]
    for image in registry["images"]:
        platform = image["platform"]
        lines.append(f"- `{platform['os']}/{platform['architecture']}` manifest `{image['manifest_digest']}`, config `{image['config_digest']}`, layers `{len(image['layers'])}`")
    lines.extend(("", "Evidence is compact and sanitized; it contains no credentials, raw logs, or matched content.", ""))
    return "\n".join(lines)


def write_evidence(output: Path, evidence: dict[str, Any]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "evidence.json").write_text(json.dumps(evidence, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    (output / "evidence.md").write_text(markdown(evidence), encoding="utf-8")


def main() -> int:
    output = Path(os.environ.get("EVIDENCE_DIR", "verification-evidence")).resolve()
    run_id_text = os.environ.get("UPSTREAM_RUN_ID", "")
    sha = os.environ.get("UPSTREAM_SHA", "")
    base = {"schema": 1, "repository": REPOSITORY, "run_id": run_id_text, "commit": sha}
    try:
        fail(not run_id_text.isdigit(), "RUN_ID_INVALID")
        run_id = int(run_id_text)
        fail(not re.fullmatch(r"[0-9a-f]{40}", sha), "COMMIT_SHA_INVALID")
        client = Client(os.environ.get("GITHUB_TOKEN", ""), os.environ.get("GITHUB_ACTOR", ""))
        repository = repository_metadata(client)
        upstream, jobs, logs = github_run_and_jobs(client, run_id, sha)
        package = package_metadata(client)
        registry = registry_evidence(client, sha, Path(__file__).resolve().parents[1])
        evidence = {
            "schema": 1,
            "result": "PASS",
            "repository": repository,
            "commit": sha,
            "upstream": upstream,
            "jobs": jobs,
            "logs": logs,
            "package": package,
            "registry": registry,
            "coverage": {
                "all_upstream_jobs": True,
                "all_log_files": True,
                "all_index_descriptors_classified": True,
                "all_runnable_configs": True,
                "all_runnable_layers_including_whiteouts": True,
                "controlled_release_source_compared": True,
                "provenance_for_all_runnable_manifests": True,
            },
        }
        write_evidence(output, evidence)
        print("PRIVATE_GHCR_VERIFICATION_PASS")
        return 0
    except VerificationError as exc:
        evidence = {**base, "result": "FAIL", "failure_code": exc.code, "sanitized": True}
        write_evidence(output, evidence)
        print(f"PRIVATE_GHCR_VERIFICATION_FAIL code={exc.code}", file=sys.stderr)
        return 1
    except Exception:
        evidence = {**base, "result": "FAIL", "failure_code": "INTERNAL_FAILURE", "sanitized": True}
        write_evidence(output, evidence)
        print("PRIVATE_GHCR_VERIFICATION_FAIL code=INTERNAL_FAILURE", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
