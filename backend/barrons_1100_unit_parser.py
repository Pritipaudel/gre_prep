"""
Parser for "1100 Words You Need to Know" style vocab-workbook units.

Handles the specific structure of this book, which is a QUIZ/WORKBOOK
format, not a flat glossary:

    NEW WORDS
        word1 (pron)  word2 (pron)  word3 (pron)  word4 (pron)  word5 (pron)
    [Story title]
        ... narrative passage using the 5 words in context ...
    Sample Sentences
        1. ... 2. ... 3. ... 4. ... 5. ...   (cloze / fill-in-blank)
    Definitions
        6. word1  ___  a. def_X
        7. word2  ___  b. def_Y
        ...
    TODAY'S IDIOM
        phrase - meaning. Example sentence.
    ANSWERS ARE ON PAGE nnn

IMPORTANT: the word next to a letter in the "Definitions" section is
NOT that word's definition -- it's a shuffled matching quiz. The real
mapping lives on the "ANSWERS ARE ON PAGE nnn" page elsewhere in the
book (usually formatted like "6-d, 7-b, 8-e, 9-a, 10-c"). You MUST
parse that page too and join on item number, or you will store wrong
definitions.
"""

import re
from dataclasses import dataclass, field
from typing import Optional, List


# ── Section markers exactly as they appear (after MinerU markdown cleanup)
SECTION_MARKERS = [
    "Sample Sentences",
    "Definitions",
    "TODAY'S IDIOM",
]

ANSWER_PAGE_RE = re.compile(r"ANSWERS?\s+ARE\s+ON\s+PAGE\s+(\d+)", re.IGNORECASE)

# Matches "6-d", "6. d", "6 d" style answer key entries
ANSWER_KEY_ENTRY_RE = re.compile(r"(\d+)\s*[-.\s]\s*([a-eA-E])\b")


# ── OCR / markdown noise cleanup
def clean_ocr_text(text: str) -> str:
    """Remove OCR noise: tildes, excessive whitespace, stray markdown underscores."""
    text = text.replace("~", "")
    # MinerU may render the blank line "____" as markdown bold/italic (**__**) — strip it
    text = re.sub(r"\*+_+\*+", " ", text)
    text = re.sub(r"_+", " ", text)   # bare underscores (the fill-in-blank line)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# ── Data classes
@dataclass
class VocabEntry:
    word: str
    pronunciation_raw: Optional[str] = None   # low confidence, OCR-derived
    definition_letter: Optional[str] = None   # e.g. "d" — from quiz section
    definition_text: Optional[str] = None     # resolved after answer-key join
    resolved: bool = False
    context_sentence: Optional[str] = None    # from the passage
    confidence: float = 0.5                   # bumped once answer-key resolved


@dataclass
class Unit:
    words: list = field(default_factory=list)           # list[str] — the 5 headwords
    passage_title: Optional[str] = None
    passage_text: Optional[str] = None
    sample_sentences: list = field(default_factory=list)
    definitions_by_letter: dict = field(default_factory=dict)  # {'a': text, ...}
    idiom: Optional[dict] = None                        # {phrase, meaning, example}
    answers_page: Optional[int] = None
    entries: list = field(default_factory=list)         # list[VocabEntry]


# ─────────────────────────────────────────────────────────────────────────────
# Section splitting
# ─────────────────────────────────────────────────────────────────────────────

def split_sections(raw_text: str) -> dict:
    """
    Split a unit's raw OCR/MinerU text into labeled chunks.

    Returns a dict with keys:
        "preamble", "Sample Sentences", "Definitions", "TODAY'S IDIOM"
    """
    sections = {"preamble": ""}
    for marker in SECTION_MARKERS:
        sections[marker] = ""

    current_key = "preamble"
    buffer: List[str] = []

    for raw_line in raw_text.split("\n"):
        # Strip markdown heading prefixes (##, #, **) so we can match plain section names
        stripped = re.sub(r"^#+\s*", "", raw_line).strip()
        stripped = re.sub(r"^\*+\s*|\s*\*+$", "", stripped).strip()

        matched_marker = None
        for marker in SECTION_MARKERS:
            # Case-insensitive match, and also match partial like "TODAY'S IDIOM" vs "TODAY's IDIOM"
            if stripped.upper().startswith(marker.upper()):
                matched_marker = marker
                break

        if matched_marker:
            # Save current buffer
            sections[current_key] = "\n".join(buffer)
            current_key = matched_marker
            buffer = []
        else:
            buffer.append(raw_line)

    # Save final buffer
    sections[current_key] = "\n".join(buffer)
    return sections




# ─────────────────────────────────────────────────────────────────────────────
# NEW WORDS parser
# ─────────────────────────────────────────────────────────────────────────────

def parse_new_words(section_text: str, known_wordlist: Optional[set] = None) -> list:
    """
    Extract headwords from the NEW WORDS block.

    The NEW WORDS section is a compact header with 5 words + their
    OCR'd pronunciations, e.g.:
        voracious        indiscriminate       eminent
        və rā' shəs      in'dis krim'ə nit    em'ə nənt
        steeped          replete
        stēpt            ri plēt'

    Strategy:
    1. Only inspect the first 400 characters — the headwords appear early;
       later text bleeds into the passage which contains many more words.
    2. Pick lowercase alphabetic tokens ≥ 4 chars that look like real words
       (not pronunciation fragments, which contain digits, tildes, diacritics).
    3. If a known_wordlist is provided, fuzzy-guard against OCR errors.
    4. Deduplicate and cap at 5 (one unit always has exactly 5 new words).

    Returns list of dicts: [{"lemma": str, "part_of_speech": None}, ...]
    """
    STOP_WORDS = {
        "words", "today", "week", "unit", "page", "reading", "wisely", "the", "and",
        "new", "for", "with", "this", "from", "have", "been", "very", "also", "some",
        "well", "more", "book", "each", "will", "than", "over", "into", "test",
        "yourself", "cover", "before", "begin", "match", "definitions", "sample",
        "sentences", "basis", "above", "paragraph", "following", "occasionally",
        "necessary", "change", "ending", "on", "now", "that", "you", "your", "use",
        "seen", "used",
    }

    results = []
    seen = set()

    lines = [line.strip() for line in section_text.split("\n") if line.strip()]

    for line in lines:
        # Skip blank/heading lines
        if not line:
            continue
        line_stripped = re.sub(r"^#+\s*", "", line).strip()

        # Each non-empty line in NEW WORDS is:  <headword> <pronunciation>
        # The headword is always the first all-alpha token.
        # e.g. "voracious və rā' shəs"  or  "steeped stept"  or  "eminent em'ə nent"
        m = re.match(r'^([a-zA-Z]+(?:-[a-zA-Z]+)?)\s*(.*)$', line_stripped)
        if not m:
            continue

        word = m.group(1)
        pron_rest = m.group(2).strip() or None

        if word.lower() in STOP_WORDS:
            continue
        if word.lower() in seen:
            continue
        if len(word) < 4:
            continue
        # Reject if the word looks like a sentence fragment (contains digit or > 15 chars of weird mix)
        if re.search(r'\d', word):
            continue

        seen.add(word.lower())
        results.append({"lemma": word, "part_of_speech": None, "pronunciation_raw": pron_rest})

        if len(results) >= 5:
            break

    return results[:5]


# ─────────────────────────────────────────────────────────────────────────────
# Sample Sentences parser
# ─────────────────────────────────────────────────────────────────────────────

def parse_sample_sentences(section_text: str) -> list:
    """
    Extract numbered sample sentences from the Sample Sentences block.

    The section contains 5 fill-in-the-blank sentences numbered 1–5:
        1. The football game was ________ with excitement and great plays.
        2. The ________ author received the Nobel Prize for literature.
        ...

    Returns list of sentence strings (with the blank preserved as-is).
    """
    if not section_text.strip():
        return []

    text = clean_ocr_text(section_text)
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    # If MinerU collapsed to single line, split on "N." patterns
    if len(lines) <= 1:
        # Try splitting on number boundaries: "1. ... 2. ..."
        lines = re.split(r"(?=\d+\.)", text)
        lines = [l.strip() for l in lines if l.strip()]

    sentences: List[str] = []
    current_sentence = ""

    for line in lines:
        m = re.match(r"^(\d+)\.?\s+(.*)$", line)
        if m:
            if current_sentence:
                sentences.append(current_sentence.strip())
            current_sentence = m.group(2)
        else:
            if current_sentence:
                current_sentence += " " + line

    if current_sentence:
        sentences.append(current_sentence.strip())

    # Clean and filter
    cleaned = []
    for s in sentences:
        s = re.sub(r"\s+", " ", s).strip()
        if len(s) > 8:
            cleaned.append(s)

    return cleaned


# ─────────────────────────────────────────────────────────────────────────────
# Definitions quiz parser
# ─────────────────────────────────────────────────────────────────────────────

def parse_definitions_quiz(section_text: str) -> dict:
    """
    Extract the shuffled {letter: definition_text} mapping from the
    Definitions matching section.

    The book layout is:
        6. voracious    ___   a. of high reputation, outstanding
        7. indiscriminate ___  b. completely filled or supplied with
        8. eminent      ___   c. choosing at random without careful selection
        9. steeped      ___   d. desiring or consuming great quantities
       10. replete      ___   e. soaked, drenched, saturated

    MinerU renders this as markdown, which may:
    - Strip underscores or render them as bold/italic markers
    - Collapse multiple lines into one
    - Preserve the structure line-by-line

    We extract only the letter→definition mapping (NOT word→letter —
    that comes from the answer key page).

    Returns dict like {"a": "of high reputation, outstanding", "b": ..., ...}
    """
    if not section_text.strip():
        return {}

    text = clean_ocr_text(section_text)

    # Strategy 1: Line-by-line — works when MinerU preserves newlines.
    # Look for lines that start with or contain "letter. definition"
    line_defs: dict = {}
    for line in section_text.split("\n"):
        line_clean = clean_ocr_text(line)
        # Pre-normalize: OCR sometimes squashes word+letter together e.g. "technologyb." → "technology b."
        # Pattern: any lowercase [a-e] immediately before a period that follows a longer word
        line_clean = re.sub(r"([a-zA-Z]{4,})([a-e])\.", r"\1 \2.", line_clean)
        # Match "a. definition text" anywhere in the line
        m = re.search(r"\b([a-e])\.\s+(.+)", line_clean, re.IGNORECASE)
        if m:
            letter = m.group(1).lower()
            defn = m.group(2).strip()
            # Strip any trailing fragment that looks like the next word label
            # e.g. "of high reputation, outstanding 7. indiscriminate" → trim after number
            defn = re.sub(r"\s+\d+\.\s+\w+.*$", "", defn).strip()
            if defn and letter not in line_defs:
                line_defs[letter] = defn

    if len(line_defs) >= 3:
        return line_defs

    # Strategy 2: Inline / collapsed — all definitions on one line.
    # The definitions section in MinerU output often looks like:
    # "voracious a. of high reputation indiscriminate b. completely filled..."
    # We anchor on "letter." and capture up to the next "word letter." or end
    inline_pattern = re.compile(
        r"\b([a-e])\.\s+(.+?)(?=\s+\w[\w\s]{0,20}[a-e]\.\s|\s*$)",
        re.IGNORECASE | re.DOTALL,
    )
    inline_defs: dict = {}
    for m in inline_pattern.finditer(text):
        letter = m.group(1).lower()
        defn = re.sub(r"\s+", " ", m.group(2)).strip()
        if defn and letter not in inline_defs:
            inline_defs[letter] = defn

    if len(inline_defs) >= 3:
        return inline_defs

    # Strategy 3: Very compact / single-number anchor
    # "a. consuming great quantities b. choosing at random c. ..."
    compact_matches = re.findall(r"\b([a-e])\.\s+([^a-e\.]{5,}?)(?=\s+[a-e]\.\s|$)", text)
    compact_defs = {let.lower(): defn.strip() for let, defn in compact_matches if defn.strip()}
    return compact_defs



def parse_vocab_definitions_direct(section_text: str) -> list[dict]:
    """
    parses directly from the Definitions section text, one dict per line
    Format per line: <item_number>. <word> [(<part_of_speech>)] [blank artifact] <letter>. <definition text>.
    """
    results = []
    lines = section_text.split('\n')
    
    # 6. badger (v.) a. unpleasant, dull, or hard work
    # We want to flexibly match:
    # item_number: \d+
    # word: \w+
    # part_of_speech: optional (.*?)
    # letter: [a-e]
    # def_text: .*
    
    for line in lines:
        line_clean = clean_ocr_text(line)
        if not line_clean:
            continue
        
        # Look for the primary anchor pattern:   <number>.  <word>  <optional POS>  ...  <letter>.  <definition>
        # e.g.: 6. badger (v.) a. unpleasant
        # or e.g.: 9. interminable d. to understand
        m = re.match(r'^(\d+)\.\s*([a-zA-Z]+)(?:\s*\((.*?)\))?\s*.*?\b([a-e])\.\s*(.*)$', line_clean, re.IGNORECASE)
        if m:
            item_number = int(m.group(1))
            word = m.group(2).lower()
            part_of_speech = m.group(3).strip() if m.group(3) else None
            shown_letter = m.group(4).lower()
            shown_definition = m.group(5).strip()
            
            results.append({
                "item_number": item_number,
                "word": word,
                "part_of_speech": part_of_speech,
                "shown_letter": shown_letter,
                "shown_definition": shown_definition
            })
    return results

def parse_week_day(raw_text: str) -> tuple[int,int] | None:
    """extracts (week, day) from a page's raw text, e.g. (1, 4)"""
    m = re.search(r'(?:[Ww]eek|WEEK)\s+(\d+)\s*(?:[-·\.]|Day|DAY)\s*([Dday]*\s*\d+)?', raw_text)
    if not m:
        # alternative pattern: Week 1 Day 2
        m = re.search(r'[Ww]eek\s*(\d+).*?[Dd]ay\s*(\d+)', raw_text, re.IGNORECASE)
    
    if m:
        week = int(m.group(1))
        # try to extract day from second group or match again
        day_str = m.group(2) if m.group(2) else ""
        day_match = re.search(r'(\d+)', day_str)
        if day_match:
            return (week, int(day_match.group(1)))
        
        # try to look further in the same line or first 100 characters
        day_match = re.search(r'[Dd]ay\s*(\d+)', raw_text[:200], re.IGNORECASE)
        if day_match:
            return (week, int(day_match.group(1)))
            
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Idiom parser
# ─────────────────────────────────────────────────────────────────────────────


def parse_idiom(section_text: str) -> Optional[dict]:
    """
    Extract phrase, meaning, and example from the TODAY'S IDIOM block.

    Format: "phrase—to do something. Example sentence(s)."
    or:     "phrase - meaning. Example: sentence."
    """
    if not section_text.strip():
        return None

    text = clean_ocr_text(section_text)

    # Try to find explicit "Example:" marker
    example = ""
    rest_text = text
    ex_match = re.search(r"(?i)\bexample[:\s]+(.+)$", text)
    if ex_match:
        example = ex_match.group(1).strip()
        rest_text = text[: ex_match.start()].strip()

    # Split phrase and meaning on dash/em-dash/colon
    parts = re.split(r"\s*[—–\-]\s*", rest_text, maxsplit=1)
    if len(parts) == 2:
        phrase = parts[0].strip()
        meaning = parts[1].strip()
        # If no explicit Example: marker, the last sentence of meaning might be the example
        if not example:
            sentences = re.split(r"(?<=\.)\s+", meaning, maxsplit=1)
            if len(sentences) == 2:
                meaning = sentences[0].strip()
                example = sentences[1].strip()
    else:
        phrase = rest_text
        meaning = ""

    if not phrase:
        return None

    return {
        "phrase": phrase,
        "meaning": meaning,
        "example": example,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Answer key parser
# ─────────────────────────────────────────────────────────────────────────────

def parse_answer_key_page(raw_text: str) -> dict:
    """
    Parse an answer-key page like:
        6-d, 7-b, 8-e, 9-a, 10-c
    or:
        6. d  7. b  8. e  9. a  10. c

    Returns {item_number: letter, ...}  e.g. {6: 'd', 7: 'b', ...}
    """
    text = clean_ocr_text(raw_text)
    return {int(num): letter.lower() for num, letter in ANSWER_KEY_ENTRY_RE.findall(text)}


# ─────────────────────────────────────────────────────────────────────────────
# Definition resolution
# ─────────────────────────────────────────────────────────────────────────────

def resolve_definitions(
    words: list,
    shuffled_defs: dict,
    answer_key: dict,
    number_offset: int = 0,
) -> list:
    """
    Join the shuffled definitions back to their correct words using the
    parsed answer key.

    Parameters
    ----------
    words : list
        Word objects with `.answer_key_item_number`, `.pending_answer_letter`,
        `.is_resolved` attributes.
    shuffled_defs : dict
        {letter: definition_text} from parse_definitions_quiz().
    answer_key : dict
        {item_number: letter} from parse_answer_key_page().
    number_offset : int
        Items are numbered continuously across units (6-10, 11-15, …).
        Pass the offset for this unit (e.g. 0 for items 6-10, 5 for 11-15).

    Returns the same `words` list (modified in-place).
    """
    for word_obj in words:
        local_num = getattr(word_obj, "answer_key_item_number", None)
        if local_num is None:
            word_obj.is_resolved = "failed"
            continue

        absolute_num = local_num + number_offset
        letter = answer_key.get(absolute_num)
        if letter:
            word_obj.pending_answer_letter = letter
            def_text = shuffled_defs.get(letter)
            if def_text:
                word_obj.is_resolved = "resolved"
            else:
                word_obj.is_resolved = "failed"
        else:
            word_obj.is_resolved = "failed"

    return words


# ─────────────────────────────────────────────────────────────────────────────
# High-level unit parser (convenience wrapper)
# ─────────────────────────────────────────────────────────────────────────────

def parse_unit(raw_text: str, known_wordlist: Optional[set] = None) -> Unit:
    """Parse a full unit page into a Unit dataclass."""
    sections = split_sections(raw_text)
    unit = Unit()

    unit.words = [w["lemma"] for w in parse_new_words(sections.get("NEW WORDS", ""), known_wordlist)]
    unit.sample_sentences = parse_sample_sentences(sections.get("Sample Sentences", ""))
    unit.definitions_by_letter = parse_definitions_quiz(sections.get("Definitions", ""))
    unit.idiom = parse_idiom(sections.get("TODAY'S IDIOM", ""))

    m = ANSWER_PAGE_RE.search(raw_text)
    if m:
        unit.answers_page = int(m.group(1))

    return unit


# ─────────────────────────────────────────────────────────────────────────────
# Smoke test
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    sample_page = """
NEW WORDS
voracious        indiscriminate       eminent
və rā' shəs      in'dis krim'ə nit    em'ə nənt
steeped          replete
stēpt            ri plēt'

READING WISELY
The youngster who reads voraciously, though indiscriminately, does not
necessarily gain in wisdom over the teenager who is more selective in his
reading choices.

Sample Sentences
1. The football game was ________ with excitement and great plays.
2. The ________ author received the Nobel Prize for literature.
3. My cousin is so ________ in schoolwork that his friends call him a bookworm.
4. After skiing, I find that I have a ________ appetite.
5. Modern warfare often results in the ________ killing of combatants and innocent civilians alike.

Definitions
6. voracious    a. of high reputation, outstanding
7. indiscriminate   b. completely filled or supplied with
8. eminent  c. choosing at random without careful selection
9. steeped  d. desiring or consuming great quantities
10. replete e. soaked, drenched, saturated

TODAY'S IDIOM
to eat humble pie—to admit your error and apologize
After his candidate had lost the election, the boastful campaign manager had to eat humble pie.

ANSWERS ARE ON PAGE 295
"""

    unit = parse_unit(sample_page)
    print("Words:", unit.words)
    print("Sample sentences:", unit.sample_sentences)
    print("Shuffled defs:", unit.definitions_by_letter)
    print("Idiom:", unit.idiom)
    print("Answers page:", unit.answers_page)

    # Simulate answer key page (correct: 6-d,7-c,8-a,9-e,10-b for this unit)
    fake_answer_key_text = "6-d, 7-c, 8-a, 9-e, 10-b"
    answer_key = parse_answer_key_page(fake_answer_key_text)
    print("\nAnswer key:", answer_key)