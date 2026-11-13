"""Exercise the shipped archive and bootstrap without network or system writes."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import jsonschema
import pytest

from scripts import publish_release as publisher
from scripts.build_release_package import build

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def package(tmp_path_factory):
    directory = tmp_path_factory.mktemp("runtime-package")
    repository = directory / "repository"
    repository.mkdir()
    # Include current edits as well as committed files, without local ignored data.
    tracked = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT,
    ).decode().split("\0")
    for relative in set(tracked) - {""}:
        source = ROOT / relative
        if source.is_file():
            destination = repository / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    (repository / "future-development-file.md").write_text("Not part of the runtime.\n")
    subprocess.run(["git", "init", "--initial-branch=main", str(repository)], check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Package Test", "-c", "user.email=package@example.invalid",
         "-c", "commit.gpgsign=false", "commit", "-m", "Package fixture"],
        cwd=repository, check=True, capture_output=True,
    )
    archive, checksums = build(repository, directory / "dist")
    return repository, archive, checksums


def test_runtime_archive_excludes_development_and_unknown_root_files(package, tmp_path):
    _, archive, checksums = package
    version = (ROOT / "VERSION").read_text().strip()
    assert checksums.read_text() == f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n"
    with tarfile.open(archive) as source:
        source.extractall(tmp_path, filter="data")
        paths = {m.name.split("/", 1)[1] for m in source.getmembers() if m.isfile()}
    assert not any(path.startswith(("tests/", "docs/gateflow/", "docs/reviews/", "decisions/", ".")) for path in paths)
    assert not paths & {"AGENTS.md", "LESSONS.md", "future-development-file.md", "scripts/publish_release.py"}
    runtime = tmp_path / f"portfolio-management-{version}"
    schema = runtime / "contracts/quality-monitoring/quality_status.v1.schema.json"
    jsonschema.Draft202012Validator.check_schema(json.loads(schema.read_text()))
    assert (runtime / "docs/schema.md").is_file()
    assert os.access(runtime / "pm", os.X_OK)
    assert os.access(runtime / "scripts/portfolio_scheduled_job.sh", os.X_OK)
    environment = {**os.environ, "PORTFOLIO_CONFIG_FILE": str(runtime / "config.yaml"),
                   "PM_DATA_DIR": str(tmp_path / "state")}
    for arguments in (["--help"], ["config", "init", "--apply", "--confirm", "--json"]):
        result = subprocess.run(
            [sys.executable, str(runtime / "scripts/pm.py"), *arguments],
            cwd=runtime, env=environment, capture_output=True, text=True, check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    assert (runtime / "config.yaml").read_bytes() == (runtime / "config.example.yaml").read_bytes()


@pytest.fixture
def bootstrap(package, tmp_path):
    _, archive, checksums = package
    tools = tmp_path / "bin"
    tools.mkdir()
    curl = tools / "curl"
    curl.write_text(f"""#!{sys.executable}
import os
from pathlib import Path
import shutil
import sys
args = sys.argv[1:]
with open(os.environ['DOWNLOAD_LOG'], 'a') as log:
    log.write(repr(args) + '\\n')
if '%{{url_effective}}' in args:
    print('https://github.com/example/runtime/releases/tag/v' + os.environ['PACKAGE_VERSION'], end='')
else:
    url = next(arg for arg in args if arg.startswith('https://'))
    source = os.environ['PACKAGE_ARCHIVE'] if url.endswith('.tar.gz') else os.environ['PACKAGE_CHECKSUMS']
    shutil.copyfile(source, args[args.index('-o') + 1])
""")
    curl.chmod(0o755)
    python = tools / "bootstrap-python"
    python.write_text(f"""#!{sys.executable}
import json
import os
from pathlib import Path
import sys
if sys.argv[1:3] == ['-m', 'venv']:
    binary = Path(sys.argv[3]) / 'bin/python'
    binary.parent.mkdir(parents=True, exist_ok=True)
    if not binary.exists():
        binary.symlink_to(Path(__file__).resolve())
elif sys.argv[1:3] == ['-m', 'pip']:
    pass
elif sys.argv[1].endswith('/scripts/install_linux.py'):
    Path(os.environ['ASSET_LOG']).write_text(json.dumps(sys.argv[1:]))
else:
    os.execv(sys.executable, [sys.executable, *sys.argv[1:]])
""")
    python.chmod(0o755)
    environment = {**os.environ, "PATH": str(tools) + os.pathsep + os.environ["PATH"],
                   "PACKAGE_VERSION": (ROOT / "VERSION").read_text().strip(),
                   "PACKAGE_ARCHIVE": str(archive), "PACKAGE_CHECKSUMS": str(checksums),
                   "DOWNLOAD_LOG": str(tmp_path / "downloads"), "ASSET_LOG": str(tmp_path / "assets")}
    for name in list(environment):
        if name.startswith("PORTFOLIO_"):
            environment.pop(name)
    app = tmp_path / "installed"

    def invoke(*extra):
        return subprocess.run(
            ["bash", str(ROOT / "scripts/install.sh"), "--dir", str(app),
             "--python", str(python), *extra], cwd=tmp_path,
            env=environment, capture_output=True, text=True, check=False,
        )
    return invoke, app, environment


def test_default_installer_downloads_verified_runtime_and_preserves_local_files(bootstrap):
    invoke, app, environment = bootstrap
    result = invoke()
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (app / ".git").exists()
    assert not (app / "tests").exists()
    assert not (app / "docs/gateflow").exists()
    assert (app / "scripts/serve.py").is_file()
    assert Path(environment["ASSET_LOG"]).is_file()
    (app / "config.yaml").write_text("operator configuration\n")
    (app / ".data").mkdir()
    (app / ".data/state").write_text("operator state\n")
    result = invoke("--ref", "v" + environment["PACKAGE_VERSION"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert (app / "config.yaml").read_text() == "operator configuration\n"
    assert (app / ".data/state").read_text() == "operator state\n"


def test_checksum_failure_never_installs_or_runs_assets(bootstrap, tmp_path):
    invoke, app, environment = bootstrap
    damaged = tmp_path / "bad-checksums"
    damaged.write_text("0" * 64 + "  " + Path(environment["PACKAGE_ARCHIVE"]).name + "\n")
    environment["PACKAGE_CHECKSUMS"] = str(damaged)
    result = invoke()
    assert result.returncode != 0
    assert "checksum mismatch" in result.stderr
    assert not app.exists()
    assert not Path(environment["ASSET_LOG"]).exists()


def test_runtime_mode_refuses_source_checkout_without_mutation(bootstrap):
    invoke, app, environment = bootstrap
    app.mkdir()
    (app / ".git").mkdir()
    result = invoke()
    assert result.returncode != 0
    assert "--source explicitly" in result.stderr
    assert not Path(environment["DOWNLOAD_LOG"]).exists()


def test_full_source_checkout_requires_explicit_source_option(package, bootstrap):
    repository, _, _ = package
    invoke, app, environment = bootstrap
    result = invoke("--source", "--repo", str(repository), "--ref", "main")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (app / ".git").is_dir()
    assert (app / "tests").is_dir()
    assert not Path(environment["DOWNLOAD_LOG"]).exists()


@pytest.mark.parametrize("remote_assets", [None, [], [{"name": "unexpected", "size": 1}]])
def test_public_release_with_incomplete_assets_is_not_accepted(package, remote_assets):
    _, archive, checksums = package
    sha = "a" * 40
    tag = "v" + (ROOT / "VERSION").read_text().strip()

    class Reads:
        def get(self, path, *, missing=False):
            if path.startswith("/git/ref/"):
                return {"ref": "refs/tags/" + tag, "object": {"type": "commit", "sha": sha}}
            return {"tag_name": tag, "draft": False, "assets": remote_assets}

    with pytest.raises(publisher.ReleaseError, match="release assets"):
        publisher.publish(reads=Reads(), mutate=lambda command: pytest.fail("unexpected mutation"),
                          repo="example/runtime", tag=tag, expected_sha=sha, head_sha=sha,
                          version=tag[1:], assets=(archive, checksums))


def test_publisher_attaches_and_reads_back_exact_built_assets(package):
    _, archive, checksums = package
    assets = (archive, checksums)
    metadata = publisher._asset_metadata(assets)
    sha = "a" * 40
    tag = "v" + (ROOT / "VERSION").read_text().strip()
    calls = []

    class Reads:
        def get(self, path, *, missing=False):
            if path.startswith("/git/ref/"):
                return {"ref": "refs/tags/" + tag, "object": {"type": "commit", "sha": sha}}
            if path.startswith("/releases?"):
                return []
            if not calls:
                return None
            return {"tag_name": tag, "draft": False, "assets": metadata}

    result = publisher.publish(reads=Reads(), mutate=calls.append, repo="example/runtime", tag=tag,
                               expected_sha=sha, head_sha=sha, version=tag[1:], assets=assets)
    assert result == "verified-public"
    assert calls[0][-2:] == [str(archive), str(checksums)]
