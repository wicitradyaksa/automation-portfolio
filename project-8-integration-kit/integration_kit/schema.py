"""The one internal shape all three sources normalise into.

The reason this file exists at all: without it, every downstream consumer has
to know which source a record came from, and "which source" leaks into every
report, every dashboard and every if-statement in the business logic. One
schema at the boundary means the rest of the system never learns that the
helpdesk calls it ``requester_email`` and the CRM calls it ``primaryEmail``.

Two decisions worth defending:

* ``raw`` is kept. Normalisation is lossy and you will be asked "where did
  this value come from" by someone holding a screenshot. Keeping the source
  payload turns that from an archaeology project into a lookup.
* Merge precedence is explicit and data-driven, not "last write wins". When
  two sources disagree about someone's name, silently taking whichever
  arrived most recently produces a record that flickers.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass, field, replace

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")

# Higher wins when two sources disagree on a field. The CRM is authoritative
# for identity because that is where a human curates it; the helpdesk is
# authoritative for nothing but its own ticket counts.
SOURCE_TRUST = {"helpdesk": 10, "billing": 20, "crm": 30}


def normalise_email(value: str | None) -> str | None:
    """Lowercase and trim. Nothing cleverer, on purpose.

    Gmail dot-stripping and ``+tag`` removal are tempting and wrong: they are
    Gmail-specific, they are not true of most providers, and applying them
    universally merges two people who are genuinely different into one record.
    A false merge is much more expensive to undo than a duplicate.
    """
    if not value:
        return None
    value = value.strip().lower()
    return value if EMAIL_RE.match(value) else None


# Below this many digits it is not a phone number -- it is an extension, a
# placeholder, or someone's typo. Returning None beats returning "+61", which
# looks like data and is not.
MIN_PHONE_DIGITS = 6


def normalise_phone(value: str | None, default_country: str = "+61") -> str | None:
    """Digits plus a leading ``+``; national numbers get the default prefix.

    Not a substitute for libphonenumber. It is deterministic, it is testable,
    and it documents the assumption it is making instead of hiding it.

    Two cases that look like nitpicks and are not:

    * ``+61 (0) 400 000 000``. The parenthesised zero is a **trunk prefix**,
      and the notation means precisely "omit this when dialling
      internationally". Stripping punctuation blindly yields
      ``+610400000000`` -- a number with an extra digit that will never
      connect. This form is extremely common in Australian and European
      contact data, so it is handled explicitly rather than left to chance.
    * Short junk. ``"0"`` used to normalise to ``"+61"``: a country code with
      no number, which then sits in the CRM looking like a real value.
    """
    if not value:
        return None

    # Drop a parenthesised trunk prefix before any other cleaning -- it is the
    # one piece of punctuation here that carries meaning.
    value = re.sub(r"\(\s*0\s*\)", "", value)

    kept = re.sub(r"[^\d+]", "", value)
    if not kept:
        return None

    if kept.startswith("+"):
        digits = re.sub(r"\D", "", kept[1:])
        return "+" + digits if len(digits) >= MIN_PHONE_DIGITS else None

    digits = re.sub(r"\D", "", kept)
    if len(digits) < MIN_PHONE_DIGITS:
        return None
    if digits.startswith("0"):
        return default_country + digits[1:]
    return "+" + digits


def to_utc_iso(value) -> str | None:
    """Everything becomes a UTC ISO-8601 string with an explicit offset.

    A naive datetime in a shared schema is a bug waiting for a daylight-saving
    boundary. Unix seconds, unix milliseconds and ISO strings all arrive from
    these three APIs, so all three are accepted here and nowhere else.
    """
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        # Anything past ~2001 in seconds is > 1e9; a value over 1e11 is
        # milliseconds. Guessing is unavoidable when the API does not say.
        seconds = value / 1000.0 if value > 1e11 else float(value)
        return dt.datetime.fromtimestamp(seconds, dt.timezone.utc).isoformat()
    if isinstance(value, dt.datetime):
        parsed = value
    else:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            parsed = dt.datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc).isoformat()


@dataclass(frozen=True)
class Contact:
    """One person, whichever system they came from."""

    contact_id: str
    email: str | None
    full_name: str | None
    phone: str | None
    company: str | None
    source: str
    source_id: str
    created_at: str | None
    updated_at: str | None
    tags: tuple[str, ...] = ()
    raw: dict = field(default_factory=dict, compare=False, repr=False)

    def to_dict(self) -> dict:
        data = {
            "contact_id": self.contact_id,
            "email": self.email,
            "full_name": self.full_name,
            "phone": self.phone,
            "company": self.company,
            "source": self.source,
            "source_id": self.source_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "tags": list(self.tags),
        }
        return data


def stable_contact_id(email: str | None, source: str, source_id: str) -> str:
    """Deterministic id, so re-running a sync does not create new rows.

    Keyed on email where there is one, because that is the only identifier
    shared across all three systems. Where there is not, it falls back to
    ``source:source_id``, which at least stays stable within that source.
    """
    basis = email if email else source + ":" + source_id
    return "ct_" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:20]


def merge(left: Contact, right: Contact) -> Contact:
    """Field-by-field merge using SOURCE_TRUST, then recency as a tiebreak.

    Note what this does NOT do: it never overwrites a present value with an
    absent one. A less-trusted source that happens to know the phone number
    still contributes it.
    """
    if left.contact_id != right.contact_id:
        raise ValueError("refusing to merge contacts with different ids")

    def better(a_val, a_src: Contact, b_val, b_src: Contact):
        if a_val in (None, "", ()):
            return b_val
        if b_val in (None, "", ()):
            return a_val
        a_rank = SOURCE_TRUST.get(a_src.source, 0)
        b_rank = SOURCE_TRUST.get(b_src.source, 0)
        if a_rank != b_rank:
            return a_val if a_rank > b_rank else b_val
        return a_val if (a_src.updated_at or "") >= (b_src.updated_at or "") else b_val

    winner = left if SOURCE_TRUST.get(left.source, 0) >= SOURCE_TRUST.get(right.source, 0) else right
    return replace(
        winner,
        email=better(left.email, left, right.email, right),
        full_name=better(left.full_name, left, right.full_name, right),
        phone=better(left.phone, left, right.phone, right),
        company=better(left.company, left, right.company, right),
        created_at=min(x for x in (left.created_at, right.created_at) if x) if (left.created_at or right.created_at) else None,
        updated_at=max(x for x in (left.updated_at, right.updated_at) if x) if (left.updated_at or right.updated_at) else None,
        tags=tuple(sorted(set(left.tags) | set(right.tags))),
    )


def merge_all(contacts) -> list[Contact]:
    """Collapse an iterable of Contacts by contact_id, preserving input order."""
    merged: dict[str, Contact] = {}
    order: list[str] = []
    for contact in contacts:
        if contact.contact_id in merged:
            merged[contact.contact_id] = merge(merged[contact.contact_id], contact)
        else:
            merged[contact.contact_id] = contact
            order.append(contact.contact_id)
    return [merged[cid] for cid in order]
