import sys
sys.path.append('d:/gre_e_preparation/backend')
import logging
logging.disable(logging.CRITICAL)
from database import SessionLocal
from models import Page
import barrons_1100_unit_parser as parser

db = SessionLocal()
pages = db.query(Page).all()
for p in pages:
    raw = p.raw_mineru_output or ""
    print(f"=== Page {p.page_number} ===")
    print(f"Raw length: {len(raw)}")
    sections = parser.split_sections(raw)
    for k, v in sections.items():
        print(f"  SECTION '{k}': {repr(v[:120])}")
    print()
db.close()
