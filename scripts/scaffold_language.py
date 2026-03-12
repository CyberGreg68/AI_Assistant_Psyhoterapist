from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFESTS_DIR = ROOT / "manifests"
LOCALES_DIR = ROOT / "locales"

SHORT_LABELS = {
    "crisis": "cri",
    "boundary": "bd",
    "structure": "str",
    "empathy": "emp",
    "open_questions": "oq",
    "closed_questions": "cq",
    "variants": "var",
    "cbt_mi_dbt": "cbt",
    "psychoeducation": "edu",
    "encouragement": "enc",
    "closing": "clo",
    "cultural": "cult",
}


def load_jsonc(path: Path) -> dict:
    raw_text = path.read_text(encoding="utf-8")
    if raw_text.lstrip().startswith("/*"):
        comment_end = raw_text.find("*/")
        raw_text = raw_text[comment_end + 2 :].lstrip()
    return json.loads(raw_text)


def write_jsonc(path: Path, payload: object) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(text + "\n", encoding="utf-8")


def ensure_text(path: Path, text: str) -> None:
    if not path.exists():
        path.write_text(text, encoding="utf-8")


def scaffold_language(target_lang: str, source_lang: str = "hu") -> None:
    source_manifest_path = MANIFESTS_DIR / f"manifest.{source_lang}.jsonc"
    source_manifest = load_jsonc(source_manifest_path)

    target_manifest = dict(source_manifest)
    target_manifest["lang"] = target_lang
    target_manifest["generated_at"] = "TBD"
    target_manifest["derivation_notes"] = (
        "Skeleton-only manifest. Keep category order, filenames, and IDs aligned across active languages. "
        "Phrase content must be added in lockstep for all enabled locales."
    )

    target_locale_dir = LOCALES_DIR / target_lang
    target_phrase_dir = target_locale_dir / "phrases"
    target_trigger_dir = target_locale_dir / "triggers"
    target_rules_dir = target_locale_dir / "rules"
    target_mappings_dir = target_locale_dir / "mappings"
    target_phrase_dir.mkdir(parents=True, exist_ok=True)
    target_trigger_dir.mkdir(parents=True, exist_ok=True)
    target_rules_dir.mkdir(parents=True, exist_ok=True)
    target_mappings_dir.mkdir(parents=True, exist_ok=True)

    ensure_text(
        target_trigger_dir / "README.txt",
        f"Place {target_lang} patient-side trigger files here using the NN_short_triggers.{target_lang}.json convention.\n",
    )
    ensure_text(
        target_rules_dir / "README.txt",
        f"Reserve this folder for {target_lang} locale-specific runtime rules, policy fragments, and override files.\n",
    )
    ensure_text(
        target_mappings_dir / "README.txt",
        f"Reserve this folder for {target_lang} locale-specific mappings such as tag normalization, alias tables, or lookup resources.\n",
    )

    for category in target_manifest["category_order"]:
        short = SHORT_LABELS[category["name"]]
        category["filename"] = f"phrases/{category['prefix']}_{short}_phrases.{target_lang}.jsonc"
        category_path = target_locale_dir / category["filename"]
        if not category_path.exists():
            write_jsonc(category_path, [])

    target_manifest_path = MANIFESTS_DIR / f"manifest.{target_lang}.jsonc"
    write_jsonc(target_manifest_path, target_manifest)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("langs", nargs="+")
    parser.add_argument("--source-lang", default="hu")
    args = parser.parse_args()
    for lang in args.langs:
        scaffold_language(lang, source_lang=args.source_lang)
    print(f"Scaffolded languages: {', '.join(args.langs)} from {args.source_lang}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
