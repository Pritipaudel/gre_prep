import sys
sys.path.append('d:/gre_e_preparation/backend')
import logging
logging.disable(logging.CRITICAL)
from database import SessionLocal
from models import Page

db = SessionLocal()
p = db.query(Page).first()
raw = p.raw_mineru_output

lines = raw.split('\n')
with open('diag_alllines_utf8.txt', 'w', encoding='utf-8') as f:
    for i, line in enumerate(lines):
        f.write(f"L{i:02d}: {repr(line)}\n")
print("Written to diag_alllines_utf8.txt")
