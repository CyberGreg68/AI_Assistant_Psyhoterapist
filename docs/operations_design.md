# Operations Design

## Roles And Channels

Patient:
Use `web_chat`, `voice`, and `secure_messaging` as the supported ingress set. Keep the initial barrier low, but move any clinically sensitive follow-up to secure channels.

Responsible operator:
Use `admin_console` and `secure_email` as primary channels, with `ops_phone` only as fallback. Require MFA, named accounts, and auditable change logs.

Clinical lead / psychotherapy supervisor:
Use `clinical_console`, `secure_chat`, and `secure_email` as primary channels. Keep emergency phone access as fallback only. Require named accounts, MFA, and clinical-role authorization.

## Model Pipeline

STT:
Prefer local-first for privacy and lower recurring cost. Fall back to online only when device load, noise, or quality thresholds justify it.

Intent and risk:
Keep deterministic local logic first. Escalate to online triage only when confidence is low or the utterance pattern is out-of-distribution.

Phrase selection:
Keep local and manifest-driven.

Generative fallback:
Use online safe-response models when phrase inventory is insufficient, but allow local fallback for degraded connectivity or privacy-sensitive deployments.

TTS:
Prefer local voices when acceptable; use online voices when quality, speed, or voice requirements matter more than network dependence.

## Latency Masking

Use latency masking only to bridge short waits, never to stall or obscure safety-critical escalation.

Safe Hungarian filler patterns:

- "Egy pillanat."
- "Rendben, átgondolom."
- "Fontos, hogy pontosan értselek."
- "Köszönöm a türelmed, már nézem a következő lépést."

Rules:

- Prefer one short filler over multiple stacked fillers.
- Use the shortest filler in crisis or safety-check context.
- Avoid empty social chatter during high-risk flows.
- If delay exceeds the masking budget, surface the delay explicitly or fail over to a faster route.