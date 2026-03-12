# Profile Ingest And Voice Pipeline

Ez a dokumentum azt a gyakorlati folyamatot irja le, ahogyan klinikusi beszelgeteskivonatokbol, transzkriptumokbol es hangfelvetelekbol profil-specifikus runtime anyagot lehet epiteni.

## Cel

Az ingest ne irjon kozvetlenul a produkcios phrase es trigger allomanyokba. Ehelyett eloszor review-pack keszuljon, amelyet a szakember, a content reviewer es adott esetben a klinikai reviewer hagy jova.

## Mit nyerunk ki a forrasokbol

Kivonatokbol:
- profil-specifikus temak es coping mintak
- jellemzo magyarazo blokkok
- boundary es safety hangsulyok
- visszatero pszichoedukacios tartalmak

Transzkriptumokbol:
- rovid, jo terapeuta-valaszok mint phrase-jeloltek
- paciens oldali megfogalmazasok mint trigger-peldak
- gyakori temak, nyelvi regiszter, tempo, empatikus fordulatok

Hanganyagbol:
- speaker-clone seed manifest
- mondathossz, prosodia, szunethasznalat megfigyelese
- kesobbi TTS fine-tuning reference lista

## Javasolt ingest folyamat

1. A nyers anyagokrol consent es felhasznalasi jog ellenorzes.
2. Audio eseten transzkriptum keszitese.
3. `scripts/build_profile_ingest_pack.py` futtatasa.
4. Review-pack keletkezik phrase, trigger, KB es voice seed jeloltekkel.
5. Klinikai review utan a jo elemek kerulnek be a locale phrase/triggers vagy knowledge snippet allomanyokba.

Az aktualis CLI ehhez mar rendelkezesre all:
- `scripts/build_profile_ingest_pack.py`

## Pelda futtatas

```powershell
python scripts\build_profile_ingest_pack.py \
  --profile-id therapist_a \
  --summary .\data_sources\therapist_a_summary.md \
  --transcript .\data_sources\therapist_a_transcript.txt \
  --audio .\data_sources\therapist_a_session_01.wav \
  --output .\data_sources\ingest\therapist_a.review_pack.json
```

## Mire jo a review pack

- phrase candidate shortlist keszitese
- trigger peldaanyag gyujtese
- helyi knowledge snippet bazis bovites
- speaker clone seed lista elokeszitese

## Egységes metadata minden uj elemre

Az uj ingestalt elemek kapjanak egy rovid `meta` blokkot. Ez jobb, mint a jelenlegi szemantikus `tags` mezot tulterhelni, mert a runtime ma a `tags`-et tematikus illesztesre hasznalja.

Javasolt rovid kodok:

- `src`: `dev` developer seed, `sum` szoveges kivonat, `trn` transcript, `aud` hanganyag, `llm` LLM-javaslat, `lit` szakirodalom, `mix` kevert eredet
- `status`: `appr` jovahagyott, `rev` review alatt, `sugg` frissen beszurt ajanlas, `test` csak tesztelheto, `hold` tiltott vagy visszatartott
- `enabled_in`: `rt` runtime, `rv` review, `tst` teszt

Peldak:

- egy fejlesztok altal kezdoen betoltott, runtime-ban hasznalhato phrase: `{"src":"dev","status":"appr","enabled_in":["rt","rv","tst"]}`
- egy uj, transcriptbol kinyert phrase: `{"src":"trn","status":"rev","enabled_in":["rv","tst"]}`
- egy LLM altal ajanlott szakirodalmi blokk: `{"src":"llm","status":"sugg","enabled_in":["rv","tst"]}`

Igy a runtime alapbol csak az `appr` + `rt` elemeket hasznalja, de a demo vagy a klinikai teszt mod explicit be tudja huzni a `rev` vagy `test` statuszu elemeket is.

## Folyamatos ingest ciklus

Az ingest lehet napi vagy heti folyamat:

1. a terapeuta feltolt kivonatokat vagy hanganyagokat;
2. a review-pack generator uj phrase/trigger/KB jelolteket keszit `meta.src` es `meta.status` mezokkel;
3. a review queue riport kigyujti a nem jovahagyott elemeket;
4. a szakember review utan az elemek `appr` statuszba lepnek;
5. a runtime ettol kezdve automatikusan tudja hasznalni oket.

## Voice clone irany

Atmeneti fazisban a nyers beszelgetesi hanganyag is hasznalhato seedkent, de ez kompromisszumos:

- sok a zaj, atfedes es nem kontrollalt a mikrofonminoseg
- a terapeuta nem ugyanazokat a phrase-okat mondja, mint amiket a runtime kivalaszt
- emiatt a vegeredmeny termeszetes lehet, de nem feltetlenul lesz kovetkezetes phrase-TTS rendszernek optimalizalva

## Javasolt ketfazisu hangstrategia

1. Seed fazis:
   - tisztitott session hanganyagok gyujtese
   - diarization vagy manualis speaker szeparalas
   - csak hozzajarulassal, audit nyomvonallal
   - speaker clone alapmodell vagy voice conversion kiserleti tanitas

2. Production fazis:
   - a jo, vegleges phrase mintak kulon felolvastatasa a szakemberrel
   - kontrollalt mondatlista, tiszta mikrofon, kulon akusztikai kornyezet
   - ezekkel keszul a vegleges brandelt TTS hang vagy voice font

## Gyakorlati kompromisszum

Az atmeneti demoban jo strategia lehet:

- most: altalanos helyi vagy felhos TTS hang + profil-specifikus phrase es tudastartalom
- koztes allapot: session audio alapjan prototipus speaker clone seed
- kesobb: kulon phrase-felolvasasokkal finomitott vegleges hang

## Fontos vedelmi szabalyok

- kulon hozzajarulas kell training vagy cloning celra
- a session hanganyag ne keruljon automatikusan a runtime-ba
- a nyers ingest pack csak review utan merge-elheto
- krizis vagy erosen szenzitiv anyagbol ne generaljunk automatikusan phrase-jelolteket review nelkul