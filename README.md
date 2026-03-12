# AI Assistant Psychotherapist Scaffold

This scaffold provides a manifest-driven, phrase-based runtime for localized, safety-focused response selection.

The primary source of truth is the `manifests/` and `locales/` directories. The `data/` directory is optional and reserved for legacy mirror exports.

Review alatt allo es jovahagyott elemek tarolhatok ugyanabban a locale fajlban, ha a tartalom `meta.status` es `meta.enabled_in` mezoi egyertelmuen jelzik, hogy mi mehet az elo runtime-ba es mi marad csak review vagy teszt csatornan. A runtime alapbeallitasa csak az `appr` + `rt` elemeket engedi at.

## Directory Structure

- `locales/{lang}/phrases`: language-specific phrase files using the `NN_short_phrases.lang.jsonc` pattern
- `locales/{lang}/triggers`: patient-side trigger files using the `NN_short_triggers.lang.json` pattern
- `locales/{lang}/rules`: reserved for locale-specific runtime rules and overrides
- `locales/{lang}/mappings`: reserved for locale-specific normalization and lookup resources
- `manifests`: language manifests and JSON schemas
- `src/assistant_runtime`: runtime modules
- `scripts`: validation, migration, and governance scripts
- `tests`: unit and integration tests

## Runtime Modules

- `manifest_loader`: loads manifests and localized phrase files
- `selection_engine`: handles intent, tags, priority, and crisis-first selection
- `model_router`: chooses local vs online model mode per pipeline stage
- `latency_masking`: selects short filler phrases to hide local or network delay
- `access_governance`: validates role/channel access and audit expectations
- `adapters/stt_adapter`: mock, text-passthrough, and generic HTTP STT adapters
- `adapters/factory`: builds the configured STT adapter from runtime and routing settings
- `adapters/handoff_client`: generic HTTP crisis handoff client
- `profiles/registry`: patient, clinician, and assistant profile loading and context summarization
- `routing/contact_router`: after-hours assistant-first routing and escalation plan construction
- `runtime_service`: end-to-end text or audio processing with optional handoff

## Package Boundaries

The codebase now has an explicit namespace split so the local developer and ingest surface can be separated from the live patient runtime later without breaking this workspace setup.

- `assistant_runtime.core`: shared domain concepts and future home for logic that both live and ops need
- `assistant_runtime.live`: patient-facing runtime, session/auth, and HTTP-facing live helpers
- `assistant_runtime.ops`: ingest, review, reporting, and operations snapshots

For backward compatibility, the original top-level modules still work as shim imports. Existing tests, scripts, and local workflows can keep running while new code moves to the namespaced paths.

The shared runtime bundle loader now lives under `assistant_runtime.core.runtime_bundle`, and the legacy `assistant_runtime.manifest_loader` path remains as a shim.

Shared policy and config snapshot helpers that both live and ops can use now also live under `assistant_runtime.core`, including `assistant_runtime.core.access_governance` and `assistant_runtime.core.operations_snapshot`.

## Quick Start

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e .[dev]
python scripts\validate_schema.py
python scripts\check_manifest_consistency.py --lang hu
python scripts\check_locale_alignment.py --langs hu en de
python scripts\check_legacy_data_empty.py
python scripts\clinical_review_hook.py locales\hu\phrases\01_cri_phrases.hu.jsonc
pytest
```

## CI Focus

- schema validation
- manifest consistency check
- locale alignment check
- legacy data guard
- sensitive diff review check
- clinical review hook

## Profile and Contact Model

- `config/profile_policies.json`: patient history usage, active languages, and after-hours routing defaults
- `config/contact_channels.json`: allowed patient, assistant, clinician, and emergency contact channels
- `config/role_channel_matrix.json`: role-specific ingress, primary, and fallback channels for patient, operator, and clinical lead access
- `config/model_routing.json`: local-first vs online fallback routing per pipeline stage
- `config/latency_masking.json`: short Hungarian filler phrases and pause budgets for hiding local or network latency
- `config/access_governance.json`: auth, audit, and escalation requirements by patient, operator, and clinical lead role
- `config/profile_sources.json`: source-of-truth provider config for syncing runtime registries from upstream snapshots
- `config/profile_registry.example.jsonc`: example registry shape for patients, clinicians, and assistants

The runtime profile registry can be generated from upstream snapshots instead of being maintained by hand:

```powershell
python scripts\sync_profile_registry.py
```

The default scaffold expects snapshot exports at:

- `data_sources/patients.snapshot.json`
- `data_sources/clinicians.snapshot.json`
- `data_sources/assistants.snapshot.json`
- `data_sources/assignments.snapshot.json`
- `data_sources/patient_history.snapshot.json`

The generated output is written to `config/profile_registry.generated.jsonc`. The existing example registry remains useful as a fallback fixture and for manual testing.

The intended flow is:

1. Identify the patient profile from the incoming channel or session.
2. Prefill basic demographics and, if allowed, a prior history summary.
3. Resolve the assigned clinician.
4. Route after-hours contact to the assistant first, then escalate to the clinician only when severity or policy requires it.
5. Refresh the runtime registry from the system-of-record snapshots on a schedule or before deployment.

## Access Channels

- Patient ingress should stay on `web_chat`, `voice`, and `secure_messaging`, with secure follow-up for sensitive content.
- Responsible operator access should go through `admin_console` or `secure_email`, with MFA and audit logging.
- Clinical lead or psychotherapy supervisor access should stay on `clinical_console`, `secure_chat`, or `secure_email`, with named accounts and secure clinical context only.
- Emergency paths should bypass async queues and use direct phone or local emergency routing.

The detailed auth, audit, and escalation expectations for these roles now live in `config/access_governance.json`.

## Model Routing

Use `config/model_routing.json` to separate local and online model responsibilities:

- STT: prefer local speech recognition, fall back to online when device noise or CPU load is high.
- Intent and risk: keep fast local heuristics first, escalate to online triage only on low confidence or novel patterns.
- Phrase selection: keep local candidates authoritative, and optionally let an online model rerank those candidates using recent conversation context.
- Generative fallback: prefer online for richer expansion, but allow local fallback where privacy or network availability requires it.
- TTS: prefer local voice output, with online fallback when higher quality or special voice requirements matter.

## Latency Masking

Use `config/latency_masking.json` for short pacing phrases that buy time without sounding evasive. The current Hungarian set is tuned for:

- quick acknowledgement before compute,
- brief safety-check pauses,
- slow local hardware bridges,
- network-delay bridges.

Keep fillers short, neutral, and clinically safe. Avoid overusing them or stacking multiple fillers before substantive content.

## Operations Design

If you want to review the operational design in one place, see `docs/operations_design.md` for:

- patient vs operator vs clinical lead access model,
- local and online model routing through the pipeline,
- latency-masking strategy and safe Hungarian filler usage.

## HU Style Metadata

Backfill missing Hungarian style metadata across all phrase files:

```powershell
python scripts\backfill_hu_style_metadata.py
```

Report current Hungarian phrase and trigger metadata coverage:

```powershell
python scripts\report_hu_style_coverage.py
```

Summarize the current operations-facing channel, routing, and latency policy as JSON:

```powershell
python scripts\report_operations_matrix.py
```

Try the current runtime manually against text or a passthrough transcript file:

```powershell
python scripts\run_runtime_demo.py --text "Szorongok, mit tegyek?"
python scripts\run_runtime_demo.py --audio-path .\sample_transcript.txt --latency-context acknowledge_then_compute --latency-elapsed-ms 250
python scripts\run_runtime_demo.py --text "Egyszerűbben mondd el." --prefer-online --active-condition device_cpu_overloaded
```

The demo output now includes stage-level route decisions for STT, intent/risk, phrase selection, and TTS, plus an optional latency preamble. It also includes `hybrid_selection` and `conversation_summary` fields so you can see when recent history influenced phrase choice. If `config/runtime.json` enables generative fallback, the same output will also contain a `generation_request` block when deterministic phrase selection has no candidate.

## Generative Fallback

The runtime can call an OpenAI-compatible chat-completions endpoint for the `generative_fallback` stage.

- Configure the endpoint in `config/endpoints.json` under `llm`.
- Enable fallback in `config/runtime.json` by setting `generative_fallback_enabled` to `true`.
- Set the bearer token in the environment variable named by `auth_env_var`.
- Use `provider: github_models` if this project should treat the endpoint as a GitHub Models backend.

This is suitable for GitHub Models or Azure OpenAI style endpoints. The scaffold does not connect to the internal VS Code GitHub Copilot chat session directly.

Example PowerShell setup:

```powershell
$env:LLM_API_TOKEN = "your-token"
python scripts\run_runtime_demo.py --text "Nem tudom, hogyan kezdjem elmondani." --prefer-online
```

You can also place local secrets in `.env.local` at the workspace root, for example:

```text
LLM_API_TOKEN=your-token
```

The helper scripts and admin API will load `.env.local` automatically if the variable is not already present in the shell.

The routing config now uses concrete GitHub Models ids for the online text stages, and the `llm` endpoint can define a `default_model` plus alias mappings.

If you want to test against a different GitHub Models model id temporarily without editing config files, set a temporary override:

```powershell
$env:LLM_MODEL_OVERRIDE = "gpt-4o-mini"
```

You can also test the endpoint directly before wiring it into full runtime behavior:

```powershell
$env:LLM_API_TOKEN = "your-token"
.venv\Scripts\python.exe scripts\test_llm_connection.py
```

Compare multiple GitHub Models models against the same prompt:

```powershell
.venv\Scripts\python.exe scripts\compare_llm_models.py --model gpt-4o-mini --model gpt-5-mini --prompt "Adj rovid, biztonsagos magyar valaszt szorongasra."
```

For GitHub Models, use a GitHub personal access token with `models:read` permission. The VS Code Copilot sign-in alone is not enough for the Python process.

## Admin API

Start a thin local admin API for runtime testing and operations introspection:

```powershell
python scripts\serve_admin_api.py --host 127.0.0.1 --port 8787
```

The patient portal now enforces the `session_token` access policy from `config/access_governance.json`.

## Published Runtime Bundle

You can now publish a versioned runtime bundle artifact from the current source tree:

```powershell
python scripts\publish_runtime_bundle.py --lang hu --output .\data\runtime_state\published
```

This produces a single JSON artifact containing the manifest, phrase categories, trigger groups, and current knowledge snippets for the selected language.

The live runtime can keep using the source tree by default, or you can opt into the published artifact by either:

- setting `published_bundle_path` in `config/runtime.json`, or
- setting `RUNTIME_BUNDLE_PATH` in the environment for a temporary override.

If no published bundle path is configured, the runtime still loads directly from `manifests/` and `locales/`, so existing local development stays unchanged.

- Set `PATIENT_PORTAL_ACCESS_CODE` to require a custom local access code.
- Optionally set `PATIENT_PORTAL_SESSION_SECRET` for stable signed cookies across restarts.
- Optionally set `PATIENT_PORTAL_SESSION_TTL_SECONDS` to change the session lifetime.
- If no access code is configured, the local demo falls back to `local-demo` and prints that in the server console.

Browser patient demo:

```text
http://127.0.0.1:8787/chat
```

Login page:

```text
http://127.0.0.1:8787/login
```

The browser demos now also support:

- microphone capture via the browser speech-recognition API when available,
- multipart audio upload to the backend runtime when the browser supports `MediaRecorder`,
- local spoken playback of assistant replies via backend-generated `.wav` files, with browser speech synthesis as fallback,
- explicit logout from the protected patient portal session.

For local backend TTS, the runtime now uses a Windows PowerShell `System.Speech` path by default, so the first implementation stays local-only and does not require an external subscription.

If you want an online TTS fallback for the `tts` routing stage, configure `config/endpoints.json` under `tts` and set `config/runtime.json` `tts_provider` to `http`, or keep `powershell` and let the router switch online when `prefer_online` or the configured `tts` trigger conditions apply.

- `tts.url`: HTTP endpoint returning either raw audio bytes or JSON with `audio_base64`
- `tts.api_format`: `raw_audio` or `json_audio_base64`
- `tts.auth_env_var`: bearer token env var for the remote TTS provider
- `tts.voice`: optional remote voice identifier passed through in the JSON request

The browser demo now resolves patient memory in this order:

- `patient_id` when an identified patient is known in the profile registry
- `patient_identity.browser_patient_key` for stable pseudonymous browser sessions
- `conversation_id` only as a last fallback

Persistent conversation memory is stored in `data/runtime_state/conversation_memory.json`. The runtime stores short excerpts when consent is present for browser sessions, and otherwise still keeps patient-level item history for repeat avoidance.

Tamper-evident audit logs are stored under `data/runtime_state/audit/` as hash-chained JSONL streams:

- `conversation.jsonl`: patient-facing turn processing, selected content ids, risk flags, and handoff events
- `content.jsonl`: ingest pack generation and content workflow events such as insert, modify, review, or approval receipts

If `AUDIT_LOG_SECRET` is set in the environment, each audit row also gets an HMAC signature in addition to the hash chain.

Available routes:

- `GET /`
- `GET /chat`
- `GET /login`
- `GET /health`
- `GET /operations`
- `POST /auth/session`
- `POST /auth/logout`
- `POST /runtime/text`
- `POST /runtime/audio`
- `POST /runtime/audio-upload`

## Local External Knowledge Ingest

You can now build a review pack from local professional material such as `.txt`, `.md`, `.html`, `.json`, `.jsonl`, `.csv`, or `.tsv` files.

Example:

```powershell
python scripts\build_external_knowledge_pack.py --pack-id local_lit_001 --source .\data_sources\articles --output .\data\runtime_state\review\local_lit_001.json
```

The generated pack:

- extracts normalized knowledge snippets from local files only,
- marks them as `meta.src = "lit"`, `meta.status = "rev"`, `meta.enabled_in = ["rv", "tst"]`,
- writes an audit event to `data/runtime_state/audit/content.jsonl`.

This is the local-first ingest path. A future remote downloader can feed this same review-pack format later without changing the runtime gating model.

## Remote Knowledge Download

You can also download curated remote sources into the same review-pack format:

```powershell
python scripts\build_remote_knowledge_pack.py --pack-id remote_lit_001 --url https://example.org/article --output .\data\runtime_state\review\remote_lit_001.json
```

Optional auth for protected sources:

- set `REMOTE_SOURCE_TOKEN` for bearer-token downloads,
- set `REMOTE_SOURCE_BASIC_AUTH` for pre-encoded `Basic ...` credentials,
- set `REMOTE_SOURCE_HEADERS_JSON` to a JSON object of extra request headers,
- set `REMOTE_SOURCE_COOKIE` to replay an already-issued session cookie,
- or pass repeated `--header "Name: Value"` and `--cookie "name=value"` flags to the CLI.

The local admin server now parses multipart uploads without Python `cgi`, so the `/runtime/audio-upload` contract stays the same without relying on a deprecated stdlib path.

Example text request:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8787/runtime/text -ContentType 'application/json' -Body '{"text":"Szorongok, mit tegyek?","latency_context":"acknowledge_then_compute","latency_elapsed_ms":200}'
```

Example text request with backend TTS:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8787/runtime/text -ContentType 'application/json' -Body '{"text":"Szorongok, mit tegyek?","synthesize_speech":true}'
```

If you add `"debug": true`, the response now includes the recent turn list, a short conversation summary, and the hybrid reranking decision for browser-demo inspection.

The conversation summary payload now also exposes:

- `recent_turns`: the condensed last turns for UI and debug use
- `active_summary`: patient themes, assistant item history, recent categories, and trigger ids

There is also a mobile demo view for customer walkthroughs:

```text
http://127.0.0.1:8787/chat/mobile
```

Local knowledge snippets can ground online reranking and generative fallback. The current Hungarian seed set lives in:

- `locales/hu/mappings/knowledge_snippets.hu.json`

To build a clinician-profile ingest review pack from summaries, transcripts, and audio references:

```powershell
python scripts\build_profile_ingest_pack.py --profile-id therapist_a --summary .\summary.md --transcript .\transcript.txt --audio .\session.wav --output .\data_sources\ingest\therapist_a.review_pack.json
```

For the ingest and voice-cloning workflow, see `docs/profile_ingest_and_voice_pipeline.md`.

## Language Scaffolding

Create empty locale skeletons from the Hungarian manifest structure:

```powershell
python scripts\scaffold_language.py en de
```

This creates:

- `manifests/manifest.en.jsonc`
- `manifests/manifest.de.jsonc`
- `locales/en/phrases/*.en.jsonc`
- `locales/de/phrases/*.de.jsonc`
- `locales/en/triggers`, `locales/en/rules`, `locales/en/mappings`
- `locales/de/triggers`, `locales/de/rules`, `locales/de/mappings`

These files are intentionally content-free. If HU, EN, and DE stay active together, phrase IDs and category order should remain aligned so the same phrase slot means the same concept across languages.

## Review Metadata Backfill

Populate review metadata for clinically sensitive Hungarian phrase files:

```powershell
python scripts\backfill_review_metadata.py
```

## Legacy sync

If you need to export back to the legacy `data/phrases` structure, use:

```powershell
python scripts\sync_scaffold_to_legacy.py
```
