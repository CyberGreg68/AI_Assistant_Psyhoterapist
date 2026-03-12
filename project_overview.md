# Project Overview — Phrased Response System (English)

## Summary
A phrase‑based response system for therapeutic-style chat and speech interactions. The system uses localized manifests and categorized phrase libraries (300–1,000 phrases per language) to deliver safe, low‑cost, fast replies. It supports offline, hybrid and online pipelines, enforces safety and boundary rules (crisis hard handoff, referral, confidentiality), and includes cultural localization. This document explains architecture, selection logic, cost model, risk mitigation and rollout plan.

## Goals
- Provide safe, consistent, and low‑latency responses in therapeutic support contexts.
- Minimize per‑interaction cost by preferring short, pre‑approved phrases and local selection.
- Ensure legal and clinical safety via boundary texts, crisis detection and human handoff.
- Support iterative growth of phrase inventory and continuous clinical review.

## Core Components
- **Localized manifest**: `manifests/manifest.{lang}.jsonc` — category order, filenames, code keys, selection rules, derivation notes.
- **Category phrase files**: `locales/{lang}/phrases/NN_short_phrases.lang.jsonc` — canonical phrase + paraphrases and metadata (`id, pri, rec, use, tags, pp`).
- **Trigger files**: `locales/{lang}/triggers/NN_short_triggers.lang.json` — patient-side trigger patterns and candidate phrase links.
- **Rules and mappings**: `locales/{lang}/rules/` and `locales/{lang}/mappings/` — reserved for locale-specific policy fragments and lookup resources.
- **Patient profile registry**: patient demographics, preferred language, consent, assigned clinician, and history policy.
- **Clinician and assistant registry**: practice staffing, channels, after-hours coverage, and escalation targets.
- **Profile source sync**: upstream EHR/CRM/export snapshots are normalized into a generated runtime registry.
- **Variants**: short acknowledgements and grounding fragments for token and TTS savings.
- **Cultural file**: localization notes, alternative phrasing, forbidden/avoid list.
- **Selection engine**: intent + tags + priority + safety rules → phrase selection.
- **STT/Chat analyzer**: transcribe (if STT), extract gist, detect intent, sentiment, risk flags.
- **Cache and local store**: session cache of frequently used phrases to avoid LLM calls.
- **Human handoff endpoint**: secure channel to notify clinician or emergency services.

## Category Derivation and Rationale
Categories derive from clinical practice and safety principles:
- **Crisis**: immediate safety, hard handoff. Source: standard crisis protocols and "safety first" principle.
- **Boundary**: legal/ethical limits (no medical/juridical advice), referral flow.
- **Structure**: session agenda, consent, time checks.
- **Empathy**: client‑centered validation (Rogers‑style).
- **Open/Closed Questions**: exploratory vs factual checks.
- **Variants**: runtime token optimization.
- **CBT/MI/DBT**: therapeutic technique prompts (skaling, cognitive reframing, distress tolerance).
- **Psychoeducation**: short evidence‑based tips.
- **Encouragement**: motivational reinforcement.
- **Closing**: summary and next steps.
- **Cultural**: localization and sensitivity guidance.

## Selection Rules (high level)
1. **Crisis check first**: any risk flag or `cri` tag → immediate crisis category and hard handoff.
2. **Category priority**: follow manifest `category_order` (prefix numeric order).
3. **Tag matching**: within category prefer items whose `tags` best match intent and detected tone.
4. **Rec/tone matching**: prefer items with `rec` matching desired tone (warm/neutral/brief).
5. **Variants preference**: for acknowledgements and short replies, prefer `variants` to save tokens and TTS cost.
6. **Per‑item override**: `pri` can override category default only if explicitly set.

## Phrase Inventory Strategy
- **Phase 1 (MVP)**: ~300 core phrases per language covering crisis, boundary, empathy, structure, top open/closed questions, and variants.
- **Phase 2**: expand to 600 phrases based on usage logs and clinician feedback.
- **Phase 3**: target 1,000+ phrases with cultural variants and context‑specific paraphrases.
- **Metadata**: every phrase includes `id, pri, rec, use, tags, pp` and cultural notes.
- **Governance**: clinical review workflow for any phrase that is boundary/crisis/therapeutic.
- **Cross-language alignment**: active locales should keep matching category order and phrase IDs so the same slot maps to the same phrase concept across HU, EN, and DE.

## Gist Extraction and Use
- **Gist**: 1–2 sentence canonical summary of user utterance (no PII unless required for safety).
- **Use cases**: empathy + open question templates, follow‑up prompts, selection context.
- **Storage**: ephemeral per session; persisted only if clinically necessary and with consent.
- **Profile enrichment**: patient basics can be auto-prefilled from the profile registry, and prior summary context can be used only if the patient policy allows it.
- **Source-of-truth boundary**: patient, clinician, assignment, and history records should originate from an upstream system export and be transformed into the runtime registry through `scripts/sync_profile_registry.py`.

## Pipeline Options
### Offline
- **Flow**: batch STT → NLP extraction → human review → phrase updates.
- **Use**: training, auditing, phrase curation.
- **Pros**: low cost, auditable.
- **Cons**: not real‑time.

### Hybrid
- **Flow**: local manifest + cache for real‑time selection; background offline model retraining and phrase proposals.
- **Use**: production MVP with continuous improvement.
- **Pros**: low latency, lower LLM usage.
- **Cons**: synchronization complexity.

### Online
- **Flow**: STT → cloud NLP/LLM selection/generation → response.
- **Use**: dynamic, context‑rich responses.
- **Pros**: most flexible.
- **Cons**: highest cost and data governance needs.

## Safety, Legal and Clinical Controls
- **Crisis hard handoff**: automatic escalation and clinician notification when risk detected.
- **Boundary enforcement**: explicit phrases that state limits (no medical/juridical advice).
- **Referral flow**: pre‑approved referral phrases and clinician contact templates.
- **Data protection**: encryption in transit and at rest; retention policy; consent capture.
- **Clinical governance**: mandatory clinician sign‑off for therapeutic and crisis phrases; periodic audits.
- **Legacy data guard**: primary phrase content must stay in `locales/` and `manifests/`; `data/phrases/` is export-only.
- **Assistant-first after-hours routing**: patient support should reach an assistant or triage role first, with clinician escalation driven by severity and policy.

## Cost Model and Example Calculations
**Assumptions and variables**
- `S` = STT cost per minute (USD/min)
- `T` = TTS cost per character (USD/char)
- `L_in` = average LLM input tokens per session
- `L_out` = average LLM output tokens per session
- `C_token` = LLM cost per token (USD/token) or cost per 1k tokens
- `R` = average session length in minutes
- `U` = number of sessions per month

**Example baseline parameters (configurable)**
- `S = $0.03 / min` (mid-range STT)
- `T = $0.00010 / char` (≈ $0.10 / 1k chars)
- `L_in = 3,000 tokens` (transcript + context)
- `L_out = 300 tokens` (phrase selection or short generative output)
- `C_token = $0.00002 / token` (example; model dependent)
- `R = 10 min` per session
- `U = 10,000 sessions / month`

**Per session cost (online full LLM)**
- STT cost = `S * R` = $0.03 * 10 = **$0.30**
- LLM cost = `(L_in + L_out) * C_token` = 3,300 * $0.00002 = **$0.066**
- TTS cost = average 1,200 chars * T = 1,200 * $0.00010 = **$0.12**
- **Total per session ≈ $0.486**

**Monthly cost (10k sessions)**
- ≈ $4,860 (plus infra and monitoring)

**Hybrid scenario (manifest + cache reduces LLM calls)**
- Assume 70% of replies served from phrase library (no LLM), 30% require LLM.
- STT still required for transcript: STT cost unchanged.
- LLM cost reduced to 30% of previous: 0.3 * $0.066 = $0.0198
- TTS cost reduced if variants used (shorter outputs). If average chars drop to 400 chars: TTS = 400 * $0.00010 = $0.04
- **Total per session ≈ $0.30 + $0.0198 + $0.04 = $0.3598**
- **Monthly (10k sessions) ≈ $3,598** → ~26% savings vs full online.

**Token and character sensitivity**
- Replacing generative outputs with pre‑approved short phrases reduces both LLM tokens and TTS characters.
- Example: replacing a 300‑token generative reply (~1,800 chars) with a 20‑word phrase (~120 chars) reduces TTS cost by ~93% for that reply and LLM output tokens to near zero.

**Operational costs**
- Hosting, monitoring, clinician on‑call, and compliance add fixed monthly costs (estimate: $2k–$10k depending on scale and region).

**Key levers to reduce cost**
- Increase phrase coverage (serve more replies from library).
- Use variants for acknowledgements and grounding.
- Cache per session and per clinician templates.
- Batch offline updates rather than frequent online LLM calls.

## Metrics and Monitoring
- **Safety metrics**: crisis triggers, false positives/negatives, handoff latency.
- **Cost metrics**: STT minutes, LLM tokens, TTS characters, % replies served from phrase library.
- **Quality metrics**: clinician review scores, user satisfaction, response latency.
- **Operational routing metrics**: assistant response time, clinician escalation rate, unresolved after-hours contacts.

## Roadmap (high level)
1. Build localized manifest and 300 phrase inventory per language (MVP).
2. Implement hybrid runtime with local cache and manifest loader.
3. Deploy STT adapter and basic selection engine; integrate crisis detector and handoff.
4. Run pilot with clinician oversight; collect usage and safety metrics.
5. Expand phrase inventory to 1,000+ and add cultural variants; automate offline suggestion pipeline.
6. Scale to more languages and integrate advanced analytics.

## Multi-language Scaffolding
- New locales can be scaffolded from the current manifest structure with `scripts/scaffold_language.py`.
- New locale scaffolds should start empty and be populated only in synchronized language batches.
- Clinically sensitive categories can be backfilled with review metadata using `scripts/backfill_review_metadata.py`.
- Locale alignment can be checked with `scripts/check_locale_alignment.py`.

## Profile Source Integration
- The default provider scaffold is `json_snapshot`, which can represent scheduled exports from an EHR, CRM, or practice-management platform.
- The sync layer merges five upstream record sets: patients, clinicians, assistants, patient-to-clinician assignments, and optional patient-history summaries.
- History context remains gated by consent flags and local policy, even if the source system exports more data.
- Patients without required clinician assignments can be excluded during sync so after-hours routing never operates on ambiguous ownership.

## Deliverables
- `manifests/manifest.hu.jsonc` and phrase files under `locales/{lang}/phrases/`.
- `07_var_phrases.hu.jsonc` and `12_cult_phrases.hu.jsonc`.
- Selection engine pseudocode and CI schema checks.
- Cost model spreadsheet (parameterized).
- VSCode agent prompt for scaffold generation.

