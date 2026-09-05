"""Splitting a stored display name into first and last name.

Pure string logic with no IO, so the API and the backfill script agree on
what a split looks like.
"""

from __future__ import annotations

from app.db.models import Customer

SUFFIXES = frozenset({"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "phd", "md"})


def split_full_name(display_name: str) -> tuple[str, str]:
    """Split a display name into ``(first_name, last_name)``.

    The last whitespace separated token is the last name, unless it is an
    honorific suffix, in which case it stays attached to the token before it.
    A single token is treated as a first name because a mononym is not a
    family name.
    """
    parts = display_name.split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    if len(parts) > 2 and parts[-1].lower().rstrip(",") in SUFFIXES:
        return " ".join(parts[:-2]), f"{parts[-2]} {parts[-1]}"
    return " ".join(parts[:-1]), parts[-1]


def join_name(first_name: str, last_name: str) -> str:
    """Join the split columns back into a display name."""
    return " ".join(part for part in (first_name.strip(), last_name.strip()) if part)


def display_name_for(customer: Customer) -> str:
    """The name to show for a customer, preferring the split columns once backfilled."""
    if customer.first_name or customer.last_name:
        return join_name(customer.first_name, customer.last_name)
    return customer.name
