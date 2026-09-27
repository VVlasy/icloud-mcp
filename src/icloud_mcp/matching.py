"""Forgiving name matching shared by contacts and mail lookups.

People misspell names ("Katryn" for "Katrien", "Stefan" for "Stephan"), so exact substring search alone answers "no match" when the person
is right there. These helpers give a sound-alike key plus a similarity score. Anything found only through them is an *approximate*
match: callers must present it as "did you mean...?" and never act on it without the user confirming.
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

_LATIN = re.compile(r"^[a-z]+$")
SIMILAR_MIN = 0.8          # similarity needed for words of 4+ letters (shorter words must sound identical)
Keyed = tuple[str, str, bool]   # (normalised word, its phonetic key, whether it is plain Latin): see keyed()


def norm(s: str) -> str:
    """Case- and accent-insensitive form; keeps non-Latin scripts intact."""
    return "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch)).casefold()


def phonetic_key(word: str) -> str:
    """Rough sound-alike key for Latin-script names (English/Dutch/German-ish spellings). Non-Latin words are returned as-is."""
    w = norm(word)
    if not _LATIN.match(w):
        return w
    for src, dst in (("ij", "i"), ("sch", "sk"), ("ph", "f"), ("ck", "k"), ("th", "t"), ("q", "k"), ("x", "ks"), ("z", "s"), ("w", "v")):
        w = w.replace(src, dst)
    w = re.sub(r"c(?=[aou]|$)", "k", w)
    w = w.replace("c", "s")
    w = re.sub(r"ie|ei|ey|ay|y", "i", w)
    w = w[:1] + w[1:].replace("h", "")
    return re.sub(r"(.)\1+", r"\1", w)


def keyed(word: str) -> Keyed:
    """A word prepared once for matching against many others (norm() is idempotent, so the keys are what similar_enough uses)."""
    n = norm(word)
    return n, phonetic_key(n), bool(_LATIN.match(n))


def _ratio(a: str, b: str, floor: float) -> float:
    """SequenceMatcher.ratio(), or 0 when it is certainly below floor: real_quick_ratio() >= quick_ratio() >= ratio() are
    exact upper bounds and far cheaper, and most name pairs fail them."""
    m = SequenceMatcher(None, a, b)
    if floor and (m.real_quick_ratio() < floor or m.quick_ratio() < floor):
        return 0.0
    return m.ratio()


def word_similarity(a: str, b: str, floor: float = 0.0) -> float:
    """0..1 similarity between two words: the better of spelling similarity and sound-alike similarity. Each is 0 when below
    floor (so an answer below floor is 0, and the slow comparison is skipped)."""
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return max(_ratio(na, nb, floor), _ratio(phonetic_key(na), phonetic_key(nb), floor))


def similar_keyed(q: Keyed, c: Keyed) -> float:
    """similar_enough for two keyed() words."""
    (qn, qk, ql), (cn, ck, cl) = q, c
    if not qn or not cn:
        return 0.0
    if qn == cn:
        return 1.0
    if min(len(qn), len(cn)) <= 3:
        return 1.0 if qk == ck and ql and cl else 0.0
    # spelling and sound are pruned separately: anything below SIMILAR_MIN ends as 0 anyway, so the maximum is unchanged
    sim = max(_ratio(qn, cn, SIMILAR_MIN), _ratio(qk, ck, SIMILAR_MIN))
    return sim if sim >= SIMILAR_MIN else 0.0


def similar_enough(query_word: str, candidate_word: str) -> float:
    """Similarity if the two words plausibly are the same name, else 0. Short words must sound identical."""
    return similar_keyed(keyed(query_word), keyed(candidate_word))


def fuzzy_match_keyed(query: list[Keyed], words: list[Keyed]) -> float:
    """fuzzy_match_all for keyed() query tokens and words, for callers that match one query against many word lists."""
    if not query or not words:
        return 0.0
    total = 0.0
    for tok in query:
        best = max((similar_keyed(tok, w) for w in words), default=0.0)
        if best == 0.0:
            return 0.0
        total += best
    return total / len(query)


def fuzzy_match_all(query_tokens: list[str], words: list[str]) -> float:
    """Mean best similarity if EVERY query token plausibly matches some word; 0 if any token matches none."""
    return fuzzy_match_keyed([keyed(t) for t in query_tokens], [keyed(w) for w in words])
