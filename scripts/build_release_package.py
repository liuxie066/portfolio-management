"""Build the runtime archive from a committed tree using .gitattributes."""
from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build(root: Path, output_dir: Path, *, ref: str = "HEAD") -> tuple[Path, Path]:
    version = subprocess.check_output(
        ["git", "show", f"{ref}:VERSION"], cwd=root, text=True,
    ).strip()
    if not re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", version):
        raise ValueError("invalid release version")
    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir.resolve() / f"portfolio-management-{version}.tar.gz"
    prefix = f"portfolio-management-{version}/"
    subprocess.run(
        ["git", "archive", "--format=tar.gz", f"--prefix={prefix}",
         f"--output={archive}", ref], cwd=root, check=True,
    )
    with tarfile.open(archive) as source:
        paths = {member.name.removeprefix(prefix) for member in source if member.isfile()}
    required = {
        "pm", "VERSION", "requirements.txt", "config.example.yaml",
        "scripts/install.sh", "scripts/pm.py", "scripts/serve.py",
        "scripts/install_linux.py", "scripts/install_macos.py",
        "scripts/portfolio_scheduled_job.sh", "scripts/macos_preflight.sh",
        "scripts/publish_daily_report.py", "scripts/generate_daily_report_html.py",
        "src/service/http.py", "docs/schema.md",
        "contracts/quality-monitoring/quality_status.v1.schema.json",
    }
    forbidden = {
        "AGENTS.md", "LESSONS.md", "scripts/publish_release.py",
        "scripts/build_release_package.py", "scripts/export_om_openapi.py",
        "scripts/om_api_contract_release.py", "scripts/generate_feishu_schema_docs.py",
    }
    development_prefixes = ("tests/", "docs/gateflow/", "docs/reviews/", "decisions/", ".")
    if not required <= paths or forbidden & paths or any(
        path.startswith(development_prefixes) for path in paths
    ):
        raise ValueError("runtime archive contains development files or lacks required resources")
    checksums = output_dir.resolve() / "SHA256SUMS"
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksums.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    return archive, checksums


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    for path in build(ROOT, args.output_dir):
        print(path)


if __name__ == "__main__":
    main()
