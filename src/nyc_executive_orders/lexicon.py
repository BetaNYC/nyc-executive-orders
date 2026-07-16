"""Local, offline, deterministic word recognizer for the title gate.

The prototype cleaner showed that a vowel/consonant heuristic cannot tell a clean
OCR title from one with a single mangled word that still "looks word-like"
(``Cray`` for ``City``; ``TRANSPL``; ``AMBL``). This module gates a candidate
title on actual word recognition: a title is trustworthy only when EVERY
meaningful token is a recognized word.

Recognition = English dictionary  ∪  a curated NYC/gov domain lexicon  ∪  allowed
non-words (acronyms, Roman numerals), with light deterministic stemming so the
older system dictionary's missing plurals / ``-ing`` forms don't cause false
rejects.

Design constraints (engineering-standards §7 — local-first, reproducible):
  * **No network, no dependency.** Pure stdlib + a small bundled fallback list.
  * **Deterministic.** Same inputs → same recognition, no randomness.
  * **Reproducibility caveat (loud, on purpose).** For the *sample prototype* we
    prefer the system word list at ``/usr/share/dict/words`` when present (broad
    coverage on macOS). That makes the accept/reject decision depend on the host
    dictionary. BEFORE wiring this into a full corpus sweep or a published
    artifact, FREEZE the word list into the repo so the decision is regenerable by
    third parties. :func:`english_lexicon_source` reports which source was used so
    the report can state it.

Err toward flagging: an unrecognized-but-real word (e.g. a rare proper noun) makes
a good title hold as ``title-uncertain`` for human review — the safe direction.
"""

from __future__ import annotations

import functools
import re
from pathlib import Path

# Common system word list (BSD/macOS ships web2 here; many Linux distros install a
# words file at the same path via `words`/`wamerican`). Used when present.
SYSTEM_WORDS_PATH = Path("/usr/share/dict/words")

# --------------------------------------------------------------------------- #
# Curated NYC / civic-gov domain lexicon — lowercase. Words that appear in EO
# titles but the older web2 dictionary lacks (modern compounds, agencies, NYC
# place names, common administrative vocabulary). A "sensible starter set", not
# exhaustive — unknown terms simply route a title to human review.
# --------------------------------------------------------------------------- #
DOMAIN_LEXICON: frozenset[str] = frozenset({
    # NYC geography / neighborhoods / boroughs
    "citywide", "midtown", "downtown", "uptown", "manhattan", "brooklyn", "bronx",
    "queens", "staten", "harlem", "chinatown", "borough", "boroughs", "waterfront",
    "neighborhood", "neighborhoods",
    # infrastructure / environment compounds web2 misses
    "stormwater", "wastewater", "watershed", "citywide", "brownfield", "brownfields",
    "resiliency", "sustainability", "cybersecurity", "broadband", "rezoning",
    "streetscape", "bikeway", "greenway",
    # administrative / civic vocabulary the old dict lacks in these forms
    "coordination", "implementation", "reorganization", "designation",
    "establishment", "administration", "modernization", "privatization",
    "decentralization", "accountability", "transparency", "interagency",
    "taskforce", "citywide", "workforce", "nonprofit", "stakeholder",
    "stakeholders", "procurement", "oversight", "compliance", "governance",
    "preparedness", "modification", "amendment", "amendments", "continuation",
    "moratorium", "rulemaking",
    # civic / equity terms
    "minority", "womenowned", "disadvantaged", "underserved", "immigrant",
    "immigrants", "tenant", "tenants", "veteran", "veterans", "homeless",
    "homelessness",
    # government structures & roles
    "mayoral", "commissioner", "commissioners", "deputy", "chancellor",
    "comptroller", "corporation", "authority", "commission", "committee",
    "council", "agency", "agencies", "bureau", "directive",
    # pandemic-era vocabulary (common in 2020-2022 emergency orders)
    "covid", "coronavirus", "pandemic", "vaccination", "quarantine", "telework",
})

# Acronyms / initialisms allowed as recognized tokens (checked case-insensitively).
ABBREVIATIONS: frozenset[str] = frozenset({
    "ceqr", "ulurp", "nyc", "nycha", "hpd", "dot", "dep", "dob", "dcas", "dcp",
    "hhc", "nypd", "fdny", "doh", "doe", "doitt", "oti", "mopd", "mocj", "mou",
    "moa", "eeo", "eo", "fy", "id", "it", "tv", "ems", "mwbe", "wbe", "mbe",
    "lgbtq", "ada", "eas", "oem", "nycem", "sro", "sros", "ceta", "hiv", "aids",
})

# Roman numerals I..XXX (orders reference "Title I", "Article IV", etc.).
_ROMAN_RE = re.compile(r"^m{0,4}(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})$")

# Deterministic suffix stems: (suffix, replacement). Tried in order; the first
# whose stripped form is in the dictionary counts as a match. Covers the plurals
# and gerunds the web2 dictionary omits.
_STEM_RULES: tuple[tuple[str, str], ...] = (
    ("ies", "y"),
    ("es", ""),
    ("s", ""),
    ("ing", ""),
    ("ing", "e"),
    ("ed", ""),
    ("ed", "e"),
    ("ation", "ate"),
    ("ations", "ate"),
)

# Minimal bundled fallback: the few hundred function/common words needed so a host
# WITHOUT a system dictionary still recognizes ordinary title glue. Deliberately
# small — real coverage comes from the system dict; this only prevents total
# collapse (which would flag every title) on a dict-less box.
_FALLBACK_WORDS: frozenset[str] = frozenset("""
a an and or of the to for in on at by with from as is are be this that these those
new york city order office mayor executive emergency department board agency
authority commission committee council program services service public private
management operations development planning health housing fire police human
resources environmental quality review construction contracts data information
technology education finance budget office establishment coordination action
assistance voter matters permit permits system systems control controls storm
water sewer municipal separate electronic processing veterans veteran citywide
midtown implementation requirements requirement power virtue vested pursuant
whereas section provisions charter effective date signed voter real subject line
matters pertaining permit coordination
""".split())


@functools.lru_cache(maxsize=1)
def _load() -> tuple[frozenset[str], str]:
    """Return ``(english_word_set, source_label)``. Cached; read once per process."""
    if SYSTEM_WORDS_PATH.exists():
        try:
            words = {
                w.strip().lower()
                for w in SYSTEM_WORDS_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()
                if w.strip()
            }
            words |= _FALLBACK_WORDS  # belt-and-suspenders for tiny dicts
            return frozenset(words), f"system:{SYSTEM_WORDS_PATH}"
        except OSError:
            pass
    return _FALLBACK_WORDS, "bundled-fallback"


def english_lexicon_source() -> str:
    """Human-readable label of which English word source is in effect."""
    return _load()[1]


def _in_dict(token: str) -> bool:
    words, _ = _load()
    if token in words:
        return True
    for suffix, rep in _STEM_RULES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 2:
            if token[: -len(suffix)] + rep in words:
                return True
    return False


def is_roman_numeral(token: str) -> bool:
    t = token.lower()
    return bool(t) and bool(_ROMAN_RE.match(t))


def recognize(token: str) -> bool:
    """True if ``token`` is a recognized word (dict ∪ domain ∪ abbrev ∪ roman).

    Case-insensitive; applies deterministic stemming for the dictionary check.
    """
    t = token.lower()
    if not t.isalpha():
        return False
    if t in DOMAIN_LEXICON:
        return True
    if t in ABBREVIATIONS:
        return True
    if is_roman_numeral(t):
        return True
    return _in_dict(t)
