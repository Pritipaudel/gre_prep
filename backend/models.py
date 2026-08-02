from sqlalchemy import Column, Integer, String, Text, Float, DateTime, ForeignKey, UniqueConstraint, Enum, Index
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import JSONB
from datetime import datetime
import enum
from database import Base

class BookStatus(str, enum.Enum):
    uploaded = "uploaded"
    processing = "processing"
    done = "done"
    failed = "failed"

class DefinitionSource(str, enum.Enum):
    book = "book"
    book_unverified = "book_unverified"
    dictionary_api = "dictionary_api"
    llm_generated = "llm_generated"

class Book(Base):
    __tablename__ = "books"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    filename = Column(String, nullable=False)
    status = Column(Enum(BookStatus), default=BookStatus.uploaded, nullable=False)
    error_message = Column(Text, nullable=True)
    uploaded_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    processed_at = Column(DateTime, nullable=True)
    progress_message = Column(String, nullable=True)

    # Relationships
    pages = relationship("Page", back_populates="book", cascade="all, delete-orphan")
    words = relationship("Word", back_populates="book", cascade="all, delete-orphan")
    idioms = relationship("Idiom", back_populates="book", cascade="all, delete-orphan")


class Page(Base):
    __tablename__ = "pages"

    id = Column(Integer, primary_key=True, index=True)
    book_id = Column(Integer, ForeignKey("books.id", ondelete="CASCADE"), nullable=False)
    page_number = Column(Integer, nullable=False)
    raw_mineru_output = Column(Text, nullable=True)
    parsed_sections = Column(JSONB, nullable=True)
    processing_confidence = Column(Float, nullable=True)

    # Unique constraint on (book_id, page_number)
    __table_args__ = (
        UniqueConstraint("book_id", "page_number", name="uq_book_page"),
    )

    # Relationships
    book = relationship("Book", back_populates="pages")
    words = relationship("Word", back_populates="page")
    idioms = relationship("Idiom", back_populates="page")


class Word(Base):
    __tablename__ = "words"

    id = Column(Integer, primary_key=True, index=True)
    book_id = Column(Integer, ForeignKey("books.id", ondelete="CASCADE"), nullable=False)
    page_id = Column(Integer, ForeignKey("pages.id", ondelete="SET NULL"), nullable=True)
    lemma = Column(String, nullable=False, index=True)
    pronunciation = Column(String, nullable=True)
    part_of_speech = Column(String, nullable=True)
    definition_quiz = Column(JSONB, nullable=True)

    # Answer-key resolution staging fields
    pending_answer_letter = Column(String, nullable=True)
    answer_key_item_number = Column(Integer, nullable=True, index=True)
    is_resolved = Column(String, default="pending", nullable=False)  # pending, resolved, failed

    # Relationships
    book = relationship("Book", back_populates="words")
    page = relationship("Page", back_populates="words")
    definitions = relationship("Definition", back_populates="word", cascade="all, delete-orphan")
    sample_sentences = relationship("SampleSentence", back_populates="word", cascade="all, delete-orphan")


class Definition(Base):
    __tablename__ = "definitions"

    id = Column(Integer, primary_key=True, index=True)
    word_id = Column(Integer, ForeignKey("words.id", ondelete="CASCADE"), nullable=False)
    text = Column(Text, nullable=False)
    source = Column(Enum(DefinitionSource), default=DefinitionSource.book, nullable=False)
    confidence = Column(Float, nullable=False, default=1.0)

    # Relationships
    word = relationship("Word", back_populates="definitions")


class SampleSentence(Base):
    __tablename__ = "sample_sentences"

    id = Column(Integer, primary_key=True, index=True)
    word_id = Column(Integer, ForeignKey("words.id", ondelete="CASCADE"), nullable=False)
    sentence_text = Column(Text, nullable=False)
    source = Column(String, nullable=True)  # book, dictionary_api, etc.

    # Relationships
    word = relationship("Word", back_populates="sample_sentences")


class Idiom(Base):
    __tablename__ = "idioms"

    id = Column(Integer, primary_key=True, index=True)
    book_id = Column(Integer, ForeignKey("books.id", ondelete="CASCADE"), nullable=False)
    page_id = Column(Integer, ForeignKey("pages.id", ondelete="SET NULL"), nullable=True)
    phrase = Column(String, nullable=False, index=True)
    meaning = Column(Text, nullable=False)
    example = Column(Text, nullable=True)

    # Relationships
    book = relationship("Book", back_populates="idioms")
    page = relationship("Page", back_populates="idioms")
