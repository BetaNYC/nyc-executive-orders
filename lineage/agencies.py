"""The registry facts about each agency a run actually found.

`mentions.json` records an ``agency_id`` and nothing else — no name, no acronym,
no website. That is enough for this package, which only ever compares ids, but it
is not enough for anything downstream: a reader handed ``"office-of-the-mayor"``
cannot label it, and a viewer cannot show it. So a run carries the registry's own
record for every agency it matched, and the file stands on its own.

Only the agencies that were FOUND are carried. The registry holds 317; a run
matches about 178 of them. Shipping the other 139 would add weight to the file and
say something untrue — that the corpus names them.

Two sources, both read from the sibling `ny-gov-web-registry` checkout:

* ``data/registry.json`` — the names, hierarchy, classification and domains.
* ``data/descriptions.json`` — each agency's own words, captured verbatim from its
  website. Present and ``status: "ok"`` for 207 of the 317. That text is
  CC BY-SA 4.0, so the payload records where it came from and what the licence is;
  anything showing it has to say so too.

Nothing here guesses. A field the registry left empty stays ``None`` in the
output, and the description is dropped entirely unless its own record says the
capture worked.
"""

from __future__ import annotations

import json
from pathlib import Path

from .normalize import collapse_spaces

# Long enough to say what the agency does, short enough that 178 of them do not
# double the size of the file. Cut on a word boundary; never mid-word.
DESCRIPTION_LIMIT = 600

# Said in the payload, and repeated by anything that displays the text.
DESCRIPTION_LICENCE = "CC BY-SA 4.0"
DESCRIPTION_CREDIT = (
    "Agency self-descriptions come from BetaNYC's ny-gov-web-registry, captured "
    "verbatim from each agency's own website. Licensed CC BY-SA 4.0."
)

# The only description records worth carrying. The rest say why they are empty
# (`no_url`, `robots_disallowed`, `fetch_failed`...), which is the registry's
# business, not ours.
DESCRIPTION_OK = "ok"

# `web_properties[].role`. An agency can list legacy and microsite domains too;
# only the primary one is a fair answer to "where does this agency live".
ROLE_PRIMARY = "primary"


def load_descriptions(path: Path) -> dict[str, dict]:
    """Read descriptions.json, or return nothing when it is absent.

    A missing file is not an error: the descriptions are a separate crawl from the
    registry itself, and a checkout can have one without the other. The agencies
    then carry no description, which is exactly what the payload will say.
    """
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    described = data.get("descriptions") if isinstance(data, dict) else data
    return described if isinstance(described, dict) else {}


def shorten(text: str, limit: int = DESCRIPTION_LIMIT) -> str:
    """Cut a description to ``limit`` characters on a word boundary.

    Returns the whole thing when it already fits. Otherwise cuts back to the last
    space and appends an ellipsis, so a truncated description reads as truncated
    rather than as a sentence that stops for no reason.
    """
    tidy = collapse_spaces(text)
    if len(tidy) <= limit:
        return tidy
    cut = tidy[:limit].rsplit(" ", 1)[0].rstrip(" ,;:.")
    return f"{cut}…"


def _primary_domain(agency: dict) -> str | None:
    """The domain the agency's own site lives on, or None."""
    for prop in agency.get("web_properties") or []:
        if isinstance(prop, dict) and prop.get("role") == ROLE_PRIMARY:
            return prop.get("domain")
    return None


def _other_names(agency: dict) -> list[str]:
    """Former and alternate spellings, as plain strings.

    The registry writes these as ``{"name": ..., "note": ...}``; a hand-written
    extra-agencies entry may use plain strings. Both are accepted, matching
    ``namelist._names_of``.
    """
    out: list[str] = []
    for other in agency.get("other_names") or []:
        name = other.get("name") if isinstance(other, dict) else other
        if name and name not in out:
            out.append(name)
    return out


def _description_of(agency_id: str, described: dict[str, dict]) -> tuple[str | None, str | None]:
    """The agency's own words and the page they came from, or (None, None).

    Anything the crawl did not actually capture is dropped. A record can carry
    ``status: "ok"`` with empty text, so the text is checked as well as the status.
    """
    record = described.get(agency_id)
    if not isinstance(record, dict) or record.get("status") != DESCRIPTION_OK:
        return (None, None)
    text = record.get("text")
    if not text or not text.strip():
        return (None, None)
    return (shorten(text), record.get("source_url") or None)


def details_for(agency: dict, described: dict[str, dict]) -> dict:
    """One agency's row in the payload. Flat, plain values, no guesses."""
    agency_id = agency["id"]
    description, description_source_url = _description_of(agency_id, described)
    return {
        "id": agency_id,
        "name": agency.get("name"),
        "short_name": agency.get("short_name"),
        "other_names": _other_names(agency),
        "government_level": agency.get("government_level"),
        "classification": agency.get("classification"),
        "parent_id": agency.get("parent_id"),
        "child_ids": list(agency.get("child_ids") or []),
        "primary_domain": _primary_domain(agency),
        "status": agency.get("status"),
        "founding_date": agency.get("founding_date"),
        "dissolution_date": agency.get("dissolution_date"),
        "description": description,
        "description_source_url": description_source_url,
    }


def build(agency_ids: set[str], registry_agencies: list[dict],
          described: dict[str, dict]) -> list[dict]:
    """The registry rows for the agencies a run found, sorted by id.

    An id that the run matched but the registry no longer holds still gets a row,
    carrying its id and nothing else. That cannot happen while both come from the
    same file, but it can the moment the registry is re-pulled and the mentions are
    not — and a row that says only ``{"id": ...}`` is a visible gap, where a
    silently missing row is not.
    """
    by_id = {a["id"]: a for a in registry_agencies if a.get("id")}
    rows = [details_for(by_id[i], described) if i in by_id else {"id": i}
            for i in agency_ids]
    rows.sort(key=lambda r: r["id"])
    return rows


def ids_in(mentions) -> set[str]:
    """Every agency id a run's mentions point at, ambiguous ones included.

    An ambiguous name pins down no single agency, but it names every candidate, and
    each of those still needs a label downstream.
    """
    found: set[str] = set()
    for m in mentions:
        if m.agency_id:
            found.add(m.agency_id)
        found.update(m.agency_ids)
    return found
