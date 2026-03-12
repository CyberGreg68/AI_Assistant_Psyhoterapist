# Legacy Data Mirror

This `data/` directory is a legacy mirror target. The primary source of truth is `manifests/` and `locales/`.

- Primary manifest: `manifests/manifest.{lang}.jsonc`
- Primary locale phrase files: `locales/{lang}/NN_category.lang.jsonc`
- Legacy sync: `python scripts/sync_scaffold_to_legacy.py`
