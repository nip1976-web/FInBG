"""Deciding whether a payer and a Bitrix company are the same organisation.

Bank statements and Bitrix spell the same company differently — word order
("АЛМАС ЛПК ООО" / "ООО ЛПК \"АЛМАС\""), abbreviations ("ТМФ" / "Томские
мебельные фасады"), and outright typos ("Финмарккет" / "Финмаркет"). Matching
has to tolerate all three, because the alternative — trusting the invoice
number alone — attaches a payment to whichever client happens to own that
number, which is how a 6-рубль payment from ЛДК №3 ended up on a СВАГ invoice.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

# Dropped before comparing: they carry no identifying information and appear
# on either side inconsistently.
LEGAL_FORMS = frozenset(
    {"ооо", "оао", "зао", "пао", "ао", "ип", "нпп", "пкп", "пфк", "пф", "тд", "тк"}
)
# Above this, two glued-together names are the same company with a typo.
FUZZY_THRESHOLD = 0.85


def tokens(name: str | None) -> list[str]:
    words = re.findall(r"[0-9a-zа-яё]+", (name or "").lower())
    return [word for word in words if word not in LEGAL_FORMS]


def _acronym(words: list[str]) -> str:
    return "".join(word[0] for word in words if word)


def same_company(payer: str | None, company: str | None) -> bool:
    """True when the payer and the invoice's company look like one organisation.

    Deliberately lenient: a false negative only sends the payment to manual
    review, while a false positive silently credits the wrong client.
    """
    left, right = tokens(payer), tokens(company)
    if not left or not right:
        return False

    if set(left) & set(right):
        return True
    if len(left) == 1 and left[0] == _acronym(right):
        return True
    if len(right) == 1 and right[0] == _acronym(left):
        return True

    glued_left, glued_right = "".join(left), "".join(right)
    if glued_left in glued_right or glued_right in glued_left:
        return True
    return SequenceMatcher(None, glued_left, glued_right).ratio() >= FUZZY_THRESHOLD
