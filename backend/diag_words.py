import sys
sys.path.append('d:/gre_e_preparation/backend')
import logging
logging.disable(logging.CRITICAL)
from database import SessionLocal
from models import Book, Page, Word

db = SessionLocal()

books = db.query(Book).all()
for b in books:
    print(f"Book id={b.id} status={b.status} error={b.error_message}")

pages = db.query(Page).all()
for p in pages:
    s = p.parsed_sections or {}
    new_words = s.get("new_words", "")
    defs = s.get("definitions", "")
    print(f"Page {p.page_number}: new_words={repr(new_words[:80])} defs={repr(defs[:80])}")

print(f"Total words: {db.query(Word).count()}")
db.close()
