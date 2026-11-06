"""Pronunciation fixes: words the voice says wrong, replaced before speech is generated.

Rules live in storage/pronunciation.json (per computer, not in git). They only change
the text sent to the voice service; subtitles keep the script's original spelling.
"""

import json
import os
import re
import threading
from dataclasses import asdict, dataclass

from loguru import logger

from app.utils import utils

SCOPE_TELUGU = "telugu"
SCOPE_ALL = "all"
SCOPES = (SCOPE_TELUGU, SCOPE_ALL)

MAX_RULES = 500
MAX_WORD_CHARS = 60
MAX_SAY_CHARS = 200

_TELUGU = re.compile(r"[ఀ-౿]")
# Telugu vowel signs are not \w, so they are listed explicitly; otherwise a rule
# for "కాలం" would also match inside "కాలంలో".
_WORD_CHAR = r"[\wఀ-౿]"

_lock = threading.Lock()
_cache: dict = {"mtime": None, "rules": []}


@dataclass(frozen=True)
class Rule:
    word: str
    say: str
    scope: str = SCOPE_TELUGU


def rules_file() -> str:
    return os.path.join(utils.storage_dir(create=True), "pronunciation.json")


def clean_rules(raw_rules) -> list[Rule]:
    """Drops empty rows, trims text, keeps the last rule for a repeated word."""
    by_word: dict[str, Rule] = {}
    for item in raw_rules or []:
        if isinstance(item, Rule):
            item = asdict(item)
        if not isinstance(item, dict):
            continue
        word = " ".join(str(item.get("word") or "").split())[:MAX_WORD_CHARS]
        say = " ".join(str(item.get("say") or "").split())[:MAX_SAY_CHARS]
        scope = item.get("scope") if item.get("scope") in SCOPES else SCOPE_TELUGU
        if word and say and word != say:
            by_word[word.casefold()] = Rule(word, say, scope)
    return list(by_word.values())[:MAX_RULES]


def load_rules() -> list[Rule]:
    path = rules_file()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return []
    with _lock:
        if _cache["mtime"] == mtime:
            return list(_cache["rules"])
        try:
            with open(path, encoding="utf-8") as file:
                rules = clean_rules(json.load(file).get("rules"))
        except (OSError, ValueError, AttributeError) as exc:
            logger.warning(f"ignoring unreadable pronunciation file {path}: {exc}")
            rules = []
        _cache.update(mtime=mtime, rules=rules)
        return list(rules)


def save_rules(raw_rules) -> list[Rule]:
    rules = clean_rules(raw_rules)
    path = rules_file()
    temp_path = f"{path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as file:
        json.dump({"rules": [asdict(r) for r in rules]}, file, ensure_ascii=False, indent=2)
    os.replace(temp_path, path)
    with _lock:
        _cache.update(mtime=None, rules=[])
    return rules


def _compile(rules: list[Rule]):
    lookup = {rule.word.casefold(): rule.say for rule in rules}
    # Longest first, so "black hole" wins over "hole".
    words = sorted((rule.word for rule in rules), key=len, reverse=True)
    pattern = re.compile(
        rf"(?<!{_WORD_CHAR})(?:{'|'.join(re.escape(w) for w in words)})(?!{_WORD_CHAR})",
        re.IGNORECASE,
    )
    return pattern, lookup


def apply(text: str, telugu: bool | None = None, rules: list[Rule] | None = None) -> str:
    """Replaces rule words in text. Telugu-only rules apply when the script is Telugu."""
    if not text:
        return text or ""
    rules = load_rules() if rules is None else rules
    if telugu is None:
        telugu = bool(_TELUGU.search(text))
    active = [r for r in rules if r.scope == SCOPE_ALL or telugu]
    if not active:
        return text
    pattern, lookup = _compile(active)

    def replace(segment: str) -> str:
        return pattern.sub(lambda m: lookup[m.group(0).casefold()], segment)

    # Pause tags such as [pause 1s] stay untouched.
    pieces, last = [], 0
    for tag in utils.PAUSE_TAG_PATTERN.finditer(text):
        pieces.append(replace(text[last : tag.start()]))
        pieces.append(tag.group(0))
        last = tag.end()
    pieces.append(replace(text[last:]))
    return "".join(pieces)


def spoken_variants(line: str) -> list[str]:
    """How a script line may have been spoken, for matching voice timings back to it."""
    rules = load_rules()
    if not rules:
        return []
    variants = {apply(line, telugu=True, rules=rules), apply(line, telugu=False, rules=rules)}
    variants.discard(line)
    return list(variants)
