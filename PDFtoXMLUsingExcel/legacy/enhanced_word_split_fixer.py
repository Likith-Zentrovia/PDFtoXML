"""
Utilities to clean up word splits introduced by PDF extraction.

Many PDFs insert hard line breaks mid-word or break hyphenated words
across lines (e.g. ``AI-\nassisted`` or ``pre-\nprocessing``).  The legacy
pipeline stubbed this module out which left artefacts like ``computa-\ntion``
in the final XML.  The heuristics expect this helper to normalise text.

`fix_word_splits_enhanced` embraces a conservative approach:

* Preserve explicit hyphenation (``AI-assisted``) but collapse embedded
  newlines/spaces.
* Join hard line-break splits that occur mid-word without punctuation.
* Collapse multiple internal whitespace runs produced by the reader.
* Leave numbers, acronyms, and ordinary sentence breaks untouched.
"""

from __future__ import annotations

import re

# Precompiled regular expressions reused in the cleaner
_HYPHEN_LINEBREAK_RE = re.compile(r"(\w)-\s*\n\s*(\w)")
_SOFT_LINEBREAK_RE = re.compile(r"([^\s-])\s*\n\s*([a-z])")
_LIGATURE_SPACING_RE = re.compile(r"(\w)\s{2,}(\w)")
_MULTISPACE_RE = re.compile(r"[ \t]{2,}")
# Pattern for end-of-line hyphenation already flattened (hyphen + space + lowercase continuation)
# Matches: "man- agement", "Mag- netic" but not "MRI-" at end or "AI- " before uppercase
_HYPHEN_SPACE_CONTINUATION_RE = re.compile(r"(\w{2,})- ([a-z]{2,})")
# Pattern for direct hyphenation without space (common in PDF extraction)
# Matches: "elec-tromagnetic", "in-cluding" where hyphen breaks a single word
_DIRECT_HYPHEN_RE = re.compile(r"\w-[a-z]")

# Pattern for single-space word splits from PDF kerning
# Matches patterns like "Com pany", "Se nior", "Proj ect" where space splits a word
# Extended to catch fragments up to 8 chars for suffixes like "nization", "agement"
# Also matches when followed by digits (e.g., "Com pany1915")
_SINGLE_SPACE_SPLIT_RE = re.compile(r"([A-Za-z]{2,}) ([a-z]{1,8})(?=[\s\.,;:!?\)\]\"\'\d]|$)")

# Common word fragment patterns that indicate a single space is incorrectly splitting a word
# These are fragments that are NOT standalone English words and should be rejoined
# Includes very short fragments (1-2 chars) that commonly appear in kerning splits
_KERNING_FRAGMENT_PATTERNS = re.compile(
    r"^("
    # Single character fragments (often from kerning)
    r"c|i|a|e|o|u|"
    # Two-character fragments
    r"ca|ce|ci|co|cu|"
    r"ga|ge|gi|go|gu|"
    r"re|ri|ro|ru|"
    r"na|ne|ni|no|nu|"
    r"ta|te|ti|to|tu|"
    r"ba|be|bi|bo|bu|"
    r"la|le|li|lo|lu|"
    r"ma|me|mi|mo|mu|"
    r"pa|pe|pi|po|pu|"
    r"sa|se|si|so|su|"
    r"ag|"
    # Three-character fragments
    r"ect|cal|ics|ous|nal|ial|"
    r"ity|ary|ory|ery|"
    # Four-character fragments
    r"pany|nior|ally|nifi|bral|tion|sion|ment|ness|city|"
    r"ical|ious|eous|tive|able|ible|ular|"
    r"lish|nize|ture|"
    # Five-character fragments
    r"ction|ation|ition|ution|nally|tions|sions|ments|cally|"
    r"ially|ually|ously|ively|"
    r"glish|"  # En glish
    # Longer common suffixes
    r"nition|nitions|nization|ization|izations|"
    r"agement|agements|"
    r"ference|ferences|"
    r"brate|brated|brating|"
    # Legacy patterns from original list
    r"er|ers|ter|ters|"
    r"gy|gies|try|tries|"
    r"nic|nics|nals|ty|ties|"
    r"ware|ward|wards|ble|bles|bly|"
    r"cess|cesses|cial|cially|"
    r"tain|tains|tained|taining|"
    r"tures|tured|turing|"
    r"lize|lized|lizing|lization|"
    r"tives|tively|"
    r"ence|ences|ance|ances|"
    r"ual|ually|ure|ures|"
    r"cate|cated|cating|cation|cations|"
    r"nesses|less|lessly|"
    r"age|ages|aged|aging|"
    r"ized|izing|"
    r"ese|ish|dom|doms|"
    r"ar|ars|"
    r"or|ors|ist|ists|ism|isms|"
    r"ful|fully|ship|ships|"
    r"hood|hoods|like|wise|"
    r"ways|"
    r"cy|cies|"
    r"th|ths|"  # "weal th", "heal th"
    r"ly|ry|ries"  # common endings
    r")$",
    re.IGNORECASE
)

# Common word-ending suffixes that indicate the right part is a fragment, not a standalone word
# If right part matches these patterns, it's likely a broken word that should be rejoined
_FRAGMENT_SUFFIX_PATTERNS = re.compile(
    r"^(tromagnetic|tromagnet|tronic|tronics|trical|tric|tron|"  # electro- breaks
    r"cluding|clusion|clusive|clude|"  # in-clude breaks
    r"erence|erences|ference|ferences|"  # ref-erence, dif-ference breaks
    r"ation|ations|ment|ments|tion|tions|sion|sions|"  # common suffixes
    r"agement|agements|"  # man-agement breaks
    r"ity|ities|ness|nesses|ance|ances|ence|ences|"
    r"able|ible|ably|ibly|ful|fully|less|lessly|"
    r"ing|ings|tion|tions|ly|ment|ments|"
    r"ized|izing|ization|ised|ising|isation|"
    r"ology|ological|ologist|"
    r"ular|ulars|ularly|ulate|ulated|ulating|ulation|"
    r"ative|atively|atives|"
    r"ous|ously|ousness|"
    r"ical|ically|"
    r"lated|lating|lation|lations|"  # re-lated breaks
    r"formed|forming|formation|"  # trans-formed breaks
    r"pared|paring|paration|"  # pre-pared breaks
    r"vious|viously|"  # pre-vious, ob-vious breaks
    r"netic|netics|netically|"  # mag-netic breaks
    r"dition|ditional|ditionally|"  # con-dition, tra-dition breaks
    r"ticular|ticularly|"  # par-ticular breaks
    r"cording|cordingly|"  # ac-cording breaks
    r"quent|quently|quence|"  # fre-quent, se-quence breaks
    r"tain|tained|taining|tains|"  # con-tain, main-tain breaks
    r"plied|plying|plication|plications|"  # ap-plied, im-plied breaks
    r"proved|proving|provement|"  # im-proved, ap-proved breaks
    r"duced|ducing|duction|"  # pro-duced, re-duced, in-duced breaks
    r"posed|posing|position|"  # pro-posed, com-posed breaks
    r"vided|viding|vision|"  # pro-vided, di-vided breaks
    r"cessed|cessing|cess|"  # pro-cessed, ac-cess breaks
    r"pected|pecting|pection|"  # ex-pected, in-spected breaks
    r"signed|signing|"  # de-signed, as-signed breaks
    r"quired|quiring|quirement|"  # re-quired, ac-quired breaks
    r"ceived|ceiving|ception|"  # re-ceived, per-ceived breaks
    r"sented|senting|sentation|"  # pre-sented, repre-sented breaks
    r"veloped|veloping|velopment|"  # de-veloped breaks
    r"termine|termined|termining|termination)$",  # de-termine breaks
    re.IGNORECASE
)


def _fix_hyphenated_linebreaks(text: str) -> str:
    """Collapse ``word-\ncontinuation`` to ``word-continuation``."""

    def _repl(match: re.Match[str]) -> str:
        left, right = match.group(1), match.group(2)
        return f"{left}-{right}"

    return _HYPHEN_LINEBREAK_RE.sub(_repl, text)


def _fix_soft_linebreaks(text: str) -> str:
    """
    Replace bare line-break splits between lowercase fragments with a space.

    Example::
        ``comput\nation`` -> ``comput ation`` -> subsequently normalised.
    """

    def _repl(match: re.Match[str]) -> str:
        left, right = match.group(1), match.group(2)
        return f"{left} {right}"

    return _SOFT_LINEBREAK_RE.sub(_repl, text)


def _fix_ligature_spacing(text: str) -> str:
    """
    Collapse excessive intra-word spacing often seen around ligatures.
    """

    def _repl(match: re.Match[str]) -> str:
        return f"{match.group(1)}{match.group(2)}"

    return _LIGATURE_SPACING_RE.sub(_repl, text)


def _fix_flattened_hyphenation(text: str) -> str:
    """
    Fix end-of-line hyphenation that has been flattened to ``word- continuation``.

    PDF text extraction often produces patterns like ``man- agement`` or ``Mag- netic``
    where a word was hyphenated at line end. This joins them back: ``management``, ``Magnetic``.

    Only matches when:
    - Left part has 2+ word chars (avoids single-letter cases)
    - Right part starts lowercase with 2+ chars (indicates continuation, not new word)
    """
    def _repl(match: re.Match[str]) -> str:
        left, right = match.group(1), match.group(2)
        return f"{left}{right}"

    return _HYPHEN_SPACE_CONTINUATION_RE.sub(_repl, text)


def _fix_single_space_splits(text: str) -> str:
    """
    Fix single-space word splits caused by PDF kerning/letter-spacing.

    Examples:
        - "Com pany" -> "Company"
        - "Se nior" -> "Senior"
        - "Proj ect" -> "Project"
        - "Man ag er" -> "Manager" (applied iteratively)
        - "Fi nally" -> "Finally"
        - "defi nition" -> "definition"
        - "scientifi c" -> "scientific"

    Strategy: Look for patterns where a short lowercase fragment (1-8 chars) follows
    a word portion and the fragment matches common word-ending patterns that are NOT
    standalone English words.
    """
    # Process word by word to handle cases like "defi nition" where both parts are lowercase
    # First apply the standard regex for patterns like "Com pany", "Se nior"
    max_iterations = 5
    result = text

    for _ in range(max_iterations):
        new_result = _SINGLE_SPACE_SPLIT_RE.sub(_single_space_repl, result)
        if new_result == result:
            break
        result = new_result

    # Second pass: handle consecutive lowercase word fragments like "defi nition"
    # These are missed by the first pass because the regex matches greedily from left
    words = result.split(' ')
    merged_words = []
    i = 0
    while i < len(words):
        word = words[i]
        # Check if this word + next word should be joined
        if i + 1 < len(words):
            next_word = words[i + 1]
            if next_word:
                # Strip trailing punctuation and digits for pattern matching
                # e.g., "ect." -> "ect", "c2" -> "c" for pattern check, but keep original in result
                next_word_stripped = next_word.rstrip('.,;:!?\'\")\d0123456789')
                # Check if next_word is a kerning fragment that should be joined
                if next_word_stripped and _KERNING_FRAGMENT_PATTERNS.match(next_word_stripped):
                    # Check if word ends with letters (not punctuation) and next_word starts lowercase
                    if word and word[-1].isalpha() and next_word[0].islower():
                        # Join them (keep original punctuation)
                        merged_words.append(word + next_word)
                        i += 2
                        continue
        merged_words.append(word)
        i += 1

    return ' '.join(merged_words)


def _single_space_repl(match: re.Match[str]) -> str:
    """Replacement function for single-space word split patterns."""
    left, right = match.group(1), match.group(2)

    # Check if right part looks like a word fragment (not a standalone word)
    if _KERNING_FRAGMENT_PATTERNS.match(right):
        return f"{left}{right}"

    # Keep space for legitimate word pairs
    return match.group(0)


def _fix_direct_hyphenation(text: str) -> str:
    """
    Fix direct hyphenation without space (e.g., ``elec-tromagnetic`` -> ``electromagnetic``).

    PDF text extraction sometimes produces hyphenated words directly joined without space.
    This is different from legitimate compound words like ``high-quality``.

    Strategy:
    - Only remove hyphens where the right part matches known word-fragment patterns
    - This preserves legitimate compound words while fixing PDF extraction artifacts
    """
    # Pattern to find word-hyphen-word sequences
    # Matches: "elec-tromagnetic", "in-cluding", "MRI-re-lated"
    pattern = re.compile(r"(\w+)-([a-z]{2,})")

    def _repl(match: re.Match[str]) -> str:
        left, right = match.group(1), match.group(2)
        # Only join if right part looks like a word fragment (not a standalone word)
        if _FRAGMENT_SUFFIX_PATTERNS.match(right):
            return f"{left}{right}"
        # Keep hyphen for legitimate compound words
        return match.group(0)

    return pattern.sub(_repl, text)


def fix_word_splits_enhanced(text: str) -> str:
    """
    Best-effort normalisation for PDF derived text.

    The function intentionally performs several small passes rather than
    a monolithic regex so that each transformation stays easy to reason
    about and can be tuned independently.
    """
    if not text:
        return text

    # Check if any processing is needed
    # Include direct hyphenation check for patterns like "elec-tromagnetic"
    # Include single-space split check for patterns like "Com pany", "Se nior"
    needs_processing = (
        "\n" in text or
        "  " in text or
        "- " in text or
        " " in text or  # Check for single spaces (for kerning splits)
        _DIRECT_HYPHEN_RE.search(text) is not None
    )
    if not needs_processing:
        return text

    cleaned = text
    cleaned = _fix_hyphenated_linebreaks(cleaned)
    cleaned = _fix_soft_linebreaks(cleaned)
    cleaned = _fix_flattened_hyphenation(cleaned)  # Fix "word- continuation" patterns
    cleaned = _fix_direct_hyphenation(cleaned)  # Fix "word-continuation" patterns (no space)
    cleaned = _fix_single_space_splits(cleaned)  # Fix "Com pany", "Se nior" kerning splits
    cleaned = _fix_ligature_spacing(cleaned)

    # Collapse remaining multi-spaces but preserve intentional indentation by
    # leaving leading whitespace per line untouched.
    def _collapse(multispace_match: re.Match[str]) -> str:
        return " "

    cleaned = _MULTISPACE_RE.sub(_collapse, cleaned)

    # Normalise ``-\n`` patterns that may remain after other substitutions.
    cleaned = cleaned.replace("-\n", "-")

    # Finally collapse residual line breaks that split mid-word (but retain
    # truly blank lines).
    cleaned = re.sub(r"(\S)\n(\S)", r"\1 \2", cleaned)

    return cleaned


__all__ = ["fix_word_splits_enhanced"]
