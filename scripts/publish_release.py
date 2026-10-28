"""Product publisher gated by Quality Contract; one CLI invocation, then readback.

The CLI may make several HTTP requests internally. Unknown results are never
retried here; this does not implement an exactly-once remote transaction.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener


class ReleaseError(Exception):
    """Fixed diagnostic without provider or subprocess output."""


def _valid_repo(repo):
    return (re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo)
            and all(part not in {".", ".."} for part in repo.split("/")))


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHubReads:
    def __init__(self, repo, token, *, open_url=None):
        if not _valid_repo(repo) or not token:
            raise ReleaseError("invalid publisher configuration")
        self.repo = repo
        self.token = token
        self.open_url = open_url or build_opener(_NoRedirect()).open

    def get(self, path, *, missing=False):
        if not re.fullmatch(r"/(?:releases\?per_page=100&page=[1-9][0-9]*|releases/tags/v[0-9]+\.[0-9]+\.[0-9]+|git/ref/tags/v[0-9]+\.[0-9]+\.[0-9]+|git/tags/[0-9a-f]{40})", path):
            raise ReleaseError("invalid API read path")
        request = Request(
            f"https://api.github.com/repos/{self.repo}{path}",
            headers={"Authorization": f"Bearer {self.token}",
                     "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28"},
        )
        try:
            with self.open_url(request, timeout=10) as response:
                data = json.load(response)
            expected_type = list if path.startswith("/releases?") else dict
            if not isinstance(data, expected_type):
                raise ValueError()
            return data
        except HTTPError as exc:
            if exc.code == 404 and missing and path.startswith("/releases/tags/"):
                return None
            raise ReleaseError("GitHub read failed") from None
        except Exception:
            raise ReleaseError("GitHub read failed") from None


def _tag_commit(reads, tag):
    data = reads.get(f"/git/ref/tags/{tag}")
    if not isinstance(data, dict) or data.get("ref") != f"refs/tags/{tag}":
        raise ReleaseError("invalid tag identity")
    seen = set()
    for _ in range(10):
        obj = data.get("object")
        if not isinstance(obj, dict) or not re.fullmatch(r"[0-9a-f]{40}", str(obj.get("sha", ""))):
            raise ReleaseError("invalid tag identity")
        sha = obj["sha"]
        if obj.get("type") == "commit":
            return sha
        if obj.get("type") != "tag" or sha in seen:
            raise ReleaseError("invalid tag identity")
        seen.add(sha)
        data = reads.get(f"/git/tags/{sha}")
        if not isinstance(data, dict) or data.get("sha") != sha:
            raise ReleaseError("invalid tag identity")
    raise ReleaseError("invalid tag identity")


def _release(reads, tag):
    data = reads.get(f"/releases/tags/{tag}", missing=True)
    if data is None:
        # The by-tag API promises published releases only. Authenticated list
        # includes drafts for the existing contents-write publisher token.
        matches = []
        for page in range(1, 11):
            rows = reads.get(f"/releases?per_page=100&page={page}")
            if (not isinstance(rows, list) or len(rows) > 100
                    or any(not isinstance(row, dict) or not isinstance(row.get("tag_name"), str)
                           or type(row.get("draft")) is not bool for row in rows)):
                raise ReleaseError("invalid release listing")
            matches.extend(row for row in rows if row["tag_name"] == tag)
            if len(matches) > 1:
                raise ReleaseError("ambiguous release identity")
            if len(rows) < 100:
                break
        else:
            raise ReleaseError("release listing incomplete")
        data = matches[0] if matches else None
    if data is not None and (
        not isinstance(data, dict) or data.get("tag_name") != tag
        or type(data.get("draft")) is not bool
    ):
        raise ReleaseError("invalid release identity")
    return data


def publish(*, reads, mutate, repo, tag, expected_sha, head_sha, version):
    """All reads and the single mutation invocation are injectable for tests."""
    if (not re.fullmatch(r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", tag)
            or not re.fullmatch(r"[0-9a-f]{40}", expected_sha)
            or head_sha != expected_sha or version != tag[1:]
            or not _valid_repo(repo)):
        raise ReleaseError("release source identity mismatch")
    if _tag_commit(reads, tag) != expected_sha:
        raise ReleaseError("release source identity mismatch")
    existing = _release(reads, tag)
    if _tag_commit(reads, tag) != expected_sha:
        raise ReleaseError("release source identity mismatch")
    if existing is not None and not existing["draft"]:
        return "already-public"
    if existing is None:
        command = ["gh", "release", "create", tag, "--repo", repo, "--verify-tag",
                   "--title", tag, "--generate-notes", "--fail-on-no-commits",
                   "--target", expected_sha]
    else:
        command = ["gh", "release", "edit", tag, "--repo", repo, "--draft=false"]
    try:
        mutate(command)
    except Exception:
        pass  # Outcome may be unknown; read back, never repeat the invocation.
    try:
        current = _release(reads, tag)
        commit = _tag_commit(reads, tag)
        if current is None or current["draft"] or commit != expected_sha:
            raise ReleaseError("publication outcome unknown; inspect before retry")
    except Exception:
        raise ReleaseError("publication outcome unknown; inspect before retry") from None
    return "verified-public"


def main():
    try:
        repo = os.environ.get("GITHUB_REPOSITORY", "")
        reads = GitHubReads(repo, os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", ""))
        head = subprocess.run(["git", "rev-parse", "HEAD"], check=True,
                              capture_output=True, text=True, timeout=10).stdout.strip()
        result = publish(
            reads=reads,
            mutate=lambda command: subprocess.run(command, check=True, capture_output=True, timeout=90),
            repo=repo, tag=os.environ.get("GITHUB_REF_NAME", ""),
            expected_sha=os.environ.get("GITHUB_SHA", ""), head_sha=head,
            version=Path("VERSION").read_text().strip(),
        )
        print(result)
        return 0
    except ReleaseError as exc:
        print(str(exc), file=sys.stderr)
    except Exception:
        print("publisher failed", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
