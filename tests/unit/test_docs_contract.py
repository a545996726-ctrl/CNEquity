"""Small checks that keep the public docs aligned with shipped CLI behavior."""

from pathlib import Path

from click.testing import CliRunner

from cnequity.cli.main import cli

ROOT = Path(__file__).resolve().parents[2]


def test_cli_reference_covers_the_research_demo_flag():
    help_result = CliRunner().invoke(cli, ["demo", "--help"])
    assert help_result.exit_code == 0
    reference = (ROOT / "docs" / "reference" / "cli.md").read_text(encoding="utf-8")
    assert "`--research`" in reference
    assert "raw / hfq" in reference
    assert "--research" in help_result.output


def test_source_health_note_does_not_describe_removed_eastmoney_sticky_state():
    from cnequity.diagnostics.source_health import PROBES_BY_KEY

    note = PROBES_BY_KEY["eastmoney_push2his"].note
    assert "sticky" not in note.lower()
    assert "proxy" in note


def test_the_declared_version_is_the_packaged_version():
    """__version__ is hand-written, so nothing but a test keeps it honest.

    A release bumps pyproject and can silently leave the module behind; the
    wheel then reports one version and `cne --version` another.
    """
    import tomllib

    from cnequity import __version__

    with (ROOT / "pyproject.toml").open("rb") as fh:
        declared = tomllib.load(fh)["project"]["version"]
    assert __version__ == declared, (
        f"src/cnequity/__init__.py says {__version__}, pyproject.toml says {declared}"
    )


def test_the_release_contract_for_this_version_exists():
    """The release workflow fails on a missing contract; fail here instead."""
    import tomllib

    with (ROOT / "pyproject.toml").open("rb") as fh:
        version = tomllib.load(fh)["project"]["version"]
    assert (ROOT / "contracts" / f"v{version}.json").is_file(), (
        f"contracts/v{version}.json is missing; run `cne contract show > contracts/v{version}.json`"
    )


def test_citation_metadata_tracks_the_current_package_version():
    from cnequity import __version__

    citation = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
    assert f"version: {__version__}" in citation
    assert "license: Apache-2.0" in citation
    assert 'repository-code: "https://github.com/rootSunc/CNEquity"' in citation
