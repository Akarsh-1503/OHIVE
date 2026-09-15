"""Field normalisation: names, phone numbers, emails, websites and locations."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

import phonenumbers
from email_validator import EmailNotValidError, validate_email
from rapidfuzz.distance import Levenshtein

# --------------------------------------------------------------------------- text helpers


def _ascii_fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _alnum(text: str) -> str:
    return "".join(ch for ch in _ascii_fold(text).lower() if ch.isalnum())


def _collapse(text: str | None) -> str | None:
    if not text:
        return None
    # Cards pasted from PDFs carry NO-BREAK SPACE; fold it before collapsing runs.
    cleaned = re.sub(r"\s+", " ", text.replace("\u00a0", " ")).strip(" \t\n\r,;:|")
    return cleaned or None


# --------------------------------------------------------------------------- names

_NAME_TITLES = frozenset(
    {
        "mr", "mrs", "ms", "miss", "mx", "dr", "dr.-ing", "prof", "professor", "sir", "dame",
        "rev", "fr", "capt", "col", "gen", "lt", "sgt", "maj", "hon", "eng", "ing", "ir",
        "adv", "shri", "smt", "mme", "mlle", "herr", "frau",
    }
)
# Generational suffixes stay on the name — they disambiguate father from son. Post-nominal
# qualifications do not: "Dear Ms. Raghavan PhD" is how you lose a deal.
_GENERATIONAL = frozenset({"jr", "sr", "ii", "iii", "iv"})
_POST_NOMINALS = frozenset(
    {
        "phd", "ph", "md", "mba", "msc", "bsc", "ma", "ba", "bs", "ms", "md.", "cpa", "cfa",
        "esq", "pe", "pmp", "dds", "dvm", "rn", "jd", "llm", "llb", "frcs", "mrics", "cissp",
        "cma", "aia", "riba", "ceng", "facs", "fca", "aca",
    }
)
_PARTICLES = frozenset(
    {
        "van", "von", "der", "den", "de", "del", "della", "di", "da", "das", "dos", "du",
        "le", "la", "lo", "bin", "binti", "bint", "ibn", "al", "el", "ter", "ten", "op",
        "zu", "st", "mac", "mc", "abu", "y", "e", "af", "av",
    }
)


def _strip_dots(token: str) -> str:
    return _ascii_fold(token).lower().strip(".,")


def _smart_case(token: str, *, is_particle: bool) -> str:
    if is_particle:
        return token.lower()
    # Both apostrophe glyphs appear on real cards and must case identically.
    parts = re.split(r"([\-'\u2019])", token.lower())
    rebuilt = "".join(p if p in "-'\u2019" else p.capitalize() for p in parts)
    lowered = rebuilt.lower()
    if lowered.startswith("mc") and len(rebuilt) > 2:
        rebuilt = "Mc" + rebuilt[2:].capitalize()
    elif lowered.startswith("mac") and len(rebuilt) > 4:
        rebuilt = "Mac" + rebuilt[3:].capitalize()
    return rebuilt


def split_name(full_name: str | None) -> tuple[str | None, str | None]:
    """Split a printed name into (first, last), handling titles, suffixes and particles."""
    cleaned = _collapse(full_name)
    if not cleaned:
        return None, None

    # "Raghavan, Priya" — a trailing comma-separated part that is not a suffix means the
    # card used surname-first ordering.
    if cleaned.count(",") == 1:
        head, tail = (part.strip() for part in cleaned.split(","))
        tail_tokens = [_strip_dots(t) for t in tail.split()]
        if head and tail and tail_tokens and not set(tail_tokens) <= (
            _GENERATIONAL | _POST_NOMINALS
        ):
            cleaned = f"{tail} {head}"
    cleaned = cleaned.replace(",", " ")

    all_caps = cleaned == cleaned.upper() and len(cleaned) > 3
    tokens = [t for t in cleaned.split() if t.strip(".")]

    while tokens and _strip_dots(tokens[0]) in _NAME_TITLES:
        tokens.pop(0)

    generational: list[str] = []
    while tokens:
        tail = _strip_dots(tokens[-1])
        if tail in _POST_NOMINALS:
            tokens.pop()
        elif tail in _GENERATIONAL and len(tokens) > 2:
            generational.insert(0, tokens.pop())
        else:
            break

    if not tokens:
        return None, None
    if all_caps:
        tokens = [
            _smart_case(t, is_particle=(i > 0 and _strip_dots(t) in _PARTICLES))
            for i, t in enumerate(tokens)
        ]

    if len(tokens) == 1:
        return tokens[0], (" ".join(generational) or None)

    # The surname runs from the first particle onwards ("van Dijk", "de la Cruz"); with no
    # particle it is the final token and anything before it is given/middle names.
    surname_start = len(tokens) - 1
    for index in range(1, len(tokens) - 1):
        if _strip_dots(tokens[index]) in _PARTICLES:
            surname_start = index
            break

    first = " ".join(tokens[:surname_start]) or None
    last_tokens = tokens[surname_start:] + generational
    return first, (" ".join(last_tokens) or None)


def email_local_key(email: str | None) -> str:
    """Alphanumeric local part of an email, for comparing against a printed name."""
    if not email or "@" not in email:
        return ""
    return _alnum(email.rpartition("@")[0])


def _name_agrees_with_local(first: str | None, last: str | None, local_key: str) -> bool:
    """Does `local_key` look like it was built from this given/family split?"""
    given, family = _alnum(first or ""), _alnum(last or "")
    if not given or not family or not local_key:
        return False
    return local_key in (given + family, given[0] + family, given + family[0])


def reorder_name_from_email(
    first: str | None, last: str | None, email: str | None
) -> tuple[str | None, str | None, bool]:
    """Correct family-name-first cards using the email address as a second opinion.

    Singaporean, Chinese, Japanese and Hungarian cards routinely print `Tan Wei Ming` for
    the person a CRM would file as `Wei Ming Tan`. Nothing in the glyphs says which
    convention is in use — but `weiming.tan@...` does, and it is an independent,
    machine-written reading of the same name already present on the card.

    Deliberately conservative on two counts. The order is flipped only when the printed
    reading does *not* agree with the local part and the flipped reading does; and only for
    names of three or more tokens.

    That token floor matters. For a two-token name the email cannot adjudicate at all:
    `Yuki Tanaka` with `tanaka.yuki@` is equally consistent with a Western-ordered card
    using a `family.given` mailbox convention (common in Japan and parts of Europe) as it
    is with a family-first card. Both readings are self-consistent, so flipping would be a
    coin toss on correctly-extracted data. At three or more tokens the naive
    "last token is the surname" rule is genuinely unreliable and the email is decisive:
    `Tan Wei Ming` splits to a two-token given name, which `weiming.tan@` then re-partitions
    unambiguously.
    """
    local_key = email_local_key(email)
    if not local_key or not first or not last:
        return first, last, False
    if _name_agrees_with_local(first, last, local_key):
        return first, last, False
    tokens = f"{first} {last}".split()
    if len(tokens) < 3:
        return first, last, False
    flipped_first, flipped_last = " ".join(tokens[1:]), tokens[0]
    if _name_agrees_with_local(flipped_first, flipped_last, local_key):
        return flipped_first, flipped_last, True
    return first, last, False


# --------------------------------------------------------------------------- phone

_PHONE_LABEL = re.compile(
    r"^\s*(tel|telephone|phone|ph|mob|mobile|cell|direct|dir|office|off|work|fax|f|t|m|p|o|d|w)"
    r"[\s.:\-]+",
    re.IGNORECASE,
)
_PHONE_ALLOWED = re.compile(r"[^\d+()\-.\s/]")


def clean_phone_text(raw: str | None) -> str | None:
    """Strip the `Tel:` style label and keep only the first number when several are printed."""
    text = _collapse(raw)
    if not text:
        return None
    text = _PHONE_LABEL.sub("", text)
    # Cards print "+44 161 496 0118 / +44 7700 900 461" or separate lines; take the first.
    for separator in ("\n", " / ", "/", " | ", "|", ";", " or "):
        if separator in text:
            text = text.split(separator)[0]
            break
    text = _PHONE_ALLOWED.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip(" -./")
    if text.startswith("00"):
        text = "+" + text[2:].lstrip()
    return text or None


def normalise_phone(raw: str | None, region_hint: str | None) -> tuple[str | None, str | None]:
    """Return `(printed, e164)`. The printed form is kept exactly as a human would read it."""
    printed = clean_phone_text(raw)
    if not printed:
        return None, None
    if len(re.sub(r"\D", "", printed)) < 6:
        return printed, None

    candidates: list[str | None] = [None] if printed.startswith("+") else []
    if region_hint:
        candidates.append(region_hint)
    candidates.append(None)
    for region in candidates:
        try:
            parsed = phonenumbers.parse(printed, region)
        except phonenumbers.NumberParseException:
            continue
        if phonenumbers.is_valid_number(parsed):
            return printed, phonenumbers.format_number(
                parsed, phonenumbers.PhoneNumberFormat.E164
            )
    return printed, None


def phone_region(e164: str | None) -> str | None:
    if not e164:
        return None
    try:
        return phonenumbers.region_code_for_number(phonenumbers.parse(e164, None))
    except phonenumbers.NumberParseException:
        return None


# --------------------------------------------------------------------------- email

_EMAIL_LABEL = re.compile(r"^\s*(e-?mail|email|mail|e)[\s.:\-]+", re.IGNORECASE)
_TLD_TYPOS = {
    "corn": "com", "cim": "com", "con": "com", "c0m": "com", "cm": "com", "comm": "com",
    "co1n": "com", "ne7": "net", "ent": "net", "orq": "org", "0rg": "org", "lo": "io",
}
_DOMAIN_TYPOS = {
    "gmall.com": "gmail.com", "gmai.com": "gmail.com", "gmial.com": "gmail.com",
    "gmaill.com": "gmail.com", "gnail.com": "gmail.com", "grnail.com": "gmail.com",
    "hotmai.com": "hotmail.com", "hotrnail.com": "hotmail.com", "outiook.com": "outlook.com",
    "yahaa.com": "yahoo.com", "yaboo.com": "yahoo.com", "icioud.com": "icloud.com",
}
# Repairs to a ubiquitous consumer domain are accepted without a website to corroborate
# them; nobody's card says "@gmall.com" on purpose.
_WELL_KNOWN_DOMAINS = frozenset(
    {
        "gmail.com", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com",
        "protonmail.com", "proton.me", "live.com", "aol.com", "gmx.com", "qq.com",
        "163.com", "yandex.com",
    }
)


def _is_valid_email(candidate: str) -> bool:
    try:
        validate_email(candidate, check_deliverability=False)
    except EmailNotValidError:
        return False
    return True


_CHARACTER_CONFUSIONS: tuple[tuple[str, str], ...] = (
    ("rn", "m"), ("l", "i"), ("i", "l"), ("1", "l"), ("l", "1"),
    ("0", "o"), ("o", "0"), ("5", "s"), ("8", "b"),
)


def _email_repair_candidates(local: str, domain: str) -> list[tuple[str, bool]]:
    """`(candidate, from_typo_table)` — known misspellings first, character swaps after."""
    domains: list[tuple[str, bool]] = []
    if domain in _DOMAIN_TYPOS:
        domains.append((_DOMAIN_TYPOS[domain], True))
    head, _, tld = domain.rpartition(".")
    if head and tld in _TLD_TYPOS:
        domains.append((f"{head}.{_TLD_TYPOS[tld]}", True))
    for wrong, right in _CHARACTER_CONFUSIONS:
        if wrong in domain:
            domains.append((domain.replace(wrong, right), False))
    seen: set[str] = set()
    out: list[tuple[str, bool]] = []
    for candidate_domain, from_table in domains:
        if candidate_domain != domain and candidate_domain not in seen:
            seen.add(candidate_domain)
            out.append((f"{local}@{candidate_domain}", from_table))
    return out


@dataclass
class EmailResult:
    value: str | None
    valid: bool
    repaired: bool


def normalise_email(raw: str | None, website_host: str | None, company: str | None) -> EmailResult:
    """Lower-case, validate, and repair obvious OCR damage — but only when corroborated.

    An uncorroborated guess that happens to validate is worse than a flagged bad address:
    the reviewer can fix the latter, but will never notice the former.
    """
    text = _collapse(raw)
    if not text:
        return EmailResult(None, False, False)
    text = _EMAIL_LABEL.sub("", text).strip().strip("<>").rstrip(".,;")
    text = re.sub(r"\s*@\s*", "@", text).replace(" ", "").lower()
    if "@" not in text:
        return EmailResult(text or None, False, False)

    local, _, domain = text.rpartition("@")
    original_valid = _is_valid_email(text)
    company_slug = _alnum(company or "")

    # `@gmall.com` is syntactically perfect, so validity alone cannot gate the repair. The
    # gate is corroboration: a candidate is only accepted when a second, independent piece
    # of the card agrees with it.
    for candidate, from_typo_table in _email_repair_candidates(local, domain):
        if not _is_valid_email(candidate):
            continue
        candidate_domain = candidate.rpartition("@")[2]
        registrable = candidate_domain.split(".")[0]
        matches_website = website_host is not None and candidate_domain == website_host
        corroborated = (
            matches_website
            or candidate_domain in _WELL_KNOWN_DOMAINS
            or (len(registrable) >= 4 and company_slug != "" and registrable in company_slug)
        )
        if not corroborated:
            continue
        # An address that already parses is only overruled by a documented misspelling or an
        # exact website match — never by a speculative character swap.
        if original_valid and not (from_typo_table or matches_website):
            continue
        return EmailResult(candidate, True, True)

    # Keep the unrepairable original: a human reviews it, confidence gets floored.
    return EmailResult(text, original_valid, False)


# --------------------------------------------------------------------------- website

_URL_PREFIX = re.compile(r"^\s*(https?://|//)?(www\d?\.)?", re.IGNORECASE)
_HOST_RE = re.compile(r"^[a-z0-9](?:[a-z0-9\-._]*[a-z0-9])?$")


def normalise_website(raw: str | None) -> str | None:
    """Reduce a printed address to a bare host: `https://www.Northwind.io/` -> `northwind.io`."""
    text = _collapse(raw)
    if not text:
        return None
    text = text.split()[0].strip().rstrip(".,;)")
    text = _URL_PREFIX.sub("", text, count=1)
    host = text.split("/")[0].split("?")[0].split("#")[0].split("@")[-1].lower()
    host = host.split(":")[0].strip(".")
    if not host or "." not in host or not _HOST_RE.match(host):
        return _collapse(raw)
    return host


def website_is_valid(host: str | None) -> bool:
    return bool(host and "." in host and _HOST_RE.match(host))


# A printed URL and a printed email carry the same domain twice. When the two readings
# differ by a character or two, one of them is an OCR slip rather than a genuinely
# different domain, and the email is the safer source: it was already validated.
WEBSITE_REPAIR_MAX_EDITS = 2


def repair_website_from_email(host: str | None, email: str | None) -> tuple[str | None, bool]:
    """Return `(host, repaired)`, correcting small OCR damage using the email's domain.

    Observed on a real card: the model read `heliixmeridian.example` for the URL while
    reading `m.kowalczyk-reyes@helixmeridian.example` correctly. The guard is deliberately
    tight — companies really do use different domains for web and mail, so only a one or
    two character difference counts as damage.
    """
    if not host or not email or "@" not in email:
        return host, False
    domain = email.rpartition("@")[2]
    if not domain or domain == host or not website_is_valid(domain):
        return host, False
    distance = Levenshtein.distance(host, domain)
    if 0 < distance <= WEBSITE_REPAIR_MAX_EDITS:
        return domain, True
    return host, False


# --------------------------------------------------------------------------- location

_COUNTRY_TO_REGION: dict[str, str] = {
    "united states": "US", "usa": "US", "u.s.a": "US", "u.s.": "US", "america": "US",
    "united kingdom": "GB", "uk": "GB", "great britain": "GB", "england": "GB",
    "scotland": "GB", "wales": "GB", "northern ireland": "GB",
    "india": "IN", "bharat": "IN", "canada": "CA", "australia": "AU", "new zealand": "NZ",
    "germany": "DE", "deutschland": "DE", "france": "FR", "spain": "ES", "espana": "ES",
    "italy": "IT", "italia": "IT", "netherlands": "NL", "holland": "NL", "belgium": "BE",
    "switzerland": "CH", "austria": "AT", "sweden": "SE", "norway": "NO", "denmark": "DK",
    "finland": "FI", "ireland": "IE", "poland": "PL", "portugal": "PT", "greece": "GR",
    "czech republic": "CZ", "czechia": "CZ", "romania": "RO", "hungary": "HU",
    "japan": "JP", "nippon": "JP", "china": "CN", "hong kong": "HK", "taiwan": "TW",
    "south korea": "KR", "korea": "KR", "singapore": "SG", "malaysia": "MY",
    "indonesia": "ID", "thailand": "TH", "vietnam": "VN", "philippines": "PH",
    "united arab emirates": "AE", "uae": "AE", "saudi arabia": "SA", "qatar": "QA",
    "kuwait": "KW", "bahrain": "BH", "oman": "OM", "israel": "IL", "turkey": "TR",
    "egypt": "EG", "south africa": "ZA", "nigeria": "NG", "kenya": "KE", "ghana": "GH",
    "morocco": "MA", "brazil": "BR", "brasil": "BR", "mexico": "MX", "argentina": "AR",
    "chile": "CL", "colombia": "CO", "peru": "PE", "russia": "RU", "ukraine": "UA",
    "pakistan": "PK", "bangladesh": "BD", "sri lanka": "LK", "nepal": "NP",
}
# Major business hubs whose cards often omit the country entirely.
_CITY_TO_REGION: dict[str, str] = {
    "bengaluru": "IN", "bangalore": "IN", "mumbai": "IN", "delhi": "IN", "hyderabad": "IN",
    "chennai": "IN", "pune": "IN", "gurugram": "IN", "gurgaon": "IN", "noida": "IN",
    "london": "GB", "manchester": "GB", "birmingham": "GB", "edinburgh": "GB",
    "new york": "US", "san francisco": "US", "austin": "US", "chicago": "US", "boston": "US",
    "seattle": "US", "los angeles": "US", "denver": "US", "atlanta": "US", "miami": "US",
    "toronto": "CA", "vancouver": "CA", "montreal": "CA",
    "berlin": "DE", "munich": "DE", "münchen": "DE", "hamburg": "DE", "frankfurt": "DE",
    "paris": "FR", "lyon": "FR", "madrid": "ES", "barcelona": "ES", "milan": "IT",
    "rome": "IT", "amsterdam": "NL", "rotterdam": "NL", "utrecht": "NL", "brussels": "BE",
    "zurich": "CH", "geneva": "CH", "vienna": "AT", "stockholm": "SE", "oslo": "NO",
    "copenhagen": "DK", "helsinki": "FI", "dublin": "IE", "warsaw": "PL", "lisbon": "PT",
    "tokyo": "JP", "osaka": "JP", "yokohama": "JP", "shanghai": "CN", "beijing": "CN",
    "shenzhen": "CN", "seoul": "KR", "taipei": "TW", "sydney": "AU", "melbourne": "AU",
    "brisbane": "AU", "auckland": "NZ", "dubai": "AE", "abu dhabi": "AE", "doha": "QA",
    "riyadh": "SA", "tel aviv": "IL", "istanbul": "TR", "cairo": "EG",
    "johannesburg": "ZA", "cape town": "ZA", "lagos": "NG", "nairobi": "KE",
    "sao paulo": "BR", "rio de janeiro": "BR", "mexico city": "MX", "buenos aires": "AR",
}


# Postcodes carry no lead value and the contract asks for "City, Region, Country".
# Ordered: the UK pattern is matched before the bare-digit one, otherwise "EC2A 4QT"
# would lose only its "4QT" half and leave a dangling fragment.
_POSTCODE_PATTERNS = (
    re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s+\d[A-Z]{2}\b", re.IGNORECASE),  # GB EC2A 4QT
    re.compile(r"\b\d{3}-\d{4}\b"),                                        # JP 107-0052
    re.compile(r"\b\d{4,6}\b"),                                            # 02220, 411057
)

# A segment beginning with a house number, or naming a street type, is an address line
# rather than a place. Bare two-letter abbreviations are deliberately excluded: `\bst\b`
# would eat "St Petersburg". Words that double as place names ("park", "way") are out too.
_STREET_LEAD = re.compile(r"^\s*(#|\d)")
_STREET_WORDS = re.compile(
    r"\b(street|road|avenue|lane|drive|boulevard|suite|floor|level|block|unit|plot"
    r"|tower|quay|wharf|plaza|building|bldg|chome|st\.|rd\.|ave\.|blvd\.|ste\.|fl\.)\b",
    re.IGNORECASE,
)


def _is_address_line(segment: str) -> bool:
    return bool(_STREET_LEAD.match(segment) or _STREET_WORDS.search(segment))


def strip_address_detail(text: str) -> str:
    """Reduce a printed address to the place: drop street lines, drop postcodes.

    The model transcribes what it sees, which on most cards is the full postal address.
    The contract wants "City, Region, Country", so the reduction happens here rather than
    by asking the model more insistently — a deterministic rule is repeatable and a prompt
    instruction is not. Never returns empty: if every segment looks like an address line,
    the original is kept for the reviewer to judge.
    """
    segments = [s.strip() for s in text.split(",")]
    kept = [s for s in segments if s and not _is_address_line(s)]
    if not kept:
        return text
    cleaned: list[str] = []
    for segment in kept:
        for pattern in _POSTCODE_PATTERNS:
            segment = pattern.sub(" ", segment)
        segment = re.sub(r"\s+", " ", segment).strip(" -")
        if segment:
            cleaned.append(segment)
    return ", ".join(cleaned) if cleaned else text


def normalise_location(raw: str | None) -> str | None:
    text = _collapse(raw)
    if not text:
        return None
    text = re.sub(r"^\s*(address|addr|a)[\s.:\-]+", "", text, flags=re.IGNORECASE).strip()
    return strip_address_detail(text) or None


def region_from_location(location: str | None) -> str | None:
    """Best-effort ISO-3166 alpha-2 for the address printed on the card."""
    if not location:
        return None
    folded = _ascii_fold(location).lower()
    parts = [p.strip() for p in re.split(r"[,\n|]", folded) if p.strip()]
    for part in reversed(parts):
        key = part.strip(". ")
        if key in _COUNTRY_TO_REGION:
            return _COUNTRY_TO_REGION[key]
    for part in reversed(parts):
        for city, region in _CITY_TO_REGION.items():
            if city in part:
                return region
    return None
