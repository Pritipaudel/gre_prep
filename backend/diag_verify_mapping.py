import sys
sys.path.append('d:/gre_e_preparation/backend')
import logging
logging.disable(logging.CRITICAL)
from database import SessionLocal
from models import Book, Page, Word, Definition
from services import clean_ocr_lemma, fetch_word_metadata
import barrons_1100_unit_parser as parser

db = SessionLocal()
book_id = 35 # Try for book 35
pages = db.query(Page).filter(Page.book_id == book_id).all()

for p in pages:
    page_text = p.raw_mineru_output
    if not page_text: continue
    
    sections = parser.split_sections(page_text)
    print(f"Page {p.page_number} sections: {list(sections.keys())}")
    
    full_quiz = parser.parse_definitions_quiz_full(sections.get("Definitions", ""))
    print(f"  full_quiz num_to_word: {full_quiz.get('number_to_word')}")
    
    parsed_words = parser.parse_new_words(sections.get("NEW WORDS", ""))
    if not parsed_words and sections.get("Definitions"):
        if full_quiz.get("number_to_word"):
            parsed_words = [{"lemma": w, "part_of_speech": None} for n, w in sorted(full_quiz["number_to_word"].items())][:5]
            
    print(f"  parsed_words: {parsed_words}")
    
    for idx, word_data in enumerate(parsed_words):
        raw_lemma = word_data if isinstance(word_data, str) else word_data.get("lemma")
        lemma = clean_ocr_lemma(raw_lemma)
        print(f"  - lemma: {lemma} (raw: {raw_lemma})")
        api_pron, api_pos, api_def = fetch_word_metadata(lemma)
        print(f"    api_def: {bool(api_def)}")
        
db.close()
