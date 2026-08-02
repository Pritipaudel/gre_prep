import sys; sys.path.append('d:/gre_e_preparation/backend')
import logging; logging.disable(logging.CRITICAL)
from database import engine
import sqlalchemy as sa
with engine.connect() as conn:
    conn.execute(sa.text("ALTER TYPE definitionsource ADD VALUE IF NOT EXISTS 'book_unverified'"))
    conn.commit()
print('Enum updated.')
