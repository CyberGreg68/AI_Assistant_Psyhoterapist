# Improvement Backlog

Ez a dokumentum a 2026-03-12 allapot alapjan foglalja ossze a legfontosabb kovetkezo javitasokat.

## Elso Prioritas

### 1. Online rerank uzemkepesre hozasa

Cel:
Az online candidate rerank ne csak fallback local modban fusson, hanem stabilan elerje a GitHub Models vegpontot.

Teendok:
- `LLM_API_TOKEN` beallitas `.env.local` vagy szolgaltatasi kornyezet szinten.
- Inditaskor egyszeru readiness check a `phrase_selection` online modelre.
- 401, 429, 5xx hibak kulon kezelese a debug payloadban.
- Opcionális circuit breaker, hogy ideiglenes szolgaltatasi hiba eseten ne probaljon minden turnben ujra online rerankot.

Haszon:
- jobb kontextus-alapu phrase valasztas
- kevesebb ismetlodes
- atlathatobb online/offline uzemi allapot

### 2. Patient identity megerositese

Cel:
Az anon browser identity, a valodi `patient_id`, es a kesobbi secure csatornas azonositas egy kozos modellbe keruljon.

Teendok:
- kulon `identity_confidence` mező a runtime payloadban
- `browser_patient_key` rotacio szabalyok es lejart kulcsok kezelese
- opcionális session linking, amikor egy anonim browser session kesobb valodi patient_id-hez kotodik
- explicit consent es retention policy mezok az identity metadatahoz

Haszon:
- pontosabb patient-scoped memory
- kevesebb teves history-osszekeveres
- jobb audit es adatkezeles

### 3. Summary-kompaktalas es memory-higienia

Cel:
A jelenlegi rovid turn-lista es egyszeru summary helyett jobban hasznalhato, olcsobb prompt-kontekstus legyen.

Teendok:
- kulon `active_summary` es `recent_turns` szerkezet
- fontos temak, coping allapot, trigger mintak, elozo phrase-csaladok kulon tarolasa
- item-level anti-repeat score, nem csak utolso item tiltasa
- summary frissites csak minden N. turn utan vagy triggeresen

Haszon:
- rovidebb prompt
- jobb rerank dontes
- stabilabb tobbszoros beszelgeteseknel

## Gyors Nyereseg

### 4. Phrase-diverzifikacio javitasa

Teendok:
- variant-rotacio itemen belul
- category-family cooldown
- gyenge negativ pont ugyanarra a kerdes-tipusra ket-harom turnon belul

Varhato eredmeny:
kevesebb repetitiv kerdes, termeszetesebb flow

### 5. Trigger + analysis erosites

Teendok:
- trigger confidence vagy match_strength beemelese a debugba
- local analysis bovitese breakup, grief, panic, rumination, sleep, shame altag-ekkel
- tobb accent-insensitive es szinonima coverage a HU trigger corpusban

Varhato eredmeny:
jobb helyi jeloltlista mar online modell nelkul is

### 6. Browser UX visszajelzesek

Teendok:
- lathato `Local Only` / `Online Ready` badge a topbarban
- kulon jelzes, ha a valasz online rerank vagy local rotation miatt szuletett
- identity allapot egyszerubb nyelven a paciens-nezetben, technikai resz csak debugban

Varhato eredmeny:
kevesebb zavar a demo hasznalatakor

## Kozepes Tav

### 7. Tartósabb storage reteg

Cel:
Az egyetlen JSON fajl helyett megbizhatobb, lockolhato, tobb-folyamatos tarolo.

Opciók:
- SQLite lokalisan
- kis kulcs-ertek store
- kesobb kulso service adapter

Miért erdemes:
- jobb konkurens iras
- egyszerubb retention torles
- kereshetobb patient history

### 8. Megfigyelhetoseg es uzemeltetes

Teendok:
- `operations` vagy uj `/metrics` vegponton alap metrikak
- online rerank success rate
- fallback okok bontasa: no token, 401, 429, parse fail, invalid candidate
- handoff rate, trigger coverage, top repeated items

Haszon:
gyorsabb tuning es hibakereses

### 9. Safety policy finomitas

Teendok:
- kulon szabaly arra, mikor tilos online rerankot hasznalni magas kockazatnal
- kritikus helyzetben csak local deterministic valasztas + handoff prioritas
- safety override lista kategoriakra es phrase id-kra

Haszon:
jobban vedheto klinikai viselkedes

## Kesobbi Okositas

### 10. Learned rerank score helyi oldalon

Cel:
Az online modelltol fuggetlen, olcso helyi pontozas a jobb jeloltsorrendhez.

Irányok:
- elozo valaszcsaladok buntetese
- intent-transition score
- trigger-to-category success statisztikak
- felhasznaloi feedbackbol egyszeru local prior frissites

### 11. Patient-state modell

Cel:
Ne csak uzeneteket, hanem allapotot is kovessunk.

Peldak:
- `stress_high`
- `sleep_fragmented`
- `rumination_active`
- `pause_requested`
- `handoff_pending`

Haszon:
okosabb, allapotkoveto phrase valasztas

### 12. Secure identification flow

Cel:
A demo browser identitybol kesobb valodi produkcios azonositas fele lehessen lepni.

Irányok:
- magic link vagy secure messaging handoff
- egyszeri kod alapu patient-linking
- kulon audit trail az identity merge es unlink esemenyekre

## Javasolt Kovetkezo Sorrend

1. Online rerank readiness + tokenes uzem
2. Variant rotacio es anti-repeat score
3. Summary-kompaktalas strukturalt memory modellel
4. SQLite-alapu persistencia
5. Metrics + retry/circuit breaker

## Megjegyzes

A jelenlegi allapot mar jo alap egy biztonsagos, local-first, patient-scoped demonstratorhoz. A legnagyobb minosegi ugrast rovid tavon nem az uj UI, hanem az online rerank tenyleges uzembe allitasa, a phrase-diverzifikacio, es a strukturalt patient-memory fogja adni.