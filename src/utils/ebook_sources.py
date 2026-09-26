"""Canonical ebook-source names and identity helpers."""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


_SOURCE_NAMES = {
    "booklore": "Booklore",
    "grimmory": "Booklore",
    "bookorbit": "BookOrbit",
    "kavita": "Kavita",
    "bookfusion": "BookFusion",
    "abs": "ABS",
    "cwa": "CWA",
    "local file": "Local File",
}


def normalize_ebook_source(value) -> str:
    """Return the project-wide canonical spelling for an ebook source."""
    source = str(value or "").strip()
    if not source:
        return ""
    return _SOURCE_NAMES.get(source.lower(), source)


def source_name_variants(value) -> tuple[str, ...]:
    """Return lowercased stored spellings for one canonical ebook source."""
    canonical = normalize_ebook_source(value)
    if not canonical:
        return ()
    variants = {
        alias
        for alias, canonical_name in _SOURCE_NAMES.items()
        if canonical_name == canonical
    }
    variants.add(canonical.lower())
    return tuple(sorted(variants))


def is_grimmory_source(value) -> bool:
    """Whether *value* is a BookLore/Grimmory source-name variant."""
    return normalize_ebook_source(value) == "Booklore"


def is_storyteller_filename(value) -> bool:
    return str(value or "").lower().startswith("storyteller_")


def local_ebook_filename(book) -> Optional[str]:
    """Return the stable local/cache filename for a mapped ebook.

    ``ebook_filename`` may follow mutable source metadata.  The original name is
    retained as the local cache/device identity.  Storyteller artifacts remain the
    active local file because they are generated content rather than source
    metadata.
    """
    current = getattr(book, "ebook_filename", None)
    current = current if isinstance(current, str) and current else None
    if is_storyteller_filename(current):
        return current
    original = getattr(book, "original_ebook_filename", None)
    original = original if isinstance(original, str) and original else None
    return original or current


# Provider order for the searchable-ebook candidate pool. The first provider to
# claim a filename wins the cross-provider dedupe, so this order — not match
# quality — decides which library a file shared between two of them is
# attributed to. Installs that point several providers at one disk can reorder
# it with EBOOK_SOURCE_PRIORITY.
DEFAULT_EBOOK_SOURCE_ORDER = (
    "Booklore",
    "BookOrbit",
    "BookFusion",
    "Kavita",
    "ABS",
    "CWA",
    "Local File",
)

# Preference strings already reported as unresolvable, so a typo is logged once
# rather than on every scan and every per-book search.
_warned_source_preferences: set[str] = set()


def resolve_ebook_source_order(preference, available=None) -> tuple[str, ...]:
    """Order ebook providers for one candidate-pool build.

    *preference* is a comma-separated list of source names in any spelling
    ``normalize_ebook_source`` accepts, so 'Grimmory' and 'Booklore' both name
    the same provider. Named providers run first in the order given; the rest
    keep their default relative order behind them. Unknown and repeated names
    are dropped, and an empty preference reproduces the default order exactly.
    """
    order = tuple(available) if available is not None else DEFAULT_EBOOK_SOURCE_ORDER
    known = {name.lower(): name for name in order}

    if isinstance(preference, str):
        tokens = preference.split(",")
    elif preference:
        tokens = list(preference)
    else:
        tokens = []

    preferred: list[str] = []
    unknown: list[str] = []
    for token in tokens:
        raw = str(token).strip()
        if not raw:
            continue
        resolved = known.get(normalize_ebook_source(raw).lower())
        if resolved is None:
            unknown.append(raw)
        elif resolved not in preferred:
            preferred.append(resolved)

    if unknown:
        cache_key = str(preference)
        if cache_key not in _warned_source_preferences:
            _warned_source_preferences.add(cache_key)
            logger.warning(
                "EBOOK_SOURCE_PRIORITY: ignoring unknown source name(s) %s — known sources are %s",
                ", ".join(repr(name) for name in unknown),
                ", ".join(order),
            )

    return tuple(preferred) + tuple(name for name in order if name not in preferred)
