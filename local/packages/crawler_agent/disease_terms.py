from __future__ import annotations

DISEASE_ALIASES: dict[str, list[str]] = {
    "ebola": ["ebola", "ebola virus disease", "EVD"],
    "norovirus": [
        "norovirus",
        "诺如病毒",
        "norwalk virus",
        "norwalk-like virus",
        "norwalk-like viruses",
        "acute gastroenteritis",
        "viral gastroenteritis",
        "gastroenteritis outbreak",
        "stomach flu",
        "winter vomiting bug",
        "winter vomiting disease",
        "noro virus",
        "norovirus outbreak",
    ],
    "influenza": [
        "influenza",
        "flu",
        "seasonal influenza",
        "influenza-like illness",
        "ili",
        "respiratory virus",
        "流感",
        "季节性流感",
        "流行性感冒",
    ],
    "cholera": ["cholera", "acute watery diarrhea"],
    "measles": ["measles", "rubeola"],
    "mpox": ["mpox", "monkeypox"],
}


def expand_disease_aliases(disease: str) -> list[str]:
    normalized = disease.strip().lower()
    aliases = DISEASE_ALIASES.get(normalized, [disease.strip()])
    seen: set[str] = set()
    result: list[str] = []
    for alias in aliases:
        cleaned = alias.strip()
        if cleaned and cleaned.lower() not in seen:
            seen.add(cleaned.lower())
            result.append(cleaned)
    return result


def get_disease_aliases(disease: str | None = None) -> dict[str, list[str]] | list[str]:
    if disease is None:
        return {name: expand_disease_aliases(name) for name in DISEASE_ALIASES}
    return expand_disease_aliases(disease)
