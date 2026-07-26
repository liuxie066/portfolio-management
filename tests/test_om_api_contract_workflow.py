from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "om-api-contract-validate.yml"


def test_om_api_contract_workflow_has_no_cross_repo_authority() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "INVESTMENT_CONTRACT_APP_ID" not in text
    assert "INVESTMENT_CONTRACT_APP_PRIVATE_KEY" not in text
    assert "create-github-app-token" not in text
    assert "liuxie066/options-monitor" not in text
    assert "gh pr create" not in text
    assert "git push" not in text


def test_om_api_contract_workflow_only_validates_explicit_releases() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert '      - "pm-api-v*"' in text
    assert "  workflow_dispatch:" in text
    assert "  schedule:" not in text
    assert "om_api_contract_release.py validate" in text
