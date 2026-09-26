"""Keep the repository example generated from the packaged config template."""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    source = root / "src/cnequity/config/templates/cnequity.example.toml"
    mirror = root / "configs/cnequity.example.toml"
    expected = source.read_bytes()
    if args.check:
        if not mirror.is_file() or mirror.read_bytes() != expected:
            print("Config examples differ; run python scripts/sync_config_templates.py")
            return 1
        print("Config examples in sync")
        return 0
    mirror.write_bytes(expected)
    print(f"Updated {mirror}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
