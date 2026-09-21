import re
import unicodedata
from difflib import SequenceMatcher


def words(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", text.casefold())
    return re.findall(r"[a-z0-9]+", "".join(c for c in text if not unicodedata.combining(c)))


def search_intent(query, source, project, device, projects):
    tokens = words(query)
    phrase = " " + " ".join(tokens) + " "
    if not device and any(text in phrase for text in (
        " autre mac ", " autre ordinateur ", " other mac ", " other computer ")):
        device = "other"
    if source == "all":
        for family in ("codex", "claude", "vscode"):
            if family in tokens:
                source = family
                break
    # Only an exact, unambiguous project name becomes an automatic filter.
    matches = {p["name"] for p in projects if words(p["name"]) and
               " " + " ".join(words(p["name"])) + " " in phrase}
    if not project and len(matches) == 1:
        project = matches.pop()
    stop = set("trouve retrouvez retrouve trouver cherche recherche moi la le les de des du une un "
               "conversation conversations discussion discussions projet project sur mon ma mes autre "
               "ordinateur mac dans avec ai memory codex claude vscode desktop derniers dernier dernieres "
               "echanges messages find search for the a an my other computer about on in latest "
               "recent please s il te plait ou j ai parle we discussed".split())
    if project:
        stop.update(words(project))
    conversational = any(token in tokens for token in ("trouve", "retrouve", "cherche", "find", "search"))
    remaining = [token for token in tokens if token not in stop] if conversational or project or device else tokens
    return " ".join(remaining), source, project, device


def project_suggestions(query: str, projects: list[dict]) -> list[dict]:
    needle = "".join(words(query))
    if not needle:
        return projects
    scored = []
    for project in projects:
        name = "".join(words(project["name"]))
        score = 1.0 if needle in name else SequenceMatcher(None, needle, name).ratio()
        if score >= 0.35:
            scored.append((score, project))
    return [dict(project, name_similarity=round(score, 2))
            for score, project in sorted(scored, key=lambda item: (-item[0], item[1]["id"]))[:10]]
