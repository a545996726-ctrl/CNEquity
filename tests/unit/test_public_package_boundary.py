"""Local notes and validation reports must not enter release archives."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "check_public_package", ROOT / "scripts" / "check_public_package.py"
)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


@pytest.mark.parametrize("prefix", ["", "cnequity-0.12.0/"])
@pytest.mark.parametrize("directory", ["private", "evidence"])
def test_release_members_reject_local_materials(prefix, directory):
    local = f"{prefix}{directory}/run-report.json"
    template = f"{prefix}{checker.TEMPLATE}"
    assert checker.check_members([template, local]) == [local]


def test_release_members_allow_public_contracts_and_tests():
    members = [
        checker.TEMPLATE,
        "contracts/v0.12.0.dev0.json",
        "tests/fixtures/example.json",
        "docs/getting-started/quickstart.md",
    ]
    assert checker.check_members(members) == []
