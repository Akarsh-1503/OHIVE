"""Table-driven normalisation cases, including the ones that break naive implementations."""

from __future__ import annotations

import pytest

from app.extract import (
    normalise_email,
    normalise_location,
    normalise_phone,
    normalise_website,
    region_from_location,
    reorder_name_from_email,
    repair_website_from_email,
    split_name,
    strip_address_detail,
    website_is_valid,
)

NAME_CASES = [
    ("Dr. Priya Raghavan", "Priya", "Raghavan"),
    ("Prof. Dr. Tobias van Dijk", "Tobias", "van Dijk"),
    ("Marcus O'Neill Jr.", "Marcus", "O'Neill Jr."),
    ("Daniel O\u2019Neill III", "Daniel", "O\u2019Neill III"),
    ("Elena Vasquez PhD", "Elena", "Vasquez"),
    ("Ms. Aisha Haddad, MBA", "Aisha", "Haddad"),
    ("Ahmed bin Rashid Al Maktoum", "Ahmed", "bin Rashid Al Maktoum"),
    ("Sofia de Souza", "Sofia", "de Souza"),
    ("Mary-Jane Smith-Jones", "Mary-Jane", "Smith-Jones"),
    ("Mary Anne Smith", "Mary Anne", "Smith"),
    ("PRIYA RAGHAVAN", "Priya", "Raghavan"),
    ("MCDONALD, ANGUS", "Angus", "McDonald"),
    ("Raghavan, Priya", "Priya", "Raghavan"),
    ("Ingrid Bergström", "Ingrid", "Bergström"),
    ("Yuki", "Yuki", None),
    ("   ", None, None),
    (None, None, None),
]


@pytest.mark.parametrize(("raw", "first", "last"), NAME_CASES)
def test_split_name(raw: str | None, first: str | None, last: str | None) -> None:
    assert split_name(raw) == (first, last)


PHONE_CASES = [
    ("+91 80 4718 2200", None, "+91 80 4718 2200", "+918047182200"),
    ("Tel: +44 161 496 0118", None, "+44 161 496 0118", "+441614960118"),
    ("(512) 555-0147", "US", "(512) 555-0147", "+15125550147"),
    ("00 44 161 496 0118", None, "+44 161 496 0118", "+441614960118"),
    ("M: +65 6812 4470", None, "+65 6812 4470", "+6568124470"),
    ("Fax +81 45 227 9310", None, "+81 45 227 9310", "+81452279310"),
    ("+44 (0)161 496 0118", None, "+44 (0)161 496 0118", "+441614960118"),
    ("80 4718 2200", "IN", "80 4718 2200", "+918047182200"),
    # Two numbers on one line: keep the first, which cards print as the primary.
    ("+91 80 4718 2200 / +91 98765 43210", None, "+91 80 4718 2200", "+918047182200"),
    # Kept verbatim but unparseable — the reviewer sees it, confidence gets floored.
    ("123", None, "123", None),
    ("+1 555 000 0000", None, "+1 555 000 0000", None),
    (None, None, None, None),
]


@pytest.mark.parametrize(("raw", "region", "printed", "e164"), PHONE_CASES)
def test_normalise_phone(
    raw: str | None, region: str | None, printed: str | None, e164: str | None
) -> None:
    assert normalise_phone(raw, region) == (printed, e164)


EMAIL_CASES = [
    ("PRIYA@Northwind.IO ", None, None, "priya@northwind.io", True, False),
    ("priya @ northwind.io", None, None, "priya@northwind.io", True, False),
    ("Email: priya@northwind.io", None, None, "priya@northwind.io", True, False),
    ("<priya@northwind.io>", None, None, "priya@northwind.io", True, False),
    # Repaired: the typo table plus a ubiquitous consumer domain.
    ("priya@gmall.com", None, "Northwind Robotics", "priya@gmail.com", True, True),
    # Repaired: `rn`->`m` in the TLD, corroborated by the company name.
    (
        "priya@northwind.corn", "northwind.io", "Northwind Robotics",
        "priya@northwind.com", True, True,
    ),
    # Repaired: `l`->`i`, corroborated by an exact website match.
    (
        "m.okonkwo@cedargrld.com", "cedargrid.com", "Cedar Grid Energy",
        "m.okonkwo@cedargrid.com", True, True,
    ),
    # Not repaired: a valid address is never overwritten on a speculative swap.
    (
        "priya@northwind.io", "northwind.io", "Northwind Robotics",
        "priya@northwind.io", True, False,
    ),
    # Not repairable: kept as-is and marked invalid so it lands in the review queue.
    ("priya[at]northwind.io", None, None, "priya[at]northwind.io", False, False),
    ("priya@northwind", None, None, "priya@northwind", False, False),
    (None, None, None, None, False, False),
]


@pytest.mark.parametrize(("raw", "site", "company", "value", "valid", "repaired"), EMAIL_CASES)
def test_normalise_email(
    raw: str | None,
    site: str | None,
    company: str | None,
    value: str | None,
    valid: bool,
    repaired: bool,
) -> None:
    result = normalise_email(raw, site, company)
    assert (result.value, result.valid, result.repaired) == (value, valid, repaired)


def test_email_repair_refuses_uncorroborated_guess() -> None:
    """No website, no matching company, not a consumer domain: leave it alone."""
    result = normalise_email("someone@acrne.example", None, "Totally Different Ltd")
    assert result.value == "someone@acrne.example"
    assert result.repaired is False


WEBSITE_CASES = [
    ("https://www.Northwind.io/careers", "northwind.io", True),
    ("WWW.NORTHWIND.IO", "northwind.io", True),
    ("northwind.io", "northwind.io", True),
    ("www2.example.co.uk/a?b=1", "example.co.uk", True),
    ("http://sub.domain.example.com:8080/x", "sub.domain.example.com", True),
    ("not a url", "not a url", False),
    (None, None, False),
]


@pytest.mark.parametrize(("raw", "host", "valid"), WEBSITE_CASES)
def test_normalise_website(raw: str | None, host: str | None, valid: bool) -> None:
    result = normalise_website(raw)
    assert result == host
    assert website_is_valid(result) is valid


REGION_CASES = [
    ("Bengaluru, KA, India", "IN"),
    ("221B Baker Street, London NW1", "GB"),
    ("Austin, TX, United States", "US"),
    ("Singapore", "SG"),
    ("Rotterdam, Zuid-Holland, Netherlands", "NL"),
    ("São Paulo, SP, Brazil", "BR"),
    ("Somewhere unlabelled", None),
    (None, None),
]


@pytest.mark.parametrize(("location", "region"), REGION_CASES)
def test_region_from_location(location: str | None, region: str | None) -> None:
    assert region_from_location(location) == region


def test_normalise_location_strips_label_and_collapses_space() -> None:
    assert normalise_location("Address:  Bengaluru,   KA,  India ") == "Bengaluru, KA, India"
    assert normalise_location("  ") is None


WEBSITE_REPAIR_CASES = [
    # Observed on a real card: the URL lost a character, the email did not.
    ("heliixmeridian.example", "m.kowalczyk-reyes@helixmeridian.example",
     "helixmeridian.example", True),
    ("northwind.io", "priya@northwind.io", "northwind.io", False),
    # Genuinely different web and mail domains must survive untouched.
    ("acme.co.uk", "priya@acme.com", "acme.co.uk", False),
    ("example.com", "priya@totallydifferent.org", "example.com", False),
    (None, "a@b.co", None, False),
    ("x.io", None, "x.io", False),
    ("x.io", "not-an-email", "x.io", False),
]


@pytest.mark.parametrize(("host", "email", "expected", "repaired"), WEBSITE_REPAIR_CASES)
def test_repair_website_from_email(
    host: str | None, email: str | None, expected: str | None, repaired: bool
) -> None:
    assert repair_website_from_email(host, email) == (expected, repaired)


# (printed_first, printed_last, email, expected_first, expected_last, flipped)
NAME_ORDER_CASES = [
    # Family-name-first, the case the sample corpus ships. `weiming.tan@` re-partitions
    # a three-token name that the naive split gets wrong.
    ("Tan Wei", "Ming", "weiming.tan@kallangharbour.example", "Wei Ming", "Tan", True),
    ("Li Wei", "Chen", "weichen.li@example.cn", "Wei Chen", "Li", True),
    # Western ordering already agrees with the local part: never touched.
    ("Priya", "Raghavan", "priya.raghavan@northwind.io", "Priya", "Raghavan", False),
    ("Aarav", "Deshmukh", "aarav.deshmukh@northwind.example", "Aarav", "Deshmukh", False),
    # Initial-plus-surname mailbox form.
    ("Marta", "Kowalczyk-Reyes", "m.kowalczyk-reyes@helix.example",
     "Marta", "Kowalczyk-Reyes", False),
    # Two tokens are never flipped: `family.given` mailboxes make the email ambiguous.
    ("Yuki", "Tanaka", "tanaka.yuki@example.jp", "Yuki", "Tanaka", False),
    # Particles and hyphenated surnames must survive untouched.
    ("Tobias", "van Dijk", "tobias.vandijk@example.io", "Tobias", "van Dijk", False),
    ("Sofia", "de Souza", "sofia.desouza@example.br", "Sofia", "de Souza", False),
    ("Ahmed", "bin Rashid Al Maktoum", "ahmed.binrashid@example.ae",
     "Ahmed", "bin Rashid Al Maktoum", False),
    ("Mary-Jane", "Smith-Jones", "maryjane.smithjones@example.com",
     "Mary-Jane", "Smith-Jones", False),
    # No usable signal: leave the card's own reading alone.
    ("Priya", "Raghavan", None, "Priya", "Raghavan", False),
    ("Priya", "Raghavan", "not-an-email", "Priya", "Raghavan", False),
    ("Priya", "Raghavan", "info@northwind.io", "Priya", "Raghavan", False),
    (None, "Raghavan", "x.y@z.io", None, "Raghavan", False),
]


@pytest.mark.parametrize(
    ("first", "last", "email", "want_first", "want_last", "flipped"), NAME_ORDER_CASES
)
def test_reorder_name_from_email(
    first: str | None,
    last: str | None,
    email: str | None,
    want_first: str | None,
    want_last: str | None,
    flipped: bool,
) -> None:
    assert reorder_name_from_email(first, last, email) == (want_first, want_last, flipped)


# Real strings the live model returned for the sample corpus, with the place the
# contract asks for ("City, Region, Country").
ADDRESS_DETAIL_CASES = [
    ("Boston, MA 02220, USA", "Boston, MA, USA"),
    ("Pune, MH 411057, India", "Pune, MH, India"),
    ("Austin, TX 78701, USA", "Austin, TX, USA"),
    ("London EC2A 4QT, United Kingdom", "London, United Kingdom"),
    ("Manchester M4 5JT, United Kingdom", "Manchester, United Kingdom"),
    ("2-14-6 Akasaka, Minato-ku, Tokyo 107-0052, Japan", "Minato-ku, Tokyo, Japan"),
    ("18 Kallang Quay, #22-03, Singapore 339158", "Singapore"),
    ("The Old Dispensary, Redcliff Street, Bristol BS1 6Ml, United Kingdom",
     "The Old Dispensary, Bristol, United Kingdom"),
    # Already clean: must pass through untouched.
    ("Bengaluru, KA, India", "Bengaluru, KA, India"),
    ("Dubai, United Arab Emirates", "Dubai, United Arab Emirates"),
    ("San Francisco, CA, USA", "San Francisco, CA, USA"),
    ("Düsseldorf, Germany", "Düsseldorf, Germany"),
    # A place name that merely starts with a saint abbreviation is not a street.
    ("St Petersburg, Russia", "St Petersburg, Russia"),
    # If every segment looks like an address line, keep the original rather than nothing.
    ("18 Kallang Quay", "18 Kallang Quay"),
]


@pytest.mark.parametrize(("raw", "expected"), ADDRESS_DETAIL_CASES)
def test_strip_address_detail(raw: str, expected: str) -> None:
    assert strip_address_detail(raw) == expected


def test_normalise_location_applies_address_stripping() -> None:
    assert normalise_location("Address: 41 Harborline Street, Boston, MA 02220, USA") == (
        "Boston, MA, USA"
    )
