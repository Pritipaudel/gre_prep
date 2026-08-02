import sys
sys.path.append('d:/gre_e_preparation/backend')
import logging
logging.disable(logging.CRITICAL)
from database import SessionLocal
from models import Page, Word, Definition

db = SessionLocal()

# Check parsed sections
pages = db.query(Page).all()
for p in pages:
    s = p.parsed_sections or {}
    print(f"--- Page {p.page_number} (book_id={p.book_id}, page_id={p.id}) ---")
    print(f"  new_words present: {bool(s.get('new_words'))}")
    print(f"  definitions present: {bool(s.get('definitions'))}")
    print(f"  definitions_quiz: {s.get('definitions_quiz', {})}")
    print()

# Check words and their definitions
words = db.query(Word).all()
for w in words:
    defs = db.query(Definition).filter(Definition.word_id == w.id).all()
    def_texts = [(d.text, d.source, d.confidence) for d in defs]
    print(f"Word: {w.lemma}")
    print(f"  page_id={w.page_id}, resolved={w.is_resolved}")
    print(f"  answer_key_item_number={w.answer_key_item_number}")
    print(f"  pending_answer_letter={w.pending_answer_letter}")
    print(f"  definition_quiz={w.definition_quiz}")
    print(f"  definitions_in_table={def_texts}")
    print()

db.close()
