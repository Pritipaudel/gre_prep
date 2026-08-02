import sys
sys.path.append('d:/gre_e_preparation/backend')
import logging
logging.disable(logging.CRITICAL)
import requests
from database import SessionLocal
from models import Book, Page, Word, Definition
import barrons_1100_unit_parser as parser


def fetch_definition(word: str) -> tuple:
    """Fetch real pronunciation, POS, and definition. Datamuse for def (fast), Free Dict for phonetics."""
    pronunciation = None
    part_of_speech = None
    definition = None

    # Free Dictionary for pronunciation
    try:
        r = requests.get(f"https://api.dictionaryapi.dev/api/v2/entries/en/{word}", timeout=3)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and data:
                for p in data[0].get("phonetics", []):
                    if p.get("text"):
                        pronunciation = p["text"]
                        break
                if not pronunciation:
                    pronunciation = data[0].get("phonetic")
    except Exception:
        pass

    # Datamuse for definition + POS (very reliable, no rate limit)
    try:
        r = requests.get(f"https://api.datamuse.com/words?sp={word}&md=d", timeout=3)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and data:
                defs = data[0].get("defs", [])
                if defs:
                    parts = defs[0].split("\t")
                    if len(parts) > 1:
                        definition = parts[1].strip()
                        if not part_of_speech:
                            part_of_speech = parts[0].strip()
                    else:
                        definition = defs[0]
    except Exception:
        pass

    return pronunciation, part_of_speech, definition


def load_all_words(book_id: int):
    db = SessionLocal()
    pages = db.query(Page).filter(Page.book_id == book_id).order_by(Page.page_number).all()

    pages_processed = 0
    pages_skipped_no_text = 0
    pages_skipped_no_defs = 0
    pages_skipped_unparseable = 0
    words_created = 0
    words_updated = 0
    definitions_created = 0

    for page in pages:
        if not page.raw_mineru_output:
            pages_skipped_no_text += 1
            print(f"Page {page.page_number} skipped: no raw text")
            continue

        # Re-run split_sections fresh — do NOT trust stale parsed_sections
        parsed_sections = parser.split_sections(page.raw_mineru_output)
        page.parsed_sections = parsed_sections  # overwrite with corrected version

        defs_text = parsed_sections.get("Definitions", "").strip()
        if not defs_text:
            pages_skipped_no_defs += 1
            continue

        entries = parser.parse_vocab_definitions_direct(defs_text)
        if not entries:
            pages_skipped_unparseable += 1
            print(f"Page {page.page_number} skipped: Definitions section could not be parsed")
            continue

        pages_processed += 1

        for entry in entries:
            lemma = entry["word"]

            # Fetch the REAL definition from dictionary — NOT the shuffled quiz shown_definition
            pron, api_pos, api_def = fetch_definition(lemma)
            pos = entry["part_of_speech"] or api_pos

            # Idempotent upsert keyed on (book_id, page_id, lemma)
            word_db = db.query(Word).filter(
                Word.book_id == book_id,
                Word.page_id == page.id,
                Word.lemma == lemma
            ).first()

            if word_db:
                word_db.part_of_speech = pos
                word_db.pronunciation = pron
                word_db.answer_key_item_number = entry["item_number"]
                word_db.pending_answer_letter = entry["shown_letter"]
                
                if api_def:
                    word_db.is_resolved = "resolved"
                    try:
                        word_db.definition_quiz = {"api": api_def}
                    except Exception:
                        pass # if column was dropped
                else:
                    word_db.is_resolved = "failed"
                    
                words_updated += 1
            else:
                word_db = Word(
                    book_id=book_id,
                    page_id=page.id,
                    lemma=lemma,
                    pronunciation=pron,
                    part_of_speech=pos,
                    answer_key_item_number=entry["item_number"],
                    pending_answer_letter=entry["shown_letter"],
                    is_resolved="resolved" if api_def else "failed"
                )
                if api_def:
                    try:
                        word_db.definition_quiz = {"api": api_def}
                    except Exception:
                        pass
                db.add(word_db)
                db.flush()
                words_created += 1

            if api_def:
                # Only insert if no identical (word_id, text) pair already exists
                existing_def = db.query(Definition).filter(
                    Definition.word_id == word_db.id,
                    Definition.text == api_def
                ).first()
                if not existing_def:
                    db.add(Definition(
                        word_id=word_db.id,
                        text=api_def,
                        source="dictionary_api",
                        confidence=0.8
                    ))
                    definitions_created += 1

        db.commit()

    db.commit()
    db.close()

    print("\n=== Summary ===")
    print(f"Pages processed:              {pages_processed}")
    print(f"Pages skipped (no raw text):  {pages_skipped_no_text}")
    print(f"Pages skipped (no Defs):      {pages_skipped_no_defs}")
    print(f"Pages skipped (unparseable):  {pages_skipped_unparseable}")
    print(f"Words created:                {words_created}")
    print(f"Words updated:                {words_updated}")
    print(f"Definitions created:          {definitions_created}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python load_all_words.py <book_id>")
        sys.exit(1)
    load_all_words(int(sys.argv[1]))
