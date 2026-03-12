# Literature Candidate Handoff

Igen, ez jo munkamegosztas.

Az a jo hatar, ha a masik agent:

- letolti a szakirodalmi vagy szakmai forrasokat;
- dokumentum-szinten metadatazza oket;
- indexeli es chunkolja oket;
- phrase- es trigger-jelolteket allit elo;
- minden jelolthoz eros provenance-ot ad;
- de nem ir bele kozvetlenul a runtime locale keszletekbe.

En a kovetkezo lepest vallalom:

- a candidate-ek review-ja;
- a schemahoz es kategoriakhoz igazitas;
- a fejlesztoi phrase/trigger keszletekbe valo beemeles;
- a safety, status es enabled_in metadata veglegesitese;
- tesztek es consistency checkek futtatasa.

## Javasolt Atadasi Csomag

Egy kulso agent egy kulon, datumozott staging csomagot adjon at, peldaul:

- `data_sources/literature_batches/2026-03-12-topic-name/manifest.json`
- `data_sources/literature_batches/2026-03-12-topic-name/documents.jsonl`
- `data_sources/literature_batches/2026-03-12-topic-name/chunks.jsonl`
- `data_sources/literature_batches/2026-03-12-topic-name/phrase_candidates.jsonl`
- `data_sources/literature_batches/2026-03-12-topic-name/trigger_candidates.jsonl`
- `data_sources/literature_batches/2026-03-12-topic-name/review_notes.md`

Semmi ne menjen direktbe ezekbe:

- `locales/*/phrases/*`
- `locales/*/triggers/*`
- `manifests/*`

## Document Metadata

Minden dokumentum kapjon stabil rekordot:

```json
{
  "doc_id": "lit_20260312_001",
  "title": "Example title",
  "authors": ["Author One", "Author Two"],
  "year": 2024,
  "source_type": "guideline",
  "publisher": "Publisher or journal",
  "language": "en",
  "url": "https://example.org/doc",
  "access": "public",
  "license": "unknown",
  "clinical_domain": ["crisis", "supportive_communication"],
  "population": ["adult"],
  "notes": "Short acquisition note"
}
```

Minimum elvaras:

- `doc_id`, `title`, `year`, `language`, `source_type`, `url` vagy mas visszakeresheto forras;
- egyertelmu, hogy guideline, review, training material vagy operator note;
- legyen lathato, hogy public, licensed vagy restricted forrasrol van-e szo.

## Chunk Index

Az indexelt chunk legyen kicsi, auditolhato, visszakovetheto:

```json
{
  "chunk_id": "lit_20260312_001_c012",
  "doc_id": "lit_20260312_001",
  "section": "Safety planning",
  "page_ref": "p.14",
  "text": "Short excerpt or normalized summary.",
  "topics": ["crisis", "safety_plan"],
  "risk_level": "critical",
  "confidence": 0.84
}
```

Fontos:

- ne teljes PDF-ek keruljenek runtime-kozeli allapotba, hanem chunkolt, visszakeresheto kivonatok;
- ha a licenc vagy hozzaferes kerdeses, a teljes szoveg helyett csak rovid normalized summary menjen tovabb;
- a `text` ne legyen tul hosszu, inkabb 1 idezheto resz vagy rovid osszefoglalo.

## Phrase Candidate Format

Minden phrase candidate legyen kulon rekord:

```json
{
  "candidate_id": "phr_cand_20260312_001",
  "lang": "hu",
  "category": "empathy",
  "intent": "emotional_support",
  "tags": ["emp", "val"],
  "allowed_uses": ["c", "t"],
  "suggested_priority": 2,
  "draft_text": "Ez nagyon nehez lehet most neked.",
  "rationale": "Short reason why this is clinically useful.",
  "source_doc_ids": ["lit_20260312_001"],
  "source_chunk_ids": ["lit_20260312_001_c012"],
  "evidence_level": "guideline_based",
  "safety_flags": ["non_crisis", "supportive_only"],
  "review_status": "candidate"
}
```

Elvarasok:

- egy candidate egy klinikailag ertelmes kommunikacios egyseg legyen;
- ne vegleges locale phrase-kent erkezzen, hanem draftkent;
- legyen mellette egy rovid `rationale`;
- kotelezo legyen a `source_doc_ids` es `source_chunk_ids`.

Ha tobb varians is van, kulon rekordok legyenek, ne egy osszefesult, nehezen review-zhato tomb.

## Trigger Candidate Format

Trigger candidate ugyanigy kulon rekord legyen:

```json
{
  "candidate_id": "trg_cand_20260312_001",
  "lang": "hu",
  "category": "crisis",
  "trigger_text": "nem akarok elni",
  "normalized_forms": ["nem akarok elni", "nem akarok mar elni"],
  "matched_tags": ["cri", "saf"],
  "suggested_risk_flags": ["crisis"],
  "confidence": 0.93,
  "source_doc_ids": ["lit_20260312_001"],
  "source_chunk_ids": ["lit_20260312_001_c004"],
  "rationale": "Maps to immediate self-harm concern.",
  "review_status": "candidate"
}
```

Elvarasok:

- trigger csak akkor jo, ha tenyleg lokalisan detektalhato nyelvi minta;
- legyenek `normalized_forms` ertekek az ekezetes es gyakori egyszerusitett alakokra;
- trigger candidate ne allitson runtime-policyt, csak javasoljon taget es risk flaget.

## Handoff Rules

Kulso agent ezt csinalhatja:

- letoltes;
- OCR vagy text extraction;
- chunkolas;
- embedding vagy index epites;
- deduplikacio;
- candidate ranking es priorizalas;
- provenance osszegyujtes.

Kulso agent ezt ne csinalja automatikusan:

- ne modositson runtime phrase fajlokat;
- ne modositson trigger fajlokat;
- ne adjon `appr` statuszt;
- ne tegyen be `enabled_in: ["rt"]` erteket review nelkul;
- ne toroljon meglevo curated elemeket.

## Review Gate For This Repo

Amikor en emelem be a candidate-eket a fejlesztoi keszletekbe, akkor lesznek hozzarendelve ezek:

- vegleges category;
- vegleges phrase vagy trigger forma;
- `meta.src`;
- `meta.status`;
- `meta.enabled_in`;
- esetleges `review_required`, `audit`, `review` blokkok.

Praktikus default:

- kulso agent kimenete mindig `candidate` vagy `rev` allapot logikahoz igazodjon;
- production-szeru `appr` statuszt csak manualis klinikai review utan adunk.

## Minimum Quality Bar

Csak olyan candidate erje meg az atadast, amelynel:

- van egyertelmu forras;
- van visszakeresheto chunk;
- van rovid, ertheto rationale;
- nincs nyilvanvalo safety kockazat vagy tul eros klinikai allitas;
- a szoveg nem tul hosszu, nem esszeszeru, hanem runtime-kompatibilis.

## Why This Split Works

Ez a felosztas jo, mert:

- a nehez, idogenyes dokumentumletoltes es indexeles kulon futhat;
- a repo nem szennyezodik automatikus, nyers candidate-ekkel a runtime locale fajlokban;
- a beemeles tovabbra is schema- es safety-vezereit marad;
- a provenance nem veszik el;
- kesobb ugyanebbbol knowledge snippet candidate-eket is lehet generalni.