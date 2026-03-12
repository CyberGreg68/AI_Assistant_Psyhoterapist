# Hybrid Memory + Knowledge Base + Online Model Strategy

Ez a projekt akkor marad jol vedheto es olcso, ha az online modell nem a teljes valaszt generalja, hanem egy mar helyben elokeszitett kontextuson dolgozik.

## Javasolt minta

1. Helyi rovid memoria:
   - maradjon a mostani patient-scoped conversation memoria a rovid turnokhoz;
   - e felett legyen egy strukturalt `active_summary`, amely kulon tarolja a fo temat, trigger mintakat, elozo phrase-csaladokat es nyitott teendoket.

2. Helyi tudasbazis:
   - ne szabad szoveges dokumentumhalmaz legyen, hanem kicsi, klinikailag review-zott snippet gyujtemeny;
   - kulcsok: `kb_id`, `topic`, `risk_level`, `audience`, `lang`, `text`, `allowed_stages`;
   - forrasok: coping tippek, boundary szovegek, psychoeducation blokkok, operator playbook reszletek.

3. Helyi retrieval sorrend:
   - trigger + intent alapjan phrase candidate lista;
   - patient memory summary;
   - top 3-5 helyi KB snippet ugyanarrol a temarol;
   - csak ezutan online rerank vagy grounded expansion.

4. Online modell szerepe:
   - `phrase_selection` stage-ben csak rerankolja a helyi candidate-eket;
   - opcionálisan kivalaszt 1-2 relevans `kb_id`-t a helyi snippetek kozul;
   - `generative_fallback` stage-ben csak akkor ir uj szoveget, ha nincs jo phrase, es akkor is a helyi KB snippetekre kell tamaszkodnia.

## Mi legyen helyben, mi menjen online

Helyben:
- patient identity es consent logika
- crisis detection es handoff dontes
- phrase candidate kepzes
- tudasbazis retrieval
- auditolhato policy override-ok

Online:
- candidate rerank a kozelmult memoria es a helyi KB snippetek alapjan
- grounded expansion, ha nincs eleg jo determinisztikus phrase
- summary-kompaktalas alacsony kockazatu turnoknal

## Mi a legjobb kezdo online modell

Gyakorlati defaultnak a `openai/gpt-4o-mini` a legjobb valasztas GitHub Models alatt:
- jo esellyel elerheto a free/prototyping workflow-ban;
- a rate limitjei joval baratsagosabbak, mint a `gpt-5` csalade;
- candidate rerankre es rovid grounded bovitmenyre boven eleg.

A `openai/gpt-5-mini` jo masodik lepcso lehet, de csak akkor erdemes bekapcsolni, ha a fiokod es a rate limit tenylegesen tamogatja. Free szinten ez nem mindig lesz realis.

## Minimalisan ajanlott adatfolyam

1. `analyze_text()`
2. trigger match
3. local phrase candidate ranking
4. local patient summary + local KB snippet retrieval
5. online JSON-only rerank:
   - `candidate_id`
   - `kb_ids`
   - `reason`
6. deterministic final valasz epites
7. csak vegso esetben grounded generative fallback

## Miert jobb ez, mint a teljes online chat

- kisebb adatkuldes a felhobe
- konnyebben auditálhato dontesi lanc
- alacsonyabb koltseg
- kisebb kockazat krizishelyzetben
- jobban illeszkedik a jelenlegi manifest-driven runtime-hoz

## Kovetkezo implementacios lepesek

1. Vezessunk be `active_summary` mezot a session memory mellett.
2. Hozzunk letre egy kicsi, review-zott HU `knowledge_snippets` allomanyt.
3. Adjunk a runtime-hoz helyi snippet retrievalt intent/tag/topic alapjan.
4. A jelenlegi online rerank prompt kapja meg a local KB snippeteket es a strukturalt `active_summary` blokkot is.
5. Kockazatos helyzetben tiltsuk az online reranket, maradjon csak local deterministic ut.