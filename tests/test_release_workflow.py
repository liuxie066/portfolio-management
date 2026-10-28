"""Synthetic release boundary tests: never invoke a real API or gh mutation."""
import io
import json
from pathlib import Path
import subprocess
from urllib.error import HTTPError, URLError

import pytest
import yaml

from scripts import publish_release as publisher

SHA = "a" * 40
OTHER = "b" * 40
TAG = "v1.2.3"
REPO = "example/synthetic"


class Reads:
    def __init__(self, releases=(None, False), commits=(SHA, SHA, SHA)):
        self.releases = iter(releases)
        self.commits = iter(commits)
        self.calls = []

    def get(self, path, *, missing=False):
        self.calls.append((path, missing))
        if path.startswith("/git/ref/"):
            return {"ref": f"refs/tags/{TAG}", "object": {"type": "commit", "sha": next(self.commits)}}
        if path.startswith("/releases?"):
            return []
        draft = next(self.releases)
        return None if draft is None else {"tag_name": TAG, "draft": draft}


def run(reads=None, mutate=None, **overrides):
    calls = []
    arguments = dict(reads=reads or Reads(), mutate=mutate or calls.append, repo=REPO,
                     tag=TAG, expected_sha=SHA, head_sha=SHA, version="1.2.3")
    arguments.update(overrides)
    return publisher.publish(**arguments), calls


def test_create_preserves_first_release_and_no_new_commit_cli_guards():
    result, calls = run()
    assert result == "verified-public"
    assert calls == [["gh", "release", "create", TAG, "--repo", REPO, "--verify-tag",
                      "--title", TAG, "--generate-notes", "--fail-on-no-commits", "--target", SHA]]


def test_draft_and_already_public_paths():
    assert run(Reads((True, False)))[1] == [["gh", "release", "edit", TAG, "--repo", REPO, "--draft=false"]]
    assert run(Reads((False,))) == ("already-public", [])


@pytest.mark.parametrize("override", [
    {"head_sha": OTHER}, {"expected_sha": "main"}, {"version": "1.2.4"},
    {"tag": "v1.2.3;command"}, {"tag": "v01.2.3"}, {"tag": "pm-api-v1.2.3"},
    {"repo": "example/repo/extra"},
])
def test_local_identity_fails_before_any_api_or_mutation(override):
    reads, calls = Reads(), []
    with pytest.raises(publisher.ReleaseError):
        run(reads, calls.append, **override)
    assert not reads.calls and not calls


@pytest.mark.parametrize("commits", [(OTHER,), (SHA, OTHER)])
def test_tag_mismatch_or_movement_prevents_write(commits):
    calls = []
    with pytest.raises(publisher.ReleaseError):
        run(Reads(commits=commits), calls.append)
    assert not calls


@pytest.mark.parametrize("readback", [None, True, False])
def test_failed_cli_gets_one_readback_never_second_invocation(readback):
    calls = []
    reads = Reads((None, readback))

    def fail(command):
        calls.append(command)
        raise subprocess.CalledProcessError(1, command, stderr="secret")

    if readback is False:
        assert run(reads, fail)[0] == "verified-public"
    else:
        with pytest.raises(publisher.ReleaseError, match="outcome unknown"):
            run(reads, fail)
    assert len(calls) == 1
    assert len(reads.calls) == (7 if readback is None else 6)


def test_successful_cli_with_moved_tag_is_unknown():
    calls = []
    with pytest.raises(publisher.ReleaseError, match="outcome unknown"):
        run(Reads(commits=(SHA, SHA, OTHER)), calls.append)
    assert len(calls) == 1


@pytest.mark.parametrize("cycle", [False, True])
def test_annotated_tag_peeling(cycle):
    class Annotated:
        def get(self, path):
            if "/ref/" in path:
                return {"ref": f"refs/tags/{TAG}", "object": {"type": "tag", "sha": OTHER}}
            return {"sha": OTHER, "object": {"type": "tag" if cycle else "commit", "sha": OTHER if cycle else SHA}}
    if cycle:
        with pytest.raises(publisher.ReleaseError, match="tag identity"):
            publisher._tag_commit(Annotated(), TAG)
    else:
        assert publisher._tag_commit(Annotated(), TAG) == SHA


@pytest.mark.parametrize("error", [403, 404, 429, 500, "network", "json"])
def test_read_adapter_distinguishes_release_missing_without_secret_leak(error):
    calls = []

    def open_url(request, *, timeout):
        calls.append(request)
        assert timeout == 10
        if error == "json":
            return io.BytesIO(b"private malformed")
        if error == "network":
            raise URLError("private token")
        raise HTTPError(request.full_url, error, "private", {}, None)

    reads = publisher.GitHubReads(REPO, "synthetic-secret", open_url=open_url)
    if error == 404:
        assert reads.get(f"/releases/tags/{TAG}", missing=True) is None
    else:
        with pytest.raises(publisher.ReleaseError, match="^GitHub read failed$"):
            reads.get(f"/releases/tags/{TAG}", missing=True)
    with pytest.raises(publisher.ReleaseError, match="^GitHub read failed$"):
        reads.get(f"/git/ref/tags/{TAG}", missing=True)
    assert all(r.full_url.startswith("https://api.github.com/repos/example/synthetic/") for r in calls)


def test_http_success_and_fixed_read_paths_and_no_redirect():
    reads = publisher.GitHubReads(REPO, "synthetic", open_url=lambda *a, **k: io.BytesIO(json.dumps({"draft": False}).encode()))
    assert reads.get(f"/releases/tags/{TAG}") == {"draft": False}
    for path in ("https://evil.test", "/../../secret", "/releases", "/git/tags/main"):
        with pytest.raises(publisher.ReleaseError):
            reads.get(path)
    assert publisher._NoRedirect().redirect_request(None, None, 302, None, None, "https://evil.test") is None


def test_quality_native_dependency_and_exact_identity_contract():
    root = Path(__file__).resolve().parents[1]
    release = yaml.load((root / ".github/workflows/release.yml").read_text(), Loader=yaml.BaseLoader)
    quality = yaml.load((root / ".github/workflows/quality-contract.yml").read_text(), Loader=yaml.BaseLoader)
    assert "workflow_call" in quality["on"]
    assert set(quality["on"]) == {"workflow_call", "push", "pull_request"}
    call = release["jobs"]["quality"]
    assert call["uses"] == "./.github/workflows/quality-contract.yml"
    assert call["permissions"] == {"contents": "read"}
    publish = release["jobs"]["release"]
    assert publish["needs"] == "quality"
    assert "if" not in publish and "continue-on-error" not in publish
    assert publish["timeout-minutes"] == "5"
    assert quality["jobs"]["quality-contract"]["timeout-minutes"] == "15"
    assert release["concurrency"] == {"group": "release-${{ github.ref }}", "cancel-in-progress": "false"}
    for job in (publish, quality["jobs"]["quality-contract"]):
        checkout = next(s for s in job["steps"] if s.get("uses") == "actions/checkout@v5")
        assert checkout["with"]["ref"] == "${{ github.sha }}"
        assert all("continue-on-error" not in s for s in job["steps"])
    assert "tests/test_release_workflow.py" in quality["jobs"]["quality-contract"]["steps"][-1]["run"]
    assert release["permissions"] == {"contents": "write"}


def test_draft_found_via_authenticated_list_after_tag_api_404():
    class DraftReads(Reads):
        def get(self, path, *, missing=False):
            if path.startswith("/releases?"):
                return [{"tag_name": TAG, "draft": True}]
            return super().get(path, missing=missing)
    result, calls = run(DraftReads())
    assert result == "verified-public"
    assert calls == [["gh", "release", "edit", TAG, "--repo", REPO, "--draft=false"]]


@pytest.mark.parametrize("damage", ["duplicate", "truncated", "malformed"])
def test_draft_listing_ambiguity_and_incompleteness_prevent_mutation(damage):
    class BadList(Reads):
        def get(self, path, *, missing=False):
            if path.startswith("/releases?"):
                if damage == "duplicate":
                    return [{"tag_name": TAG, "draft": True}] * 2
                if damage == "truncated":
                    return [{"tag_name": "v0.0.1", "draft": False}] * 100
                return [{"tag_name": TAG}]
            return super().get(path, missing=missing)
    calls = []
    with pytest.raises(publisher.ReleaseError):
        run(BadList(), calls.append)
    assert not calls


@pytest.mark.parametrize("repo", ["../repo", "owner/..", "./repo", "owner/."])
def test_repository_dot_segments_rejected(repo):
    with pytest.raises(publisher.ReleaseError):
        publisher.GitHubReads(repo, "synthetic")


def test_http_adapter_lists_drafts_on_second_page_before_undraft():
    state = {"public": False}
    requests, commands = [], []

    def open_url(request, *, timeout):
        path = request.full_url.split(REPO, 1)[1]
        requests.append(path)
        if path.startswith("/git/ref/"):
            payload = {"ref": f"refs/tags/{TAG}", "object": {"type": "commit", "sha": SHA}}
        elif path.startswith("/releases/tags/"):
            if not state["public"]:
                raise HTTPError(request.full_url, 404, "not published", {}, None)
            payload = {"tag_name": TAG, "draft": False}
        elif path.endswith("page=1"):
            payload = [{"tag_name": f"v0.0.{i}", "draft": False} for i in range(100)]
        else:
            payload = [{"tag_name": TAG, "draft": True}]
        return io.BytesIO(json.dumps(payload).encode())

    def mutate(command):
        commands.append(command)
        state["public"] = True

    reads = publisher.GitHubReads(REPO, "synthetic", open_url=open_url)
    assert run(reads, mutate)[0] == "verified-public"
    assert commands[0][2] == "edit"
    assert len(commands) == 1
    assert "/releases?per_page=100&page=2" in requests
