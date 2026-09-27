"""Map a user's topic to the same Wikipedia article in every requested language.

Topics go through Wikidata: one item (QID) has one sitelink per language
edition, which avoids translating titles by hand. A topic spec can be:

  "intermittent fasting"          free text, searched on Wikidata
  "Q1666254"                      a Wikidata item id
  "uk:Астрономія"                 an exact article title in one language
  "Learning English=Q1860+Q4..."  optional label, several items summed

In a topic with several parts, a "lang:Title" part counts only in its own
language ("Fasting=pl:Głodówka lecznicza+cs:Přerušovaný půst" is a per-language
proxy map). A language that no part covers gets no article, rather than a
silent sum of different concepts; add "+uk:<Title>" to cover it.
"""

from __future__ import annotations

import re

from .api import wiki_api, wikidata

QID_RE = re.compile(r"^Q\d+$", re.I)
TITLE_RE = re.compile(r"^([a-z]{2,3}(?:-[a-z]{2,8})?):(.+)$")
# P31 values that mean "this item is not a real topic article".
NON_TOPIC_TYPES = {
    "Q4167410": "disambiguation page",
    "Q13406463": "Wikimedia list article",
    "Q4167836": "Wikimedia category",
}
SITE_EXCEPTIONS = {"be-tarask": "be_x_oldwiki"}


def site_key(lang: str) -> str:
    return SITE_EXCEPTIONS.get(lang, lang.replace("-", "_") + "wiki")


def parse_topic(spec: str) -> tuple[str | None, list[str]]:
    label = None
    if "=" in spec:
        label, spec = spec.split("=", 1)
        label = label.strip() or None
    parts = [p.strip() for p in spec.split("+") if p.strip()]
    if not parts:
        raise ValueError(f"empty topic spec: {spec!r}")
    return label, parts


def _search(term: str, search_langs: list[str]) -> list[dict]:
    seen: dict[str, dict] = {}
    for lang in search_langs:
        data = wikidata({"action": "wbsearchentities", "search": term, "language": lang,
                         "uselang": "en", "type": "item", "limit": 7})
        for hit in data.get("search", []):
            seen.setdefault(hit["id"], {
                "qid": hit["id"],
                "label": hit.get("label", ""),
                "description": hit.get("description", ""),
                "exact": hit.get("match", {}).get("text", "").casefold() == term.casefold(),
            })
        if seen:
            break  # first language that finds anything wins; keeps results predictable
    return list(seen.values())


def _entity(qid: str, langs: list[str]) -> dict:
    data = wikidata({"action": "wbgetentities", "ids": qid,
                     "props": "sitelinks|labels|descriptions",
                     "languages": "|".join(["en", *langs])})
    ent = data.get("entities", {}).get(qid)
    if not ent or "missing" in ent:
        raise ValueError(f"Wikidata item {qid} not found")
    return ent


def _qid_for_title(lang: str, title: str) -> str | None:
    data = wikidata({"action": "wbgetentities", "sites": site_key(lang), "titles": title,
                     "props": "info", "normalize": 1})
    for qid in data.get("entities", {}):
        if QID_RE.match(qid):
            return qid
    return None


def resolve_part(part: str, langs: list[str]) -> dict:
    warnings: list[str] = []
    candidates: list[dict] = []
    if QID_RE.match(part):
        qid = part.upper()
    elif m := TITLE_RE.match(part):
        qid = _qid_for_title(m.group(1), m.group(2))
        if not qid:
            raise ValueError(f"no Wikidata item for {m.group(1)}.wikipedia article {m.group(2)!r}")
    else:
        candidates = _search(part, ["en", *[l for l in langs if l != "en"]])
        if not candidates:
            raise ValueError(f"nothing found on Wikidata for {part!r}; try an English term, "
                             f"a QID, or 'lang:Exact title'")
        exact = [c for c in candidates if c["exact"]]
        qid = (exact or candidates)[0]["qid"]
        if len(candidates) > 1 and not exact:
            warnings.append("no exact label match; picked the top search hit - check the description")

    ent = _entity(qid, langs)
    label = ent.get("labels", {}).get("en", {}).get("value") or next(
        (v["value"] for v in ent.get("labels", {}).values()), qid)
    desc = ent.get("descriptions", {}).get("en", {}).get("value", "")
    claims = wikidata({"action": "wbgetclaims", "entity": qid, "property": "P31", "props": ""})
    for claim in claims.get("claims", {}).get("P31", []):
        val = claim.get("mainsnak", {}).get("datavalue", {}).get("value", {})
        if isinstance(val, dict) and val.get("id") in NON_TOPIC_TYPES:
            warnings.append(f"{qid} is a {NON_TOPIC_TYPES[val['id']]}, not a topic article - pick another candidate")
    titles = {}
    for lang in langs:
        link = ent.get("sitelinks", {}).get(site_key(lang))
        if link:
            titles[lang] = link["title"]
    return {
        "qid": qid, "label": label, "description": desc, "titles": titles,
        "warnings": warnings,
        "other_candidates": [c for c in candidates if c["qid"] != qid][:4],
    }


def resolve_topic(spec: str, langs: list[str]) -> dict:
    label, parts = parse_topic(spec)
    resolved = [resolve_part(p, langs) for p in parts]
    warnings: list[str] = []
    m = TITLE_RE.match(parts[0]) if len(parts) == 1 else None
    other = {l: t for l, t in resolved[0]["titles"].items() if l in langs and l != m.group(1)} if m else {}
    if other:  # a single 'lang:Title' is expanded to the other editions through its Wikidata item
        warnings.append(f"'{parts[0]}' is also counted in other languages through its Wikidata item: "
                        + "; ".join(f"{l}: {t}" for l, t in other.items())
                        + " - check they are the same concept, or pin each language (Label=cs:A+pl:B)")
    if len(parts) > 1:
        for part, r in zip(parts, resolved):
            if m := TITLE_RE.match(part):  # language-scoped part of a composite topic
                pin = m.group(1)
                r["pinned_lang"] = pin
                r["titles"] = {pin: r["titles"][pin]} if pin in r["titles"] else {}
                if pin not in langs:
                    warnings.append(f"part {part!r} is for '{pin}', which is not in --langs - ignored")
        counts = {l: [r["titles"][l] for r in resolved if l in r["titles"]] for l in langs}
        sizes = {l: len(t) for l, t in counts.items() if t}
        if len(set(sizes.values())) > 1:
            detail = "; ".join(f"{l}: {len(t)} ({' + '.join(t)})" for l, t in counts.items() if t)
            warnings.append(f"not like-for-like: languages sum different numbers of articles ({detail}). "
                            "Compare with care, or pin one title per language with 'lang:Title' parts")
    return {
        "spec": spec,
        "label": label or " + ".join(r["label"] for r in resolved),
        "items": resolved,
        "warnings": warnings,
        "missing_langs": [l for l in langs if not any(l in r["titles"] for r in resolved)],
    }


def redirects_to(lang: str, title: str, limit: int) -> list[str]:
    """Titles of main-namespace redirects pointing at an article.

    Pageviews of a redirect are counted under the redirect's own title, so
    ignoring them undercounts interest (sometimes by a lot)."""
    if limit <= 0:
        return []
    data = wiki_api(lang, {"action": "query", "titles": title, "prop": "redirects",
                           "rdnamespace": 0, "rdlimit": "max", "redirects": 1})
    pages = data.get("query", {}).get("pages", [])
    if not pages:
        return []
    return [r["title"] for r in pages[0].get("redirects", [])][:limit]


def search_wiki(lang: str, query: str, limit: int = 8) -> list[dict]:
    """Full-text search inside one language edition - for finding a local article
    that is missing from (or linked to a different) Wikidata item."""
    data = wiki_api(lang, {"action": "query", "list": "search", "srsearch": query,
                           "srlimit": limit, "srnamespace": 0, "srprop": ""})
    titles = [h["title"] for h in data.get("query", {}).get("search", [])]
    if not titles:
        return []
    props = wiki_api(lang, {"action": "query", "titles": "|".join(titles),
                            "prop": "pageprops|description", "ppprop": "wikibase_item"})
    info = {p["title"]: p for p in props.get("query", {}).get("pages", [])}
    return [{"title": t, "qid": info.get(t, {}).get("pageprops", {}).get("wikibase_item"),
             "description": info.get(t, {}).get("description", "")} for t in titles]
