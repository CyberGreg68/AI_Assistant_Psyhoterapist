from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from collections.abc import Iterable
from datetime import datetime, UTC
from pathlib import Path

from deep_translator import GoogleTranslator

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from assistant_runtime.json_utils import load_json_document


CACHE_PATH = ROOT / "scripts" / "locale_translation_cache.json"
SOURCE_LANG = "hu"

TARGETS = {
    "en": {
        "google_target": "en",
        "language_label": "English",
        "schema_title": "English patient triggers",
        "code_keys": {
            "tone": {"n": "neutral", "w": "warm", "f": "firm"},
            "length": {"s": "short", "m": "medium", "l": "long"},
            "use": {"c": "chat", "t": "tts", "s": "style_snippet"},
            "rec": {"w": "warm", "n": "neutral", "b": "brief", "f": "firm"},
        },
        "file_comment_template": "/* <filename> — <language>\\nSchema: id, pri, rec, use, tags, pp; pp[0] is canonical; code_keys live in the manifest */",
    },
    "de": {
        "google_target": "de",
        "language_label": "Deutsch",
        "schema_title": "Deutsche Patiententrigger",
        "code_keys": {
            "tone": {"n": "neutral", "w": "warm", "f": "bestimmt"},
            "length": {"s": "kurz", "m": "mittel", "l": "lang"},
            "use": {"c": "chat", "t": "tts", "s": "stilbaustein"},
            "rec": {"w": "warm", "n": "neutral", "b": "kurz", "f": "bestimmt"},
        },
        "file_comment_template": "/* <Dateiname> — <Sprache>\\nSchema: id, pri, rec, use, tags, pp; pp[0] ist kanonisch; code_keys stehen im Manifest */",
    },
}

MANIFEST_TEXT_FIELDS = (
    ("selection_rules", "step_1"),
    ("selection_rules", "step_2"),
    ("selection_rules", "step_3"),
    ("selection_rules", "step_4"),
    ("selection_rules", "step_5"),
    ("review_policy", "legacy_content_policy"),
    ("derivation_notes",),
)

OVERRIDES = {
    "en": {
        "Azonnali biztonsági helyzetek és emberi átadás (hard handoff).": "Immediate safety situations and human handoff (hard handoff).",
        "Szerepkörök, felelősségkorlátok, titoktartás és referral útmutatás.": "Role boundaries, limits of responsibility, confidentiality, and referral guidance.",
        "Ülés menete, hozzájárulás, időellenőrzés és folyamatok.": "Session flow, consent, time checks, and process guidance.",
        "Empatikus visszatükrözés és validálás.": "Empathic reflection and validation.",
        "Feltáró, nyitott kérdések a kontextus és érzések megismeréséhez.": "Exploratory open questions to understand context and emotions.",
        "Zárt, tényellenőrző kérdések és igen/nem ellenőrzések.": "Closed factual questions and yes-or-no checks.",
        "Rövid, gyakori variánsok az elismeréshez és gyors válaszokhoz (tokenkímélő).": "Short, frequent variants for acknowledgment and quick replies (token-efficient).",
        "Terápiás eszközök és kérdések (CBT, MI, DBT).": "Therapeutic tools and prompts (CBT, MI, DBT).",
        "Rövid oktató jellegű tippek és coping gyakorlatok.": "Brief psychoeducational tips and coping exercises.",
        "Bátorító, motiváló mondatok és kis lépések támogatása.": "Encouraging, motivating language and support for small steps.",
        "Lezárás, összefoglalás és következő lépések.": "Closing, summary, and next steps.",
        "Kulturális érzékenység, lokalizációs megjegyzések és alternatív kifejezések.": "Cultural sensitivity, localization notes, and alternative wording.",
        "A krízisprotokollok a mentálhigiénés gyakorlatból és akut biztonsági irányelvekből származnak; cél a gyors, egyértelmű utasítás és emberi beavatkozás kezdeményezése.": "Crisis protocols draw on mental health practice and acute safety guidelines; the aim is rapid, clear instruction and timely human intervention.",
        "Etikai és jogi irányelvek alapján: mit nem adhat a rendszer (orvosi/jogi tanács), és hogyan irányít szakemberhez.": "Grounded in ethical and legal guidance: what the system must not provide (medical or legal advice) and how it should refer to a professional.",
        "Terápiás ülések strukturálásából: agenda, consent és időkezelés segít a biztonságos és hatékony munkában.": "Drawn from structured therapy session practice: agenda-setting, consent, and time management support safe and effective work.",
        "Kliensközpontú terápiás gyakorlatok (pl. Rogersi empátia) alapján: érzelmi validálás csökkenti a distresszt és növeli a kooperációt.": "Based on client-centered therapeutic practice, such as Rogersian empathy: emotional validation can reduce distress and support cooperation.",
        "Kvalitatív feltáró technikákból: nyitott kérdések segítik a részletes információgyűjtést és a kliens narratívájának feltárását.": "From exploratory interviewing techniques: open questions help gather detailed information and elicit the client's narrative.",
        "Biztonsági és adminisztratív célokra: gyors tényellenőrzés szükséges a kockázatfelméréshez és időpont‑egyeztetéshez.": "For safety and administrative purposes: quick factual checks are needed for risk assessment and appointment coordination.",
        "Runtime optimalizációs igény: rövid fragmensek csökkentik a tokenköltséget és gyorsítják a válaszadást; cél a természetes, folyamatos beszélgetés fenntartása.": "This category serves runtime optimization: short fragments reduce token cost and speed up responses while preserving a natural conversational flow.",
        "Ezek a kategóriák a pszichoterápiás módszerek standard eszközeit tükrözik: strukturált kérdések, skálázás, viselkedéses kísérletek, distress tolerance.": "These categories reflect standard tools from psychotherapy approaches: structured questions, scaling, behavioral experiments, and distress tolerance skills.",
        "Pszichoedukációs gyakorlatokból: rövid, bizonyíték‑alapú tippek, amelyek otthoni használatra alkalmasak.": "From psychoeducational practice: brief, evidence-informed tips suitable for use between sessions and at home.",
        "Motivációs és viselkedés‑változtatási elvekből: megerősítés és kis célok támogatása növeli az elköteleződést.": "From motivation and behavior-change principles: reinforcement and support for small goals can strengthen engagement.",
        "Üléslezárási gyakorlatok: összegzés és következő lépések tisztázása javítja a folyamat követhetőségét.": "From session-closing practice: summarizing and clarifying next steps improves continuity and follow-through.",
        "Lokalizációs és kulturális érzékenységi irányelvekből: cél a sértő vagy félreérthető kifejezések elkerülése és a helyi kontextus figyelembevétele.": "From localization and cultural-sensitivity guidance: the goal is to avoid offensive or ambiguous phrasing and stay aware of local context.",
        "Ha a krízisdetektor vagy bármely elem 'cri' tagje aktiválódik, azonnal prioritást kap a crisis kategória és indítandó a hard handoff protokoll.": "If the crisis detector fires or any item with the 'cri' tag is activated, immediately prioritize the crisis category and start the hard handoff protocol.",
        "Szűrd a kategóriákat az aktuális intent relevanciája szerint; előnyben részesítendők a manifestben korábban szereplő kategóriák.": "Filter categories by relevance to the current intent; when in doubt, prefer categories that appear earlier in the manifest.",
        "Egy kategórián belül preferáld azokat az elemeket, amelyek tags mezője legjobban illeszkedik az aktuális kontextushoz és a kívánt tónushoz (rec).": "Within a category, prefer items whose tags best match the current context and desired tone (rec).",
        "Ha tie van, rövidebb 'variants' elemek használata tokenköltség csökkentésére.": "If there is a tie, prefer shorter 'variants' items to reduce token cost.",
        "Per‑item 'pri' felülírhatja a kategória alapértelmezett prioritását, de csak ha explicit beállították.": "A per-item 'pri' value may override the category default priority, but only when it is explicitly set.",
        "A kategóriák klinikai gyakorlatokból (CBT, MI, DBT), krízisprotokollokból és lokalizációs irányelvekből lettek összeállítva. A manifest célja implementációs útmutató; a végleges tartalmakat klinikai szakértőnek javasolt felülvizsgálni.": "The categories were assembled from clinical practices (CBT, MI, DBT), crisis protocols, and localization guidance. The manifest is an implementation guide; final content should be reviewed by a clinical expert.",
        "Van tervem estére": "I have a plan for tonight",
        "Elhatároztam, hogy ma vége": "I've decided to end it today",
        "Teljesen érthető, hogy így érzel.": "It's completely understandable that you feel this way.",
        "Érthető, hogy így érzed magad.": "It makes sense that you feel this way.",
        "Nem meglepő, hogy ilyen érzéseid vannak; ez teljesen jogos.": "It's not surprising that you have these feelings; they are completely valid.",
        "Ez nagyon nehéz lehet számodra.": "This must be very hard for you.",
        "Hiányzik aki meghalt": "I miss the person who died"
    },
    "de": {
        "Azonnali biztonsági helyzetek és emberi átadás (hard handoff).": "Akute Sicherheitssituationen und menschliche Übergabe (Hard Handoff).",
        "Szerepkörök, felelősségkorlátok, titoktartás és referral útmutatás.": "Rollen, Verantwortungsgrenzen, Vertraulichkeit und Hinweise zur Weitervermittlung.",
        "Ülés menete, hozzájárulás, időellenőrzés és folyamatok.": "Sitzungsablauf, Einwilligung, Zeitcheck und Prozessführung.",
        "Empatikus visszatükrözés és validálás.": "Empathisches Spiegeln und Validieren.",
        "Feltáró, nyitott kérdések a kontextus és érzések megismeréséhez.": "Erkundende, offene Fragen zum Verstehen von Kontext und Gefühlen.",
        "Zárt, tényellenőrző kérdések és igen/nem ellenőrzések.": "Geschlossene Fragen zur Faktenklärung und Ja/Nein-Prüfungen.",
        "Rövid, gyakori variánsok az elismeréshez és gyors válaszokhoz (tokenkímélő).": "Kurze, häufige Varianten für Bestätigung und schnelle Antworten (tokensparend).",
        "Terápiás eszközök és kérdések (CBT, MI, DBT).": "Therapeutische Werkzeuge und Fragen (CBT, MI, DBT).",
        "Rövid oktató jellegű tippek és coping gyakorlatok.": "Kurze psychoedukative Hinweise und Bewältigungsübungen.",
        "Bátorító, motiváló mondatok és kis lépések támogatása.": "Ermutigende, motivierende Formulierungen und Unterstützung kleiner Schritte.",
        "Lezárás, összefoglalás és következő lépések.": "Abschluss, Zusammenfassung und nächste Schritte.",
        "Kulturális érzékenység, lokalizációs megjegyzések és alternatív kifejezések.": "Kulturelle Sensibilität, Lokalisierungshinweise und alternative Formulierungen.",
        "A krízisprotokollok a mentálhigiénés gyakorlatból és akut biztonsági irányelvekből származnak; cél a gyors, egyértelmű utasítás és emberi beavatkozás kezdeményezése.": "Krisenprotokolle stützen sich auf die Praxis der psychischen Gesundheit und auf akute Sicherheitsleitlinien; Ziel sind schnelle, klare Anweisungen und eine rechtzeitige menschliche Intervention.",
        "Etikai és jogi irányelvek alapján: mit nem adhat a rendszer (orvosi/jogi tanács), és hogyan irányít szakemberhez.": "Grundlage sind ethische und rechtliche Leitlinien: was das System nicht leisten darf (medizinische oder rechtliche Beratung) und wie es an Fachpersonen weiterverweist.",
        "Terápiás ülések strukturálásából: agenda, consent és időkezelés segít a biztonságos és hatékony munkában.": "Abgeleitet aus der Strukturierung therapeutischer Sitzungen: Agenda, Einwilligung und Zeitmanagement unterstützen sicheres und wirksames Arbeiten.",
        "Kliensközpontú terápiás gyakorlatok (pl. Rogersi empátia) alapján: érzelmi validálás csökkenti a distresszt és növeli a kooperációt.": "Auf Grundlage klientenzentrierter therapeutischer Praxis, etwa der rogersschen Empathie: emotionale Validierung kann Belastung verringern und Kooperation fördern.",
        "Kvalitatív feltáró technikákból: nyitott kérdések segítik a részletes információgyűjtést és a kliens narratívájának feltárását.": "Aus explorativen Gesprächstechniken: offene Fragen helfen, detaillierte Informationen zu sammeln und die Erzählung der Klientin oder des Klienten zu erschließen.",
        "Biztonsági és adminisztratív célokra: gyors tényellenőrzés szükséges a kockázatfelméréshez és időpont‑egyeztetéshez.": "Für Sicherheits- und Verwaltungszwecke sind schnelle Faktenabfragen für Risikoabschätzung und Terminabstimmung erforderlich.",
        "Runtime optimalizációs igény: rövid fragmensek csökkentik a tokenköltséget és gyorsítják a válaszadást; cél a természetes, folyamatos beszélgetés fenntartása.": "Diese Kategorie dient der Laufzeitoptimierung: kurze Fragmente senken die Token-Kosten und beschleunigen Antworten, ohne den natürlichen Gesprächsfluss zu verlieren.",
        "Ezek a kategóriák a pszichoterápiás módszerek standard eszközeit tükrözik: strukturált kérdések, skálázás, viselkedéses kísérletek, distress tolerance.": "Diese Kategorien bilden Standardwerkzeuge psychotherapeutischer Verfahren ab: strukturierte Fragen, Skalierungen, Verhaltensexperimente und Stresstoleranz.",
        "Pszichoedukációs gyakorlatokból: rövid, bizonyíték‑alapú tippek, amelyek otthoni használatra alkalmasak.": "Aus psychoedukativer Praxis: kurze, evidenzinformierte Hinweise, die sich auch zwischen Sitzungen oder zu Hause anwenden lassen.",
        "Motivációs és viselkedés‑változtatási elvekből: megerősítés és kis célok támogatása növeli az elköteleződést.": "Aus Prinzipien der Motivation und Verhaltensänderung: Bestärkung und die Unterstützung kleiner Ziele können die Mitarbeit fördern.",
        "Üléslezárási gyakorlatok: összegzés és következő lépések tisztázása javítja a folyamat követhetőségét.": "Aus der Praxis des Sitzungsabschlusses: Zusammenfassung und Klärung der nächsten Schritte verbessern Kontinuität und Nachvollziehbarkeit.",
        "Lokalizációs és kulturális érzékenységi irányelvekből: cél a sértő vagy félreérthető kifejezések elkerülése és a helyi kontextus figyelembevétele.": "Aus Leitlinien zu Lokalisierung und kultureller Sensibilität: Ziel ist es, verletzende oder missverständliche Ausdrücke zu vermeiden und den lokalen Kontext mitzudenken.",
        "Ha a krízisdetektor vagy bármely elem 'cri' tagje aktiválódik, azonnal prioritást kap a crisis kategória és indítandó a hard handoff protokoll.": "Wenn der Krisendetektor anschlägt oder ein Element mit dem Tag 'cri' aktiviert wird, hat die Krisenkategorie sofort Vorrang und das Hard-Handoff-Protokoll wird gestartet.",
        "Szűrd a kategóriákat az aktuális intent relevanciája szerint; előnyben részesítendők a manifestben korábban szereplő kategóriák.": "Filtere die Kategorien nach ihrer Relevanz für die aktuelle Intention; im Zweifel sind Kategorien zu bevorzugen, die im Manifest früher erscheinen.",
        "Egy kategórián belül preferáld azokat az elemeket, amelyek tags mezője legjobban illeszkedik az aktuális kontextushoz és a kívánt tónushoz (rec).": "Bevorzuge innerhalb einer Kategorie die Elemente, deren Tags am besten zum aktuellen Kontext und zum gewünschten Ton (rec) passen.",
        "Ha tie van, rövidebb 'variants' elemek használata tokenköltség csökkentésére.": "Bei Gleichstand sollten kürzere 'variants'-Elemente bevorzugt werden, um Token-Kosten zu senken.",
        "Per‑item 'pri' felülírhatja a kategória alapértelmezett prioritását, de csak ha explicit beállították.": "Ein pro Element gesetzter Wert in 'pri' kann die Standardpriorität der Kategorie überschreiben, aber nur wenn er explizit gesetzt wurde.",
        "Legacy data/phrases content is mirror-only; locales and manifests are the primary source of truth.": "Alte Inhalte unter data/phrases dienen nur als Spiegel; locales und manifests sind die maßgebliche Quelle.",
        "A kategóriák klinikai gyakorlatokból (CBT, MI, DBT), krízisprotokollokból és lokalizációs irányelvekből lettek összeállítva. A manifest célja implementációs útmutató; a végleges tartalmakat klinikai szakértőnek javasolt felülvizsgálni.": "Die Kategorien wurden aus klinischen Praktiken (CBT, MI, DBT), Krisenprotokollen und Lokalisierungsleitlinien zusammengestellt. Das Manifest dient als Implementierungsleitfaden; die endgültigen Inhalte sollten von einer klinischen Fachperson geprüft werden.",
        "Van tervem estére": "Ich habe einen Plan für heute Abend",
        "Elhatároztam, hogy ma vége": "Ich habe beschlossen, es heute zu beenden",
        "Teljesen érthető, hogy így érzel.": "Es ist völlig verständlich, dass Sie sich so fühlen.",
        "Érthető, hogy így érzed magad.": "Es ist nachvollziehbar, dass Sie sich so fühlen.",
        "Nem meglepő, hogy ilyen érzéseid vannak; ez teljesen jogos.": "Es ist nicht überraschend, dass Sie solche Gefühle haben; sie sind vollkommen nachvollziehbar.",
        "Ez nagyon nehéz lehet számodra.": "Das muss sehr schwer für Sie sein.",
        "Hiányzik aki meghalt": "Ich vermisse die Person, die gestorben ist"
    }
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Populate EN/DE locale content from Hungarian source files.")
    parser.add_argument("--langs", nargs="+", default=["en", "de"], choices=sorted(TARGETS))
    return parser.parse_args()


def load_json(path: Path):
    if path.suffix == ".json":
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    return load_json_document(path)


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_cache() -> dict[str, dict[str, str]]:
    if not CACHE_PATH.exists():
        return {lang: {} for lang in TARGETS}
    payload = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    return {lang: dict(payload.get(lang, {})) for lang in TARGETS}


def save_cache(cache: dict[str, dict[str, str]]) -> None:
    ordered = {lang: dict(sorted(cache.get(lang, {}).items())) for lang in sorted(TARGETS)}
    write_json(CACHE_PATH, ordered)


def chunked(values: list[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def translate_batch(lang: str, texts: list[str], cache: dict[str, dict[str, str]]) -> None:
    for text in texts:
        if text in OVERRIDES[lang]:
            cache[lang][text] = OVERRIDES[lang][text]

    pending = [text for text in texts if text and text not in cache[lang]]
    if not pending:
        return

    translator = GoogleTranslator(source=SOURCE_LANG, target=TARGETS[lang]["google_target"])
    for batch in chunked(pending, 20):
        try:
            translated = translator.translate_batch(batch)
            if not isinstance(translated, list) or len(translated) != len(batch):
                raise ValueError("Unexpected batch translation response.")
        except Exception:
            translated = [translator.translate(item) for item in batch]

        for source, target in zip(batch, translated, strict=True):
            cache[lang][source] = str(target).strip()


def translate_text(lang: str, text: str | None, cache: dict[str, dict[str, str]]) -> str | None:
    if text is None:
        return None
    if text == "":
        return ""
    if text in OVERRIDES[lang]:
        cache[lang][text] = OVERRIDES[lang][text]
        return cache[lang][text]
    if text not in cache[lang]:
        translate_batch(lang, [text], cache)
    return cache[lang][text]


def collect_phrase_texts(items: list[dict]) -> set[str]:
    texts: set[str] = set()
    for item in items:
        review = item.get("review")
        if isinstance(review, dict) and isinstance(review.get("safety_notes"), str):
            texts.add(review["safety_notes"])
        for phrase in item.get("pp", []):
            text = phrase.get("txt")
            if isinstance(text, str):
                texts.add(text)
    return texts


def collect_trigger_texts(items: list[dict]) -> set[str]:
    texts: set[str] = set()
    for item in items:
        for example in item.get("ex", []):
            if isinstance(example, str):
                texts.add(example)
        for key in ("cult", "note"):
            value = item.get(key)
            if isinstance(value, str):
                texts.add(value)
    return texts


def build_regex(examples: list[str]) -> str:
    escaped = [re.escape(example) for example in examples if example]
    if not escaped:
        return ""
    return "(?:" + "|".join(escaped) + ")"


def translate_phrase_items(lang: str, items: list[dict], cache: dict[str, dict[str, str]]) -> list[dict]:
    translated_items: list[dict] = []
    for item in items:
        translated_item = copy.deepcopy(item)
        review = translated_item.get("review")
        if isinstance(review, dict) and isinstance(review.get("safety_notes"), str):
            review["safety_notes"] = translate_text(lang, review["safety_notes"], cache)
        for phrase in translated_item.get("pp", []):
            if isinstance(phrase.get("txt"), str):
                phrase["txt"] = translate_text(lang, phrase["txt"], cache)
        translated_items.append(translated_item)
    return translated_items


def translate_trigger_items(lang: str, items: list[dict], cache: dict[str, dict[str, str]]) -> list[dict]:
    translated_items: list[dict] = []
    for item in items:
        translated_item = copy.deepcopy(item)
        translated_examples = [translate_text(lang, example, cache) or "" for example in item.get("ex", [])]
        translated_item["ex"] = translated_examples

        matcher = translated_item.get("m")
        if isinstance(matcher, dict):
            match_type = matcher.get("t")
            if match_type == "exact" and translated_examples:
                matcher["p"] = translated_examples[0]
            elif match_type in {"regex", "hybrid"} and translated_examples:
                matcher["p"] = build_regex(translated_examples)

        for key in ("cult", "note"):
            if isinstance(translated_item.get(key), str):
                translated_item[key] = translate_text(lang, translated_item[key], cache)

        translated_items.append(translated_item)
    return translated_items


def collect_manifest_texts(manifest: dict) -> set[str]:
    texts: set[str] = set()
    for category in manifest.get("category_order", []):
        for key in ("description", "derivation"):
            value = category.get(key)
            if isinstance(value, str):
                texts.add(value)
    for path in MANIFEST_TEXT_FIELDS:
        value = manifest
        for part in path:
            value = value.get(part) if isinstance(value, dict) else None
        if isinstance(value, str):
            texts.add(value)
    return texts


def translate_manifest(lang: str, manifest: dict, cache: dict[str, dict[str, str]]) -> dict:
    translated = copy.deepcopy(manifest)
    translated["lang"] = lang
    translated["generated_at"] = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    translated["code_keys"] = copy.deepcopy(TARGETS[lang]["code_keys"])
    translated["file_comment_template"] = TARGETS[lang]["file_comment_template"]

    for category in translated.get("category_order", []):
        short_name = Path(category["filename"]).name.split("_", 2)[1]
        category["filename"] = f"phrases/{category['prefix']}_{short_name}_phrases.{lang}.jsonc"
        for key in ("description", "derivation"):
            if isinstance(category.get(key), str):
                category[key] = translate_text(lang, category[key], cache)

    for path in MANIFEST_TEXT_FIELDS:
        target = translated
        for part in path[:-1]:
            target = target[part]
        leaf = path[-1]
        if isinstance(target.get(leaf), str):
            target[leaf] = translate_text(lang, target[leaf], cache)

    return translated


def localize_schema(lang: str, schema: dict) -> dict:
    localized = copy.deepcopy(schema)
    localized["title"] = TARGETS[lang]["schema_title"]
    return localized


def gather_translation_units(manifest: dict) -> tuple[set[str], set[str], set[str]]:
    phrase_texts: set[str] = set()
    trigger_texts: set[str] = set()
    manifest_texts = collect_manifest_texts(manifest)

    for category in manifest["category_order"]:
        phrase_path = ROOT / "locales" / SOURCE_LANG / category["filename"]
        phrase_texts.update(collect_phrase_texts(load_json(phrase_path)))

        trigger_name = Path(category["filename"]).name.replace("_phrases.hu.jsonc", f"_triggers.{SOURCE_LANG}.json")
        trigger_path = ROOT / "locales" / SOURCE_LANG / "triggers" / trigger_name
        trigger_texts.update(collect_trigger_texts(load_json(trigger_path)))

    return phrase_texts, trigger_texts, manifest_texts


def populate_language(lang: str, cache: dict[str, dict[str, str]]) -> None:
    manifest = load_json(ROOT / "manifests" / f"manifest.{SOURCE_LANG}.jsonc")
    phrase_texts, trigger_texts, manifest_texts = gather_translation_units(manifest)
    translate_batch(lang, sorted(phrase_texts | trigger_texts | manifest_texts), cache)

    localized_manifest = translate_manifest(lang, manifest, cache)
    write_json(ROOT / "manifests" / f"manifest.{lang}.jsonc", localized_manifest)

    source_schema = load_json(ROOT / "locales" / SOURCE_LANG / "triggers" / "schema.triggers.json")
    write_json(ROOT / "locales" / lang / "triggers" / "schema.triggers.json", localize_schema(lang, source_schema))

    readme_path = ROOT / "locales" / lang / "triggers" / "README.txt"
    if not readme_path.exists():
        readme_path.write_text(
            f"Place {lang} patient-side trigger files here using the NN_short_triggers.{lang}.json convention.\n",
            encoding="utf-8",
        )

    for category in manifest["category_order"]:
        source_phrase_path = ROOT / "locales" / SOURCE_LANG / category["filename"]
        translated_phrase_path = ROOT / "locales" / lang / Path(localized_manifest["category_order"][int(category["prefix"]) - 1]["filename"])
        source_phrase_items = load_json(source_phrase_path)
        write_json(translated_phrase_path, translate_phrase_items(lang, source_phrase_items, cache))

        trigger_name = Path(category["filename"]).name.replace("_phrases.hu.jsonc", f"_triggers.{SOURCE_LANG}.json")
        translated_trigger_name = trigger_name.replace(f".{SOURCE_LANG}.json", f".{lang}.json")
        source_trigger_path = ROOT / "locales" / SOURCE_LANG / "triggers" / trigger_name
        translated_trigger_path = ROOT / "locales" / lang / "triggers" / translated_trigger_name
        source_trigger_items = load_json(source_trigger_path)
        write_json(translated_trigger_path, translate_trigger_items(lang, source_trigger_items, cache))


def main() -> int:
    args = parse_args()
    cache = load_cache()
    for lang in args.langs:
        populate_language(lang, cache)
        save_cache(cache)
        print(f"Populated {lang} locale content from {SOURCE_LANG} source.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())