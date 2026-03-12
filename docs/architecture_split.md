# Split Architecture Plan

## Goal

Keep the current local workspace fully usable for development and testing, while making it easy to separate the live patient-facing runtime from offline ingest, review, and operator workflows later.

## Target Layers

### Core

Shared domain logic and artifact formats that both runtime and ops can depend on.

Examples:

- manifests and locale content
- schemas and config models
- selection and trigger logic
- policy and routing rules
- serialization and shared utility code

### Live

Everything needed to serve an active patient interaction.

Examples:

- live runtime orchestration
- session memory and portal auth
- patient HTTP surface and UI helpers
- live STT, TTS, LLM, and handoff routing

### Ops

Everything needed for content ingest, review, reporting, and offline preparation.

Examples:

- local and remote knowledge ingest
- review pack generation
- operations matrix reporting
- content validation and registry sync workflows

## Current Safe Refactor

This repository now exposes:

- `assistant_runtime.live.*`
- `assistant_runtime.ops.*`
- compatibility shims on the legacy top-level module paths

That means the current workspace can still run and test exactly as before, but new code can already use the separated namespaces.

## Practical Deployment Boundary

The intended future boundary is:

1. Ops environment publishes approved runtime artifacts.
2. Live environment consumes only published artifacts.
3. Live environment does not need direct ingest, review, or remote source download capabilities.
4. Ops environment does not need direct patient-session serving.

The current published artifact model is a runtime bundle JSON containing manifest data, phrase categories, trigger groups, and knowledge snippets. The live runtime can already load this bundle through `published_bundle_path` or `RUNTIME_BUNDLE_PATH`.

## Suggested Next Moves

1. Move more shared logic under `assistant_runtime.core` and make `live` and `ops` depend on it explicitly.
2. Add import-boundary checks so `live` cannot import `ops` ingest code except through published artifacts.
3. Split config and secrets so the live deployment never sees ingest credentials.
4. Introduce a build step that emits a versioned runtime bundle from approved content.