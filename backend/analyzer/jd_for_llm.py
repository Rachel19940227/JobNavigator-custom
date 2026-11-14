"""Prepare job-description text for LLM scoring and resume tailoring.

The full posting remains stored on ``Job.description``.  This module only
removes low-value boilerplate at the prompt boundary, before the existing
character limits are applied.
"""
import re


_LOW_VALUE_HEADING_RE = re.compile(
    r"^(?:"
    r"about\s+(?:us|the\s+company|our\s+company)|company\s+(?:overview|description|profile)|"
    r"who\s+we\s+are|our\s+(?:company|story|culture|values|mission)|"
    r"benefits?|perks?|what\s+we\s+offer|why\s+(?:join|work\s+with)\s+us|"
    r"equal\s+(?:employment\s+)?opportunity|eeo|diversity(?:\s*(?:&|and)\s*inclusion)?|"
    r"affirmative\s+action|reasonable\s+accommodations?|accommodations?|"
    r"how\s+to\s+apply|application\s+(?:instructions?|process)"
    r")$",
    re.IGNORECASE,
)

_SECTION_HEADING_RE = re.compile(
    r"^(?:"
    r"job\s+(?:description|summary|overview)|position\s+(?:description|summary|overview)|"
    r"the\s+(?:role|opportunity)|role\s+(?:description|summary|overview)|"
    r"what\s+you(?:'|’)ll\s+do|what\s+you\s+will\s+do|responsibilities|duties|"
    r"key\s+(?:responsibilities|duties|accountabilities)|"
    r"requirements|qualifications|minimum\s+qualifications|preferred\s+qualifications|"
    r"what\s+you(?:'|’)ll\s+bring|what\s+you\s+will\s+bring|what\s+we(?:'|’)re\s+looking\s+for|"
    r"skills|experience|education|certifications|location|work\s+arrangement|compensation|salary|"
    r"about\s+(?:us|the\s+company|our\s+company)|company\s+(?:overview|description|profile)|"
    r"who\s+we\s+are|our\s+(?:company|story|culture|values|mission)|"
    r"benefits?|perks?|what\s+we\s+offer|why\s+(?:join|work\s+with)\s+us|"
    r"equal\s+(?:employment\s+)?opportunity|eeo|diversity(?:\s*(?:&|and)\s*inclusion)?|"
    r"affirmative\s+action|reasonable\s+accommodations?|accommodations?|"
    r"how\s+to\s+apply|application\s+(?:instructions?|process)"
    r")$",
    re.IGNORECASE,
)

_BOILERPLATE_PARAGRAPH_RE = re.compile(
    r"\b(?:"
    r"equal opportunity employer|equal employment opportunity|affirmative action employer|"
    r"does not discriminate (?:on|based)|committed to (?:building|creating|providing|fostering) "
    r"(?:a )?(?:diverse|inclusive|equal)|"
    r"reasonable accommodation (?:to|for|during)|accommodation to (?:complete|participate)|"
    r"contact us (?:at|if you require).*accommodation"
    r")\b",
    re.IGNORECASE | re.DOTALL,
)


def _heading_text(line: str) -> str | None:
    """Return normalized text when a line looks like a known section heading."""
    candidate = line.strip().strip("#*_- ").rstrip(":").strip()
    if not candidate or len(candidate) > 80:
        return None
    if _SECTION_HEADING_RE.fullmatch(candidate):
        return candidate
    return None


def prepare_jd_for_llm(text: str | None, max_chars: int) -> str:
    """Remove low-value JD sections and boilerplate, then apply ``max_chars``.

    Section removal is deliberately conservative: only recognized heading lines
    start a removable block.  Legal boilerplate is also removed when it forms a
    standalone paragraph.  If cleaning would erase the posting, the original is
    retained so existing scoring/tailoring behavior fails safe.
    """
    original = (text or "").strip()
    if not original or max_chars <= 0:
        return ""

    kept_lines = []
    skipping = False
    for line in original.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        heading = _heading_text(line)
        if heading is not None:
            skipping = bool(_LOW_VALUE_HEADING_RE.fullmatch(heading))
        if not skipping:
            kept_lines.append(line)

    cleaned = "\n".join(kept_lines)
    paragraphs = re.split(r"\n\s*\n", cleaned)
    if len(paragraphs) > 1:
        paragraphs = [p for p in paragraphs if not _BOILERPLATE_PARAGRAPH_RE.search(p)]
        cleaned = "\n\n".join(paragraphs)
    else:
        # ATS plaintext often uses one line per HTML element without blank lines.
        # In that shape, remove only the matching line rather than mistaking the
        # entire posting for one legal paragraph.
        cleaned = "\n".join(
            line for line in cleaned.split("\n")
            if not _BOILERPLATE_PARAGRAPH_RE.search(line)
        )
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    return (cleaned or original)[:max_chars]
