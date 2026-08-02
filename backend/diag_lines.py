import sys
sys.path.append('d:/gre_e_preparation/backend')
import logging
logging.disable(logging.CRITICAL)
from database import SessionLocal
from models import Page
import barrons_1100_unit_parser as parser

db = SessionLocal()
p = db.query(Page).first()
raw = p.raw_mineru_output

# print line by line from raw
for i, line in enumerate(raw.split('\n')):
    print(f"L{i:02d}: {repr(line)}")
