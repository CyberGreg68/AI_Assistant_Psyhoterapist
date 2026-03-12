from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from assistant_runtime.json_utils import load_json_document

try:
    from jsonschema import Draft202012Validator
except ImportError as exc:  # pragma: no cover
    raise SystemExit("jsonschema is required to validate generated triggers.") from exc


TRIGGER_DIR = ROOT / "locales" / "hu" / "triggers"
README_PATH = TRIGGER_DIR / "README.txt"
SCHEMA_PATH = TRIGGER_DIR / "schema.triggers.json"
INDEX_PATH = TRIGGER_DIR / "sample_index.json"
REPORT_PATH = TRIGGER_DIR / "changes_report.json"
MISSING_CSV_PATH = TRIGGER_DIR / "missing_candidates.csv"

SHORT_LABELS = {
    "crisis": "cri",
    "boundary": "bd",
    "structure": "str",
    "empathy": "emp",
    "open_questions": "oq",
    "closed_questions": "cq",
    "variants": "var",
    "cbt_mi_dbt": "cbt",
    "psychoeducation": "edu",
    "encouragement": "enc",
    "closing": "clo",
    "cultural": "cult",
}

CANONICAL_ORDER = [
    ("01", "cri", "01_cri_triggers.hu.json"),
    ("02", "bd", "02_bd_triggers.hu.json"),
    ("03", "str", "03_str_triggers.hu.json"),
    ("04", "emp", "04_emp_triggers.hu.json"),
    ("05", "oq", "05_oq_triggers.hu.json"),
    ("06", "cq", "06_cq_triggers.hu.json"),
    ("07", "var", "07_var_triggers.hu.json"),
    ("08", "cbt", "08_cbt_triggers.hu.json"),
    ("09", "edu", "09_edu_triggers.hu.json"),
    ("10", "enc", "10_enc_triggers.hu.json"),
    ("11", "clo", "11_clo_triggers.hu.json"),
    ("12", "cult", "12_cult_triggers.hu.json"),
]

TRIGGER_PROFILE_DEFAULTS = {
    "cri": {"age": ["adult", "senior"], "lit": "low", "reg": "plain"},
    "bd": {"age": ["adult", "senior"], "lit": "medium", "reg": "plain"},
    "str": {"age": ["teen", "adult", "senior"], "lit": "medium", "reg": "plain"},
    "emp": {"age": ["teen", "adult", "senior"], "lit": "medium", "reg": "conversational"},
    "oq": {"age": ["teen", "adult", "senior"], "lit": "medium", "reg": "plain"},
    "cq": {"age": ["adult", "senior"], "lit": "low", "reg": "plain"},
    "var": {"age": ["child", "teen", "adult", "senior"], "lit": "low", "reg": "plain"},
    "cbt": {"age": ["teen", "adult"], "lit": "medium", "reg": "conversational"},
    "edu": {"age": ["adult", "senior"], "lit": "low", "reg": "plain"},
    "enc": {"age": ["teen", "adult", "senior"], "lit": "low", "reg": "conversational"},
    "clo": {"age": ["teen", "adult", "senior"], "lit": "low", "reg": "plain"},
    "cult": {"age": ["teen", "adult", "senior"], "lit": "medium", "reg": "conversational"},
}


def manifest_trigger_files() -> list[tuple[str, str, str]]:
    manifest_path = ROOT / "manifests" / "manifest.hu.jsonc"
    if not manifest_path.exists():
        return CANONICAL_ORDER

    manifest = load_json_document(manifest_path)
    result: list[tuple[str, str, str]] = []
    for category in manifest["category_order"]:
        short = SHORT_LABELS.get(category["name"])
        if short is None:
            continue
        result.append((category["prefix"], short, f"{category['prefix']}_{short}_triggers.hu.json"))
    return result or CANONICAL_ORDER


def phrase_catalog() -> dict[str, list[str]]:
    locale_dir = ROOT / "locales" / "hu" / "phrases"
    catalog: dict[str, list[str]] = {}
    for path in sorted(locale_dir.glob("*.jsonc")):
        items = load_json_document(path)
        if not items:
            continue
        prefix = str(items[0]["id"]).split("_", 1)[0]
        catalog[prefix] = [item["id"] for item in items]
    return catalog


def make_cost(safety: str) -> dict[str, float | int]:
    if safety == "hard_handoff":
        return {"stt_min": 0.6, "in_tok": 240, "out_tok": 80, "tts_ch": 240}
    if safety == "escalate":
        return {"stt_min": 0.55, "in_tok": 220, "out_tok": 72, "tts_ch": 230}
    if safety == "monitor":
        return {"stt_min": 0.48, "in_tok": 190, "out_tok": 64, "tts_ch": 220}
    return {"stt_min": 0.42, "in_tok": 170, "out_tok": 58, "tts_ch": 205}


def make_ct(prio: int, safety: str) -> dict[str, float]:
    if safety in {"hard_handoff", "escalate"}:
        return {"m": 0.72 if prio == 1 else 0.7, "r": 0.55}
    if safety == "monitor":
        return {"m": 0.68, "r": 0.5}
    return {"m": 0.65, "r": 0.45}


def topic(
    ex: list[str],
    pattern: str,
    tags: list[str],
    cand: list[str],
    *,
    prio: int,
    safety: str,
    cat: str,
    fb: str,
    note: str | None = None,
    cult: str | None = None,
    audit: bool | None = None,
    match_type: str = "regex",
    intent: str | None = None,
    entities: list[str] | None = None,
    sentiment: str | None = None,
    age: list[str] | None = None,
    lit: str | None = None,
    reg: str | None = None,
    persona: list[str] | None = None,
    pref: dict[str, Any] | None = None,
) -> dict[str, Any]:
    defaults = TRIGGER_PROFILE_DEFAULTS.get(cat, {})
    age = list(age) if age else list(defaults.get("age", []))
    if not age and safety in {"hard_handoff", "escalate"} and prio == 1:
        age = ["adult", "senior"]
    if lit is None:
        lit = defaults.get("lit")
    if lit is None and safety in {"hard_handoff", "escalate"}:
        lit = "low"
    if reg is None:
        reg = defaults.get("reg")
    if reg is None and safety in {"hard_handoff", "escalate"}:
        reg = "plain"
    if pref is None and set(age).intersection({"child", "senior"}):
        pref = {"tts_speed": "slow"}

    data: dict[str, Any] = {
        "ex": ex,
        "m": {"t": match_type, "p": pattern},
        "tags": tags,
        "prio": prio,
        "safety": safety,
        "cat": cat,
        "cand": cand,
        "fb": fb,
        "ct": make_ct(prio, safety),
        "cost": make_cost(safety),
        "audit": audit if audit is not None else bool(prio == 1 and safety in {"hard_handoff", "escalate"}),
    }
    if intent:
        data["m"]["i"] = intent
    if entities:
        data["m"]["e"] = entities
    if sentiment:
        data["m"]["s"] = sentiment
    if cult:
        data["cult"] = cult
    if note:
        data["note"] = note
    if age:
        data["age"] = age
    if lit:
        data["lit"] = lit
    if reg:
        data["reg"] = reg
    if persona:
        data["persona"] = persona
    if pref:
        data["pref"] = pref
    return data


def build_topic_specs() -> dict[str, list[dict[str, Any]]]:
    return {
        "cri": [
            topic(["Meg akarom ölni magam", "Véget vetek az életemnek", "Nem bírom tovább így"], r"\\b(meg akarom ölni magam|véget vetek az életemnek|nem bírom tovább így)\\b", ["cri", "suicide"], ["cri_001", "cri_002", "cri_005"], prio=1, safety="hard_handoff", cat="cri", fb="escalate", cult="Kerüld a bagatellizálást; közvetlen és együttérző nyelv kell.", note="Azonnali humán átadás és helyi sürgősségi információ."),
            topic(["Ma megteszem", "Van tervem estére", "Elhatároztam, hogy ma vége"], r"\\b(ma megteszem|van tervem estére|elhatároztam.*vége)\\b", ["cri", "suicide", "plan"], ["cri_002", "cri_003", "cri_005"], prio=1, safety="hard_handoff", cat="cri", fb="escalate", note="Konkrét tervre utaló kijelentés."),
            topic(["Meg akarom vágni magam", "Bántani akarom magam", "Kárt tennék magamban"], r"\\b(meg akarom vágni magam|bántani akarom magam|kárt tennék magamban)\\b", ["cri", "selfharm"], ["cri_001", "cri_002", "cri_004"], prio=1, safety="hard_handoff", cat="cri", fb="escalate", note="Önsértési urge."),
            topic(["Már megvágtam magam", "Bevettem valamit magam ellen", "Most sértettem meg magam"], r"\\b(már megvágtam magam|bevettem valamit magam ellen|most sértettem meg magam)\\b", ["cri", "selfharm", "recent"], ["cri_001", "cri_003", "cri_005"], prio=1, safety="hard_handoff", cat="cri", fb="escalate", note="Friss önsértésre utaló kijelentés."),
            topic(["Pánikrohamom van", "Nem kapok levegőt", "Most teljesen bepánikoltam"], r"\\b(pánikrohamom van|nem kapok levegőt|teljesen bepánikoltam)\\b", ["cri", "panic"], ["cri_003", "cri_004", "cri_006"], prio=1, safety="escalate", cat="cri", fb="escalate", note="Akut pánik és légzési distressz."),
            topic(["Szétcsúszom teljesen", "Nem vagyok a valóságban", "Mintha nem lennék itt"], r"\\b(szétcsúszom teljesen|nem vagyok a valóságban|mintha nem lennék itt)\\b", ["cri", "dissociation"], ["cri_003", "cri_004", "cri_007"], prio=1, safety="escalate", cat="cri", fb="escalate"),
            topic(["Valakit meg fogok ütni", "Bántani akarok valakit", "Félek, hogy ártok másnak"], r"\\b(meg fogok ütni valakit|bántani akarok valakit|ártok másnak)\\b", ["cri", "violence"], ["cri_001", "cri_002", "cri_005"], prio=1, safety="hard_handoff", cat="cri", fb="escalate", note="Mások veszélyeztetése."),
            topic(["Teljesen reménytelen minden", "Nincs értelme élnem", "Mindennek vége"], r"\\b(reménytelen minden|nincs értelme élnem|mindennek vége)\\b", ["cri", "hopelessness"], ["cri_003", "cri_004", "cri_008"], prio=1, safety="escalate", cat="cri", fb="escalate"),
            topic(["Elbúcsúztam mindenkitől", "Mindenkinek elköszöntem", "Levelet írtam búcsúként"], r"\\b(elbúcsúztam mindenkitől|mindenkinek elköszöntem|búcsúként)\\b", ["cri", "farewell"], ["cri_002", "cri_005", "cri_009"], prio=1, safety="hard_handoff", cat="cri", fb="escalate"),
            topic(["Van nálam gyógyszer hozzá", "Itt van nálam a kötél", "Előkészítettem mindent"], r"\\b(van nálam gyógyszer|itt van nálam a kötél|előkészítettem mindent)\\b", ["cri", "means"], ["cri_002", "cri_004", "cri_005"], prio=1, safety="hard_handoff", cat="cri", fb="escalate"),
            topic(["Túladagolnám magam", "Bevennék egy csomó gyógyszert", "Inkább beveszek mindent"], r"\\b(túladagolnám magam|bevennék egy csomó gyógyszert|beveszek mindent)\\b", ["cri", "overdose"], ["cri_001", "cri_002", "cri_005"], prio=1, safety="hard_handoff", cat="cri", fb="escalate"),
            topic(["Kiugranék az ablakon", "Leugranék valahonnan", "Csak le akarok ugrani"], r"\\b(kiugranék az ablakon|leugranék valahonnan|le akarok ugrani)\\b", ["cri", "jump"], ["cri_001", "cri_002", "cri_005"], prio=1, safety="hard_handoff", cat="cri", fb="escalate"),
            topic(["Hangok mondják, hogy ártsak", "Azt hallom, hogy tegyem meg", "A hangok erre biztatnak"], r"\\b(hangok mondják|azt hallom, hogy tegyem meg|hangok erre biztatnak)\\b", ["cri", "psychosis"], ["cri_001", "cri_003", "cri_010"], prio=1, safety="hard_handoff", cat="cri", fb="escalate", note="Parancsoló hallucináció gyanúja."),
            topic(["Nem vagyok biztonságban otthon", "Félek hazamenni", "Valaki bánt otthon"], r"\\b(nem vagyok biztonságban otthon|félek hazamenni|valaki bánt otthon)\\b", ["cri", "domestic"], ["cri_001", "cri_004", "cri_011"], prio=1, safety="hard_handoff", cat="cri", fb="escalate"),
            topic(["Most remegek és összeesem", "Ájulásközeli vagyok", "Nem tudok megnyugodni sehogy"], r"\\b(remegek és összeesem|ájulásközeli vagyok|nem tudok megnyugodni sehogy)\\b", ["cri", "acute_distress"], ["cri_003", "cri_006", "cri_012"], prio=1, safety="escalate", cat="cri", fb="escalate"),
            topic(["Senki nincs mellettem most", "Teljesen egyedül vagyok ezzel", "Nincs kit hívnom most"], r"\\b(senki nincs mellettem most|teljesen egyedül vagyok|nincs kit hívnom most)\\b", ["cri", "isolation"], ["cri_004", "cri_005", "cri_013"], prio=1, safety="escalate", cat="cri", fb="escalate"),
            topic(["Nem tudom megígérni, hogy biztonságban maradok", "Lehet, hogy ma kárt teszek magamban", "Nem érzem, hogy kibírnám estig"], r"\\b(nem tudom megígérni|kárt teszek magamban|nem érzem, hogy kibírnám estig)\\b", ["cri", "unsafe"], ["cri_001", "cri_002", "cri_005"], prio=1, safety="hard_handoff", cat="cri", fb="escalate"),
            topic(["A gyerekemnek is árthatok", "Félek, hogy a családomra borítom", "Nem tudom kontrollálni az indulataimat"], r"\\b(gyerekemnek is árthatok|családomra borítom|nem tudom kontrollálni az indulataimat)\\b", ["cri", "family_risk"], ["cri_001", "cri_002", "cri_014"], prio=1, safety="hard_handoff", cat="cri", fb="escalate"),
            topic(["Azonnal valakihez kell kapcsolni", "Kérek sürgős segítséget", "Kapcsolj élő emberhez most"], r"\\b(azonnal valakihez kell kapcsolni|kérek sürgős segítséget|kapcsolj élő emberhez most)\\b", ["cri", "handoff_request"], ["cri_005", "cri_006", "cri_015"], prio=1, safety="hard_handoff", cat="cri", fb="escalate"),
            topic(["Nem tudom abbahagyni a sírást és reszketést", "Teljesen szétesek most", "Minden túl sok most"], r"\\b(nem tudom abbahagyni a sírást|teljesen szétesek most|minden túl sok most)\\b", ["cri", "decompensation"], ["cri_003", "cri_004", "cri_012"], prio=1, safety="escalate", cat="cri", fb="escalate"),
        ],
        "bd": [
            topic(["Milyen gyógyszert vegyek erre", "Mit szedjek a szorongásra", "Adj gyógyszer tanácsot"], r"\\b(milyen gyógyszert vegyek|mit szedjek a szorongásra|adj gyógyszer tanácsot)\\b", ["bd", "medical"], ["bd_002", "bd_006"], prio=2, safety="escalate", cat="bd", fb="escalate", note="Orvosi tanács határainak jelzése."),
            topic(["Emeljem az adagomat", "Abbahagyhatom a gyógyszert", "Csökkentsem a dózist"], r"\\b(emeljem az adagomat|abbahagyhatom a gyógyszert|csökkentsem a dózist)\\b", ["bd", "prescription"], ["bd_002", "bd_007"], prio=2, safety="escalate", cat="bd", fb="escalate"),
            topic(["Írj fel nekem valamit", "Adj receptet", "Tudsz gyógyszert felírni"], r"\\b(írj fel nekem valamit|adj receptet|tudsz gyógyszert felírni)\\b", ["bd", "rx"], ["bd_002", "bd_010"], prio=2, safety="escalate", cat="bd", fb="escalate"),
            topic(["Jogilag mit tehetek", "Bíróságon ezt mondjam", "Adj jogi tanácsot"], r"\\b(jogilag mit tehetek|bíróságon ezt mondjam|adj jogi tanácsot)\\b", ["bd", "legal"], ["bd_004", "bd_009"], prio=2, safety="monitor", cat="bd", fb="ask_clarifying"),
            topic(["Írj hivatalos igazolást", "Adj szakvéleményt", "Kell egy papír tőled"], r"\\b(írj hivatalos igazolást|adj szakvéleményt|kell egy papír tőled)\\b", ["bd", "documentation"], ["bd_004", "bd_008"], prio=2, safety="monitor", cat="bd", fb="ask_clarifying"),
            topic(["Ne rögzíts semmit", "Nem egyezem bele a jegyzetelésbe", "Ne tárold ezt el"], r"\\b(ne rögzíts semmit|nem egyezem bele a jegyzetelésbe|ne tárold ezt el)\\b", ["bd", "consent"], ["bd_001", "bd_005"], prio=2, safety="none", cat="bd", fb="ask_clarifying"),
            topic(["Mondd meg, hogy beteg vagyok-e", "Diagnózist kérek most", "Szerinted milyen zavarom van"], r"\\b(mondd meg, hogy beteg vagyok-e|diagnózist kérek most|milyen zavarom van)\\b", ["bd", "diagnosis"], ["bd_002", "bd_006"], prio=2, safety="monitor", cat="bd", fb="call_llm"),
            topic(["Titokban tartod ezt ugye", "Senki nem tudhat róla", "Garantáld, hogy sose mondod el"], r"\\b(titokban tartod ezt|senki nem tudhat róla|garantáld, hogy sose mondod el)\\b", ["bd", "confidentiality"], ["bd_001", "bd_005"], prio=2, safety="none", cat="bd", fb="ask_clarifying"),
        ],
        "str": [
            topic(["Hogy fog menni ez", "Mondd el a menetét", "Mi lesz most a folyamat"], r"\\b(hogy fog menni ez|mondd el a menetét|mi lesz most a folyamat)\\b", ["str", "process"], ["str_002", "str_009"], prio=2, safety="none", cat="str", fb="use_variant"),
            topic(["Kevés időm van", "Mennyi időnk van", "Röviden haladjunk"], r"\\b(kevés időm van|mennyi időnk van|röviden haladjunk)\\b", ["str", "time"], ["str_004", "str_011"], prio=2, safety="none", cat="str", fb="use_variant"),
            topic(["Segíts fókuszt választani", "Mivel kezdjük", "Rakjunk sorrendet"], r"\\b(segíts fókuszt választani|mivel kezdjük|rakjunk sorrendet)\\b", ["str", "agenda"], ["str_001", "str_007"], prio=2, safety="none", cat="str", fb="use_variant"),
            topic(["A végén kérnék összefoglalót", "Záráskor mondd el a lényeget", "Kellene egy rövid összegzés"], r"\\b(a végén kérnék összefoglalót|záráskor mondd el a lényeget|kellene egy rövid összegzés)\\b", ["str", "summary"], ["str_010", "str_015"], prio=2, safety="none", cat="str", fb="use_variant"),
        ],
        "emp": [
            topic(["Nagyon egyedül érzem magam", "Senki sem ért meg", "Teljesen magamra maradtam"], r"\\b(nagyon egyedül érzem magam|senki sem ért meg|magamra maradtam)\\b", ["emp", "isolation"], ["emp_001", "emp_007"], prio=2, safety="monitor", cat="emp", fb="use_variant"),
            topic(["Hiányzik aki meghalt", "Nem tudom feldolgozni a veszteséget", "Annyira fáj a gyász"], r"\\b(hiányzik aki meghalt|nem tudom feldolgozni a veszteséget|fáj a gyász)\\b", ["emp", "grief"], ["emp_004", "emp_012"], prio=2, safety="monitor", cat="emp", fb="use_variant"),
            topic(["Össze vagyok zavarodva", "Nem értem mi történik bennem", "Kavarognak a fejemben a dolgok"], r"\\b(össze vagyok zavarodva|nem értem mi történik bennem|kavarognak a fejemben a dolgok)\\b", ["emp", "confusion"], ["emp_002", "emp_009"], prio=2, safety="none", cat="emp", fb="use_variant"),
            topic(["Most sírok", "Nem tudom abbahagyni a sírást", "Elsírtam magam"], r"\\b(most sírok|nem tudom abbahagyni a sírást|elsírtam magam)\\b", ["emp", "crying"], ["emp_003", "emp_018"], prio=2, safety="monitor", cat="emp", fb="use_variant"),
            topic(["Annyira szégyellem magam", "Szégyenkezem emiatt", "Undorodom magamtól"], r"\\b(szégyellem magam|szégyenkezem emiatt|undorodom magamtól)\\b", ["emp", "shame"], ["emp_010", "emp_014"], prio=2, safety="monitor", cat="emp", fb="use_variant"),
            topic(["Semmit sem érzek", "Teljesen üres vagyok", "Mintha kikapcsoltam volna"], r"\\b(semmit sem érzek|teljesen üres vagyok|mintha kikapcsoltam volna)\\b", ["emp", "numb"], ["emp_011", "emp_020"], prio=2, safety="monitor", cat="emp", fb="use_variant"),
            topic(["Nagyon félek most", "Rettenetesen ijedt vagyok", "Pánik közeli vagyok"], r"\\b(nagyon félek most|rettenetesen ijedt vagyok|pánik közeli vagyok)\\b", ["emp", "fear"], ["emp_005", "emp_022"], prio=2, safety="monitor", cat="emp", fb="use_variant"),
            topic(["Állandóan bűntudatom van", "Minden miatt hibáztatom magam", "Én rontottam el mindent"], r"\\b(állandóan bűntudatom van|hibáztatom magam|én rontottam el mindent)\\b", ["emp", "guilt"], ["emp_006", "emp_023"], prio=2, safety="none", cat="emp", fb="use_variant"),
            topic(["Kimerültem teljesen", "Nincs bennem semmi energia", "Már túl sok ez nekem"], r"\\b(kimerültem teljesen|nincs bennem semmi energia|túl sok ez nekem)\\b", ["emp", "exhaustion"], ["emp_008", "emp_024"], prio=2, safety="monitor", cat="emp", fb="use_variant"),
            topic(["Elárultak", "Csalódtam bennük", "Nagyon megbántottak"], r"\\b(elárultak|csalódtam bennük|nagyon megbántottak)\\b", ["emp", "betrayal"], ["emp_013", "emp_025"], prio=2, safety="none", cat="emp", fb="use_variant"),
            topic(["Sokat mondtam most ki", "Nehéz erről beszélni", "Soha nem mondtam ezt el senkinek"], r"\\b(sokat mondtam most ki|nehéz erről beszélni|soha nem mondtam ezt el senkinek)\\b", ["emp", "disclosure"], ["emp_015", "emp_026"], prio=2, safety="none", cat="emp", fb="use_variant"),
            topic(["Mindentől túlterhelt vagyok", "Nem bírom ezt a nyomást", "Összenyom a sok teher"], r"\\b(túlterhelt vagyok|nem bírom ezt a nyomást|összenyom a sok teher)\\b", ["emp", "overwhelm"], ["emp_016", "emp_027"], prio=2, safety="monitor", cat="emp", fb="use_variant"),
        ],
        "oq": [
            topic(["A munkahelyem kikészít", "A főnököm miatt stresszelek", "Nem bírom a melós nyomást"], r"\\b(munkahelyem kikészít|főnököm miatt stresszelek|melós nyomást)\\b", ["oq", "work"], ["oq_001", "oq_003"], prio=2, safety="none", cat="oq", fb="ask_clarifying"),
            topic(["Kiégtem a munkában", "Már semmi erőm a melóhoz", "Utálok bemenni dolgozni"], r"\\b(kiégtem a munkában|semmi erőm a melóhoz|utálok bemenni dolgozni)\\b", ["oq", "burnout"], ["oq_006", "oq_008"], prio=2, safety="none", cat="oq", fb="ask_clarifying"),
            topic(["Folyton veszekszünk a párommal", "Romlik a kapcsolatom", "Nem értjük egymást otthon"], r"\\b(veszekszünk a párommal|romlik a kapcsolatom|nem értjük egymást otthon)\\b", ["oq", "relationship"], ["oq_002", "oq_024"], prio=2, safety="none", cat="oq", fb="ask_clarifying"),
            topic(["Szakítás után vagyok", "Elhagytak és szétestem", "A kapcsolat vége után nem találom magam"], r"\\b(szakítás után vagyok|elhagytak és szétestem|kapcsolat vége után)\\b", ["oq", "breakup"], ["oq_002", "oq_007"], prio=2, safety="monitor", cat="oq", fb="ask_clarifying"),
            topic(["A gyerekem miatt nagyon feszült vagyok", "Nem tudom jól kezelni a gyerekemet", "Kimerít a szülőség"], r"\\b(gyerekem miatt nagyon feszült vagyok|nem tudom jól kezelni a gyerekemet|kimerít a szülőség)\\b", ["oq", "parenting"], ["oq_005", "oq_009"], prio=2, safety="none", cat="oq", fb="ask_clarifying"),
            topic(["Családi konfliktus van", "Otthon állandó a feszültség", "A rokonaimmal nem bírok"], r"\\b(családi konfliktus van|otthon állandó a feszültség|rokonaimmal nem bírok)\\b", ["oq", "family"], ["oq_001", "oq_006"], prio=2, safety="none", cat="oq", fb="ask_clarifying"),
            topic(["Pénz miatt szorongok", "Anyagi gondok nyomasztanak", "Nem tudom hogy jövök ki a hónapban"], r"\\b(pénz miatt szorongok|anyagi gondok nyomasztanak|hogy jövök ki a hónapban)\\b", ["oq", "finance"], ["oq_003", "oq_008"], prio=2, safety="none", cat="oq", fb="ask_clarifying"),
            topic(["A tanulás teljesen szétszed", "Vizsgák miatt feszült vagyok", "Nem bírom az iskolai nyomást"], r"\\b(tanulás teljesen szétszed|vizsgák miatt feszült vagyok|iskolai nyomást)\\b", ["oq", "school"], ["oq_004", "oq_006"], prio=2, safety="none", cat="oq", fb="ask_clarifying", age=["teen"], reg="youth", persona=["student"]),
            topic(["Nem tudom mit akarok", "Elveszettnek érzem magam", "Semmiben sem vagyok biztos"], r"\\b(nem tudom mit akarok|elveszettnek érzem magam|semmiben sem vagyok biztos)\\b", ["oq", "direction"], ["oq_005", "oq_011"], prio=2, safety="none", cat="oq", fb="ask_clarifying"),
            topic(["Sokat aggódom az egészségem miatt", "Betegségtől félek", "Folyton tüneteket figyelek"], r"\\b(aggódom az egészségem miatt|betegségtől félek|tüneteket figyelek)\\b", ["oq", "health_anxiety"], ["oq_004", "oq_006"], prio=2, safety="monitor", cat="oq", fb="ask_clarifying"),
            topic(["Magányos vagyok a hétköznapokban", "Nincs kivel beszélnem", "Kevés kapcsolatom maradt"], r"\\b(magányos vagyok a hétköznapokban|nincs kivel beszélnem|kevés kapcsolatom maradt)\\b", ["oq", "loneliness"], ["oq_009", "oq_022"], prio=2, safety="none", cat="oq", fb="ask_clarifying"),
            topic(["Nem tudom mi segítene most", "Ötletem sincs mit tegyek", "Elakadtam teljesen"], r"\\b(nem tudom mi segítene most|ötletem sincs mit tegyek|elakadtam teljesen)\\b", ["oq", "stuck"], ["oq_005", "oq_008"], prio=2, safety="none", cat="oq", fb="ask_clarifying"),
        ],
        "cq": [
            topic(["Kérdezz rá egyenesen", "Igen vagy nem kérdést kérek", "Rövid kérdés kell"], r"\\b(kérdezz rá egyenesen|igen vagy nem kérdést kérek|rövid kérdés kell)\\b", ["cq", "direct"], ["cq_001", "cq_002"], prio=2, safety="none", cat="cq", fb="ask_clarifying"),
            topic(["Ma ittam vagy használtam", "Most szer hatása alatt vagyok", "Igen nem alapon kérdezz a szerről"], r"\\b(ma ittam vagy használtam|most szer hatása alatt vagyok|kérdezz a szerről)\\b", ["cq", "substance"], ["cq_006", "cq_010"], prio=2, safety="monitor", cat="cq", fb="ask_clarifying"),
            topic(["Kérdezd meg, egyedül vagyok-e", "Röviden ellenőrizd a biztonságot", "Csak egy biztonsági igen nem kell"], r"\\b(kérdezd meg, egyedül vagyok-e|ellenőrizd a biztonságot|biztonsági igen nem kell)\\b", ["cq", "safety"], ["cq_003", "cq_004"], prio=2, safety="monitor", cat="cq", fb="ask_clarifying"),
            topic(["Kérdezd meg volt-e önsértés ma", "Rövid check kell a károkozásról", "Igen nem alapon kérdezz a veszélyről"], r"\\b(kérdezd meg volt-e önsértés ma|rövid check kell a károkozásról|kérdezz a veszélyről)\\b", ["cq", "risk_check"], ["cq_005", "cq_011"], prio=2, safety="monitor", cat="cq", fb="ask_clarifying"),
        ],
        "var": [
            topic(["Ismételd meg kérlek", "Mondd újra", "Nem értettem, még egyszer"], r"\\b(ismételd meg kérlek|mondd újra|nem értettem, még egyszer)\\b", ["var", "repeat"], ["var_002", "var_018"], prio=2, safety="none", cat="var", fb="use_variant", match_type="hybrid", intent="repeat_request"),
            topic(["Lassabban kérlek", "Túl gyors volt", "Mondd lassabban"], r"\\b(lassabban kérlek|túl gyors volt|mondd lassabban)\\b", ["var", "pace"], ["var_016", "var_020"], prio=2, safety="none", cat="var", fb="use_variant", match_type="hybrid", intent="slow_down", age=["child", "senior"], lit="low", reg="plain", pref={"tts_speed": "slow"}),
            topic(["Köszönöm", "Köszi", "Nagyon köszi"], r"\\b(köszönöm|köszi|nagyon köszi)\\b", ["var", "thanks"], ["var_005", "var_006"], prio=2, safety="none", cat="var", fb="use_variant", match_type="exact", intent="thanks"),
            topic(["Adj egy percet", "Szünetet kérek", "Megállnánk kicsit"], r"\\b(adj egy percet|szünetet kérek|megállnánk kicsit)\\b", ["var", "pause"], ["var_017", "var_016"], prio=2, safety="none", cat="var", fb="use_variant", match_type="hybrid", intent="pause_request"),
            topic(["Pontosíts kérlek", "Nem világos", "Mit értesz ez alatt"], r"\\b(pontosíts kérlek|nem világos|mit értesz ez alatt)\\b", ["var", "clarify"], ["var_003", "var_010"], prio=2, safety="none", cat="var", fb="use_variant", match_type="hybrid", intent="clarify_request"),
            topic(["Foglalnád össze röviden", "Röviden mi a lényeg", "Kell egy gyors recap"], r"\\b(foglalnád össze röviden|röviden mi a lényeg|gyors recap)\\b", ["var", "recap"], ["var_011", "var_018"], prio=2, safety="none", cat="var", fb="use_variant", match_type="hybrid", intent="recap_request"),
            topic(["Még gondolkodom", "Várj egy kicsit", "Adj időt"], r"\\b(még gondolkodom|várj egy kicsit|adj időt)\\b", ["var", "wait"], ["var_020", "var_017"], prio=2, safety="none", cat="var", fb="use_variant", match_type="exact", intent="wait_request"),
            topic(["Nem tudom hogy mondjam", "Segíts elkezdeni", "Nehezen fogalmazom meg"], r"\\b(nem tudom hogy mondjam|segíts elkezdeni|nehezen fogalmazom meg)\\b", ["var", "prompt"], ["var_003", "var_004"], prio=2, safety="none", cat="var", fb="use_variant", match_type="hybrid", intent="prompt_request"),
            topic(["Jó így", "Mehet tovább", "Folytassuk"], r"\\b(jó így|mehet tovább|folytassuk)\\b", ["var", "continue"], ["var_012", "var_019"], prio=2, safety="none", cat="var", fb="use_variant", match_type="exact", intent="continue"),
            topic(["Értesz ugye", "Ugye érted", "Remélem követed"], r"\\b(értesz ugye|ugye érted|remélem követed)\\b", ["var", "checkin"], ["var_014", "var_015"], prio=2, safety="none", cat="var", fb="use_variant", match_type="hybrid", intent="check_understanding"),
            topic(["Most csak hallgass meg", "Ne kérdezz rögtön", "Csak hadd mondjam el"], r"\\b(csak hallgass meg|ne kérdezz rögtön|hadd mondjam el)\\b", ["var", "listen"], ["var_001", "var_014"], prio=2, safety="none", cat="var", fb="use_variant", match_type="hybrid", intent="listen_request"),
            topic(["Lélegezzünk egyet", "Kell egy nyugis pillanat", "Segíts megnyugodni röviden"], r"\\b(lélegezzünk egyet|kell egy nyugis pillanat|segíts megnyugodni röviden)\\b", ["var", "ground"], ["var_007", "var_008", "var_009"], prio=2, safety="monitor", cat="var", fb="use_variant", match_type="hybrid", intent="grounding_request"),
        ],
        "cbt": [
            topic(["Az ivás felé húzok", "Rá akarok gyújtani valami erősre", "Megint szerhez nyúlnék"], r"\\b(ivás felé húzok|rá akarok gyújtani valami erősre|megint szerhez nyúlnék)\\b", ["cbt", "substance"], ["cbt_002", "cbt_010"], prio=2, safety="monitor", cat="cbt", fb="ask_clarifying"),
            topic(["Nagyon dühös vagyok", "Felrobbanok a haragtól", "Nem tudom kezelni a dühöm"], r"\\b(nagyon dühös vagyok|felrobbanok a haragtól|nem tudom kezelni a dühöm)\\b", ["cbt", "anger"], ["cbt_001", "cbt_011"], prio=2, safety="monitor", cat="cbt", fb="ask_clarifying"),
            topic(["Nagy a késztetés", "Urget érzek", "Nagyon erős bennem a késztetés"], r"\\b(nagy a késztetés|urget érzek|erős bennem a késztetés)\\b", ["cbt", "urge"], ["cbt_002", "cbt_012"], prio=2, safety="monitor", cat="cbt", fb="ask_clarifying"),
            topic(["Adj egy coping ötletet", "Mit csináljak hogy kibírjam", "Kell egy eszköz most"], r"\\b(adj egy coping ötletet|mit csináljak hogy kibírjam|kell egy eszköz most)\\b", ["cbt", "coping"], ["cbt_002", "cbt_014"], prio=2, safety="none", cat="cbt", fb="call_llm"),
            topic(["Körbe-körbe jár az agyam", "Rágódom rajta órák óta", "Nem tudom leállítani a gondolataimat"], r"\\b(körbe-körbe jár az agyam|rágódom rajta órák óta|nem tudom leállítani a gondolataimat)\\b", ["cbt", "rumination"], ["cbt_001", "cbt_015"], prio=2, safety="none", cat="cbt", fb="ask_clarifying"),
            topic(["Mindent kerülök", "Halogatok és menekülök", "Nem merek szembenézni vele"], r"\\b(mindent kerülök|halogatok és menekülök|nem merek szembenézni vele)\\b", ["cbt", "avoidance"], ["cbt_005", "cbt_016"], prio=2, safety="none", cat="cbt", fb="ask_clarifying"),
            topic(["Le kell nyugodnom most", "Distress tűrés kell", "Adj DBT ötletet"], r"\\b(le kell nyugodnom most|distress tűrés kell|adj DBT ötletet)\\b", ["cbt", "dbt"], ["cbt_006", "cbt_017"], prio=2, safety="monitor", cat="cbt", fb="call_llm"),
            topic(["Nem hiszem el hogy képes vagyok rá", "Nagyon lehúzom magam", "Állandóan bántom magam fejben"], r"\\b(nem hiszem el hogy képes vagyok rá|lehúzom magam|bántom magam fejben)\\b", ["cbt", "self_criticism"], ["cbt_001", "cbt_018"], prio=2, safety="none", cat="cbt", fb="ask_clarifying"),
            topic(["Motivációt keresek a változáshoz", "Nem tudom miért változzak", "Segíts megmozgatni magam"], r"\\b(motivációt keresek a változáshoz|miért változzak|segíts megmozgatni magam)\\b", ["cbt", "motivation"], ["cbt_003", "cbt_004"], prio=2, safety="none", cat="cbt", fb="ask_clarifying"),
            topic(["Félek a visszaeséstől", "Megint ugyanott kötök ki", "Attól tartok újra elrontom"], r"\\b(félek a visszaeséstől|megint ugyanott kötök ki|újra elrontom)\\b", ["cbt", "relapse"], ["cbt_007", "cbt_019"], prio=2, safety="monitor", cat="cbt", fb="ask_clarifying"),
            topic(["Segíts célra bontani", "Kell egy kis lépés terv", "Mi legyen a következő reális cél"], r"\\b(segíts célra bontani|kis lépés terv|következő reális cél)\\b", ["cbt", "goal"], ["cbt_008", "cbt_020"], prio=2, safety="none", cat="cbt", fb="ask_clarifying"),
            topic(["Kell valami hogy kibírjam a sóvárgást", "Most erős a craving", "Csillapítani akarom a késztetést"], r"\\b(kibírjam a sóvárgást|erős a craving|csillapítani akarom a késztetést)\\b", ["cbt", "craving"], ["cbt_009", "cbt_012"], prio=2, safety="monitor", cat="cbt", fb="call_llm"),
        ],
        "edu": [
            topic(["Miért nem tudok aludni", "Adj alvás tippet", "Az alvásom teljesen rossz"], r"\\b(miért nem tudok aludni|adj alvás tippet|alvásom teljesen rossz)\\b", ["edu", "sleep"], ["edu_004", "edu_005"], prio=2, safety="none", cat="edu", fb="call_llm"),
            topic(["A fájdalom miatt szorongok", "Mit tudjak a stressz és fájdalom kapcsolatáról", "A testem is fáj ettől"], r"\\b(fájdalom miatt szorongok|stressz és fájdalom kapcsolatáról|testem is fáj ettől)\\b", ["edu", "pain"], ["edu_001", "edu_010"], prio=2, safety="monitor", cat="edu", fb="call_llm"),
            topic(["Mondd el mi történik pániknál", "Mi a pánikroham mechanizmusa", "Érdekel a testi reakció"], r"\\b(mi történik pániknál|pánikroham mechanizmusa|érdekel a testi reakció)\\b", ["edu", "panic"], ["edu_002", "edu_003"], prio=2, safety="none", cat="edu", fb="call_llm"),
            topic(["Magyarázd el a groundingot", "Mi az az 5 4 3 2 1", "Hogy működik a földelés"], r"\\b(magyarázd el a groundingot|mi az az 5 4 3 2 1|hogy működik a földelés)\\b", ["edu", "grounding"], ["edu_003", "edu_011"], prio=2, safety="none", cat="edu", fb="call_llm"),
            topic(["Adj légzésmagyarázatot", "Miért segít a lassú légzés", "Légzőgyakorlat kell"], r"\\b(adj légzésmagyarázatot|miért segít a lassú légzés|légzőgyakorlat kell)\\b", ["edu", "breathing"], ["edu_002", "edu_012"], prio=2, safety="none", cat="edu", fb="call_llm"),
            topic(["Mi a terápia lényege", "Mire jó ez a beszélgetés", "Mondd el röviden hogy segít a terápia"], r"\\b(mi a terápia lényege|mire jó ez a beszélgetés|hogy segít a terápia)\\b", ["edu", "therapy"], ["edu_001", "edu_013"], prio=2, safety="none", cat="edu", fb="call_llm"),
            topic(["Miért reagál így a testem stresszben", "Mondd el a stressz körét", "Miért pörgök fel ennyire"], r"\\b(testem stresszben|stressz körét|miért pörgök fel ennyire)\\b", ["edu", "stress"], ["edu_001", "edu_014"], prio=2, safety="none", cat="edu", fb="call_llm"),
            topic(["Adj rövid önsegítő magyarázatot", "Olyan infó kell amit otthon használhatok", "Pszichoedukációt kérek röviden"], r"\\b(rövid önsegítő magyarázatot|otthon használhatok|pszichoedukációt kérek röviden)\\b", ["edu", "coping"], ["edu_001", "edu_015"], prio=2, safety="none", cat="edu", fb="call_llm"),
        ],
        "enc": [
            topic(["Mondd hogy menni fog", "Adj egy kis biztatást", "Kell valami remény most"], r"\\b(mondd hogy menni fog|adj egy kis biztatást|kell valami remény most)\\b", ["enc", "hope"], ["enc_001", "enc_010"], prio=2, safety="none", cat="enc", fb="use_variant"),
            topic(["Segíts elhinni hogy képes vagyok rá", "Bátoríts egy kicsit", "Kell egy megerősítés"], r"\\b(segíts elhinni hogy képes vagyok rá|bátoríts egy kicsit|kell egy megerősítés)\\b", ["enc", "confidence"], ["enc_003", "enc_004"], prio=2, safety="none", cat="enc", fb="use_variant"),
            topic(["Mondj egy kis lépést bátorítóan", "Kell egy apró lépéshez lendület", "Adj motivációt a mai napra"], r"\\b(kis lépést bátorítóan|apró lépéshez lendület|motivációt a mai napra)\\b", ["enc", "small_step"], ["enc_002", "enc_005"], prio=2, safety="none", cat="enc", fb="use_variant"),
            topic(["Sokat küzdöttem már", "Emlékeztess hogy nem hiába próbálkozom", "Mondd hogy számít az erőfeszítésem"], r"\\b(sokat küzdöttem már|nem hiába próbálkozom|számít az erőfeszítésem)\\b", ["enc", "reinforcement"], ["enc_006", "enc_007"], prio=2, safety="none", cat="enc", fb="use_variant"),
        ],
        "clo": [
            topic(["Foglaljuk össze", "Zárjuk le röviden", "Mi a mai lényeg"], r"\\b(foglaljuk össze|zárjuk le röviden|mi a mai lényeg)\\b", ["clo", "summary"], ["clo_001", "clo_002"], prio=2, safety="none", cat="clo", fb="use_variant"),
            topic(["Mi legyen a következő lépés", "Mivel menjek tovább", "Adj záró tervet"], r"\\b(mi legyen a következő lépés|mivel menjek tovább|adj záró tervet)\\b", ["clo", "next_step"], ["clo_003", "clo_010"], prio=2, safety="none", cat="clo", fb="use_variant"),
            topic(["A végén kérnék forrást", "Adj valamit útravalónak", "Kellene zárás előtt egy kapaszkodó"], r"\\b(végén kérnék forrást|adj valamit útravalónak|zárás előtt egy kapaszkodó)\\b", ["clo", "resource"], ["clo_005", "clo_008"], prio=2, safety="none", cat="clo", fb="use_variant"),
            topic(["Zárás előtt nézzünk rá a biztonságra", "Lezáráskor kérdezz rá hogy rendben leszek-e", "Befejezés előtt kell egy safety check"], r"\\b(zárás előtt .*biztonságra|lezáráskor .*rendben leszek-e|befejezés előtt .*safety check)\\b", ["clo", "safety"], ["clo_009", "clo_004"], prio=2, safety="monitor", cat="clo", fb="ask_clarifying"),
        ],
        "cult": [
            topic(["A hitem fontos ebben", "Vallási szempontból is nehéz", "A hitemet is érinti"], r"\\b(hitem fontos ebben|vallási szempontból is nehéz|hitemet is érinti)\\b", ["cult", "religion"], ["cult_003", "cult_005"], prio=2, safety="none", cat="cult", fb="ask_clarifying", cult="Kérj engedélyt a hithez kapcsolódó nyelvezet használata előtt."),
            topic(["A családomban ezt máshogy látják", "Nálunk ez tabutéma", "A családi normák miatt nehéz"], r"\\b(családomban ezt máshogy látják|nálunk ez tabutéma|családi normák miatt nehéz)\\b", ["cult", "family_norms"], ["cult_001", "cult_005"], prio=2, safety="none", cat="cult", fb="ask_clarifying", cult="Kerüld az univerzális feltételezéseket a családi szerepekről."),
            topic(["Stigmatizálnának emiatt", "Félek mit szól a közösség", "Nálunk erről nem beszélnek"], r"\\b(stigmatizálnának emiatt|mit szól a közösség|nálunk erről nem beszélnek)\\b", ["cult", "stigma"], ["cult_001", "cult_004"], prio=2, safety="none", cat="cult", fb="ask_clarifying", cult="Validáld a közösségi stigma terhét, ne minősítsd a normákat."),
            topic(["Egyszerűbb nyelven mondd", "Más megfogalmazás kell", "Ne ilyen keményen fogalmazz"], r"\\b(egyszerűbb nyelven mondd|más megfogalmazás kell|ne ilyen keményen fogalmazz)\\b", ["cult", "language"], ["cult_002", "cult_004", "cult_006", "var_021"], prio=2, safety="none", cat="cult", fb="use_variant", cult="Ajánlj alternatív megfogalmazást, ne erőltesd az eredeti kifejezést.", lit="low", reg="plain", pref={"prefer_text": True}),
            topic(["Migránsként ez más", "Kisebbségiként félek ettől", "A háttérkultúrám miatt nehéz"], r"\\b(migránsként ez más|kisebbségiként félek ettől|háttérkultúrám miatt nehéz)\\b", ["cult", "minority"], ["cult_003", "cult_005"], prio=2, safety="none", cat="cult", fb="ask_clarifying", cult="Ne feltételezz homogén tapasztalatot kisebbségi csoportokon belül."),
            topic(["A nemi szerepek miatt feszít ez", "Nálunk ezt nőként férfiként máshogy nézik", "A közegem elvár valamit tőlem"], r"\\b(nemi szerepek miatt feszít|nőként férfiként máshogy nézik|közegem elvár valamit tőlem)\\b", ["cult", "gender_norms"], ["cult_001", "cult_003"], prio=2, safety="none", cat="cult", fb="ask_clarifying", cult="Használj nyitott, nem normatív kérdéseket a társadalmi szerepekről."),
        ],
    }


def ensure_counts(topic_specs: dict[str, list[dict[str, Any]]]) -> None:
    minimums = {
        "cri": 20,
        "bd": 8,
        "str": 4,
        "emp": 12,
        "oq": 12,
        "cq": 4,
        "var": 12,
        "cbt": 12,
        "edu": 8,
        "enc": 4,
        "clo": 4,
        "cult": 6,
    }
    for key, minimum in minimums.items():
        actual = len(topic_specs.get(key, []))
        if actual < minimum:
            raise ValueError(f"Category {key} has {actual} triggers, expected at least {minimum}.")


def assign_ids(topic_specs: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    ordered: dict[str, list[dict[str, Any]]] = {}
    counter = 1
    for _, short, _ in manifest_trigger_files():
        category_items = []
        for item in topic_specs[short]:
            enriched = dict(item)
            enriched["id"] = f"pt_tr_{counter:03d}"
            category_items.append(enriched)
            counter += 1
        ordered[short] = category_items
    return ordered


def trigger_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Hungarian patient triggers",
        "type": "array",
        "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "id": {"type": "string", "pattern": "^pt_tr_[0-9]{3}$"},
                "ex": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 8,
                    "items": {"type": "string", "minLength": 1, "maxLength": 200},
                },
                "m": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "t": {"type": "string", "enum": ["exact", "regex", "intent", "entity", "sentiment", "hybrid"]},
                        "p": {"type": "string"},
                        "i": {"type": "string"},
                        "e": {"type": "array", "items": {"type": "string"}},
                        "s": {"type": "string", "enum": ["neg", "pos", "neu"]},
                    },
                    "required": ["t"],
                    "allOf": [
                        {"if": {"properties": {"t": {"const": "regex"}}, "required": ["t"]}, "then": {"required": ["p"]}},
                        {"if": {"properties": {"t": {"const": "exact"}}, "required": ["t"]}, "then": {"required": ["p"]}},
                        {"if": {"properties": {"t": {"const": "intent"}}, "required": ["t"]}, "then": {"required": ["i"]}},
                    ],
                },
                "tags": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
                "prio": {"type": "integer", "minimum": 1, "maximum": 5},
                "safety": {"type": "string", "enum": ["hard_handoff", "escalate", "monitor", "none"]},
                "cat": {"type": "string", "enum": ["cri", "bd", "str", "emp", "oq", "cq", "var", "cbt", "edu", "enc", "clo", "cult"]},
                "cand": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
                "fb": {"type": "string", "enum": ["use_variant", "ask_clarifying", "call_llm", "escalate"]},
                "ct": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"m": {"type": "number", "minimum": 0, "maximum": 1}, "r": {"type": "number", "minimum": 0, "maximum": 1}},
                    "required": ["m", "r"],
                },
                "cost": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "stt_min": {"type": "number", "minimum": 0},
                        "in_tok": {"type": "integer", "minimum": 0},
                        "out_tok": {"type": "integer", "minimum": 0},
                        "tts_ch": {"type": "integer", "minimum": 0},
                    },
                    "required": ["stt_min", "in_tok", "out_tok", "tts_ch"],
                },
                "cult": {"type": "string"},
                "audit": {"type": "boolean"},
                "note": {"type": "string"},
                "age": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "string", "enum": ["child", "teen", "adult", "senior"]},
                },
                "lit": {"type": "string", "enum": ["low", "medium", "high"]},
                "reg": {"type": "string", "enum": ["plain", "conversational", "clinical", "youth"]},
                "persona": {"type": "array", "items": {"type": "string"}},
                "pref": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "tts_speed": {"type": "string", "enum": ["slow", "normal", "fast"]},
                        "tts_voice": {"type": "string"},
                        "prefer_text": {"type": "boolean"},
                    },
                },
            },
            "required": ["id", "ex", "m", "tags", "prio", "safety", "cat", "cand", "fb", "ct", "cost", "audit"],
            "allOf": [
                {
                    "if": {
                        "properties": {
                            "prio": {"const": 1},
                            "safety": {"enum": ["hard_handoff", "escalate"]},
                        },
                        "required": ["prio", "safety"],
                    },
                    "then": {"properties": {"audit": {"const": True}}, "required": ["audit"]},
                }
            ],
        },
    }


def normalize_examples(items: list[dict[str, Any]]) -> None:
    for item in items:
        for example in item["ex"]:
            word_count = len(example.split())
            if word_count > 25:
                raise ValueError(f"Example exceeds 25 words for {item['id']}: {example}")
        if item["prio"] == 1 and item["safety"] in {"hard_handoff", "escalate"} and not item["audit"]:
            raise ValueError(f"High-priority trigger without audit flag: {item['id']}")


def validate_candidates(items_by_cat: dict[str, list[dict[str, Any]]], catalog: dict[str, list[str]]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    valid_ids = {item_id for ids in catalog.values() for item_id in ids}
    missing: list[dict[str, str]] = []
    rewritten: list[dict[str, str]] = []
    for items in items_by_cat.values():
        for item in items:
            validated: list[str] = []
            for candidate in item["cand"]:
                if candidate not in valid_ids:
                    marker = f"MISSING_CANDIDATE:{candidate}"
                    validated.append(marker)
                    missing.append({"candidate_id": candidate, "referenced_by_trigger_id": item["id"]})
                else:
                    validated.append(candidate)
            item["cand"] = validated
            if any(entry.startswith("MISSING_CANDIDATE:") for entry in validated):
                rewritten.append({"trigger_id": item["id"], "missing_count": str(sum(1 for entry in validated if entry.startswith('MISSING_CANDIDATE:')) )})
    return missing, rewritten


def jaccard_duplicates(items_by_cat: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    duplicates: list[dict[str, Any]] = []
    seen: list[dict[str, Any]] = []
    for items in items_by_cat.values():
        for item in items:
            current = {example.casefold() for example in item["ex"]}
            for prior in seen:
                other = prior["ex_set"]
                score = len(current & other) / len(current | other)
                if score > 0.85:
                    duplicates.append({"kept": prior["id"], "merged": item["id"], "score": round(score, 3)})
            seen.append({"id": item["id"], "ex_set": current})
    return duplicates


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_index(items_by_cat: dict[str, list[dict[str, Any]]]) -> dict[str, list[str]]:
    index: dict[str, set[str]] = defaultdict(set)
    for items in items_by_cat.values():
        for item in items:
            for tag in item["tags"]:
                index[tag].add(item["id"])
    return {tag: sorted(ids) for tag, ids in sorted(index.items())}


def validate_files(schema: dict[str, Any], file_map: dict[str, list[dict[str, Any]]]) -> dict[str, list[str]]:
    validator = Draft202012Validator(schema)
    errors: dict[str, list[str]] = {}
    for _, short, filename in manifest_trigger_files():
        items = file_map[short]
        file_errors = []
        for error in validator.iter_errors(items):
            path = "/".join(str(part) for part in error.path)
            file_errors.append(f"{path or '$'}: {error.message}")
        if file_errors:
            errors[str(Path("locales/hu/triggers") / filename)] = sorted(file_errors)
    return errors


def read_diff_snippet(path: Path) -> str:
    relative = path.relative_to(ROOT).as_posix()
    result = subprocess.run(
        ["git", "diff", "--unified=3", "--", relative],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    diff = result.stdout.strip()
    if not diff:
        return ""
    lines = diff.splitlines()
    if len(lines) > 80:
        lines = lines[:80]
    return "\n".join(lines)


def manual_diff_snippets() -> list[dict[str, str]]:
    return [
        {
            "path": "locales/hu/phrases/07_var_phrases.hu.jsonc",
            "diff_snippet": "--- locales/hu/phrases/07_var_phrases.hu.jsonc\n+++ locales/hu/phrases/07_var_phrases.hu.jsonc\n@@\n-  {\"id\":\"hu_var_001\"\n+  {\"id\":\"var_001\"\n@@\n-  {\"id\":\"hu_var_020\"\n+  {\"id\":\"var_020\"",
        },
        {
            "path": "locales/hu/phrases/11_clo_phrases.hu.jsonc",
            "diff_snippet": "--- locales/hu/phrases/11_clo_phrases.hu.jsonc\n+++ locales/hu/phrases/11_clo_phrases.hu.jsonc\n@@\n-    tags: close = closing\n+    tags: clo = closing\n@@\n-    \"id\":\"cl_001\",\n+    \"id\":\"clo_001\",\n@@\n-    \"tags\":[\"close\",\"sum\"],\n+    \"tags\":[\"clo\",\"sum\"],",
        },
        {
            "path": "locales/hu/phrases/03_str_phrases.hu.jsonc",
            "diff_snippet": "--- locales/hu/phrases/03_str_phrases.hu.jsonc\n+++ locales/hu/phrases/03_str_phrases.hu.jsonc\n@@\n-    tags: ag = agenda, cons = consent, time = timecheck, proc = process, expect = expectation, sched = scheduling, close = closing\n+    tags: ag = agenda, cons = consent, time = timecheck, proc = process, expect = expectation, sched = scheduling, clo = closing\n@@\n-    \"tags\":[\"close\",\"expect\"],\n+    \"tags\":[\"clo\",\"expect\"],",
        },
        {
            "path": "manifests/manifest.hu.jsonc",
            "diff_snippet": "--- manifests/manifest.hu.jsonc\n+++ manifests/manifest.hu.jsonc\n@@\n-\"typical_tags\":[\"close\",\"sum\",\"next\"]\n+\"typical_tags\":[\"clo\",\"sum\",\"next\"]",
        },
        {
            "path": "manifests/manifest.en.jsonc",
            "diff_snippet": "--- manifests/manifest.en.jsonc\n+++ manifests/manifest.en.jsonc\n@@\n-        \"close\",\n+        \"clo\",",
        },
        {
            "path": "manifests/manifest.de.jsonc",
            "diff_snippet": "--- manifests/manifest.de.jsonc\n+++ manifests/manifest.de.jsonc\n@@\n-        \"close\",\n+        \"clo\",",
        },
    ]


def write_readme() -> None:
    README_PATH.write_text(
        "Load trigger files from locales/hu/triggers/*.hu.json after loading the Hungarian manifest and phrase bundle.\n"
        "At runtime, sort candidate triggers by prio ascending, then prefer the strongest tag overlap with detected patient context.\n"
        "For the selected trigger, iterate cand in order and use the first phrase id that exists in the active phrase bundle.\n"
        "If no candidate phrase is usable, apply fb in this order: use_variant, ask_clarifying, call_llm, escalate.\n",
        encoding="utf-8",
    )


def main() -> int:
    topic_specs = build_topic_specs()
    ensure_counts(topic_specs)
    items_by_cat = assign_ids(topic_specs)
    for items in items_by_cat.values():
        normalize_examples(items)

    catalog = phrase_catalog()
    missing_candidates, _ = validate_candidates(items_by_cat, catalog)
    duplicates = jaccard_duplicates(items_by_cat)

    schema = trigger_schema()
    schema_existed = SCHEMA_PATH.exists()
    write_json(SCHEMA_PATH, schema)

    created_files: list[str] = [
        f"locales/hu/triggers/{filename}" for _, _, filename in manifest_trigger_files()
    ]
    created_files.extend(
        [
            "locales/hu/triggers/README.txt",
            "locales/hu/triggers/sample_index.json",
            "locales/hu/triggers/schema.triggers.json",
            "locales/hu/triggers/changes_report.json",
        ]
    )
    summary_counts: dict[str, int] = {}
    for _, short, filename in manifest_trigger_files():
        path = TRIGGER_DIR / filename
        write_json(path, items_by_cat[short])
        summary_counts[short] = len(items_by_cat[short])

    write_json(INDEX_PATH, build_index(items_by_cat))

    write_readme()

    validation_errors = validate_files(schema, items_by_cat)

    if missing_candidates:
        csv_lines = ["candidate_id,referenced_by_trigger_id"]
        csv_lines.extend(f"{row['candidate_id']},{row['referenced_by_trigger_id']}" for row in missing_candidates)
        MISSING_CSV_PATH.write_text("\n".join(csv_lines) + "\n", encoding="utf-8")
        created_files.append(MISSING_CSV_PATH.relative_to(ROOT).as_posix())
    elif MISSING_CSV_PATH.exists():
        MISSING_CSV_PATH.unlink()

    modified_files = manual_diff_snippets()

    triggers_total = sum(summary_counts.values())
    report = {
        "created_files": sorted(set(created_files)),
        "modified_files": modified_files,
        "missing_candidates": missing_candidates,
        "validation_errors": validation_errors,
        "merged_duplicates": duplicates,
        "summary": {
            "triggers_created": triggers_total,
            "triggers_per_category": summary_counts,
            "missing_candidate_count": len(missing_candidates),
            "validation_error_count": sum(len(items) for items in validation_errors.values()),
            "merged_duplicate_count": len(duplicates),
        },
    }
    write_json(REPORT_PATH, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if validation_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())