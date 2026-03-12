from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from assistant_runtime.config.loader import load_profile_policy_settings
from assistant_runtime.config.loader import load_profile_source_settings
from assistant_runtime.profiles.sync import export_profile_registry
from assistant_runtime.profiles.sync import sync_profile_registry


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a runtime profile registry from the configured source-of-truth snapshots."
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=ROOT,
        help="Project root containing config/ and source snapshot paths.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_dir = args.project_root / "config"
    policy_settings = load_profile_policy_settings(config_dir)
    source_settings = load_profile_source_settings(config_dir)
    registry, report = sync_profile_registry(args.project_root, source_settings, policy_settings)
    output_path = args.project_root / source_settings.export_registry_path
    export_profile_registry(registry, output_path)
    print(
        "Profile registry sync completed: "
        f"patients={report.patients_loaded}/{report.patients_seen}, "
        f"clinicians={report.clinicians_loaded}, assistants={report.assistants_loaded}, "
        f"warnings={len(report.warnings)}"
    )
    for warning in report.warnings:
        print(f"warning: {warning}")
    print(f"Exported registry to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())