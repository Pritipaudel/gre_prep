import sys
sys.path.append('d:/gre_e_preparation/backend')
import logging
logging.disable(logging.CRITICAL)
from database import SessionLocal
from models import Page

db = SessionLocal()
pages = db.query(Page).all()
for p in pages:
    print(f"=== Page {p.page_number} raw first 1500 chars ===")
    print(repr((p.raw_mineru_output or "")[:1500]))
    print()
db.close()
