from pydantic import BaseModel, ConfigDict, Field
from datetime import datetime
from typing import Optional, List, Dict
from models import BookStatus, DefinitionSource

# Definition schemas
class DefinitionBase(BaseModel):
    text: str
    source: DefinitionSource
    confidence: float

class DefinitionCreate(DefinitionBase):
    pass

class DefinitionOut(DefinitionBase):
    id: int
    word_id: int

    model_config = ConfigDict(from_attributes=True)


# Sample sentence schemas
class SampleSentenceBase(BaseModel):
    sentence_text: str
    source: Optional[str] = None

class SampleSentenceOut(SampleSentenceBase):
    id: int
    word_id: int

    model_config = ConfigDict(from_attributes=True)


# Word schemas
class WordBase(BaseModel):
    lemma: str
    pronunciation: Optional[str] = None
    part_of_speech: Optional[str] = None

class WordCreate(WordBase):
    book_id: int
    page_id: Optional[int] = None
    definition_quiz: Optional[Dict[str, str]] = None
    pending_answer_letter: Optional[str] = None
    answer_key_item_number: Optional[int] = None
    is_resolved: str = "pending"

class WordOut(WordBase):
    id: int
    book_id: int
    page_id: Optional[int]
    is_resolved: str
    definition_quiz: Optional[Dict[str, str]] = None
    definitions: List[DefinitionOut] = []
    sample_sentences: List[SampleSentenceOut] = []

    model_config = ConfigDict(from_attributes=True)


# Idiom schemas
class IdiomBase(BaseModel):
    phrase: str
    meaning: str
    example: Optional[str] = None

class IdiomCreate(IdiomBase):
    book_id: int
    page_id: Optional[int] = None

class IdiomOut(IdiomBase):
    id: int
    book_id: int
    page_id: Optional[int]

    model_config = ConfigDict(from_attributes=True)


# Book schemas
class BookBase(BaseModel):
    title: str
    filename: str

class BookCreate(BookBase):
    pass

class BookUploadResponse(BaseModel):
    book_id: int
    title: str
    filename: str
    status: BookStatus

    model_config = ConfigDict(from_attributes=True)

class BookStatusResponse(BaseModel):
    book_id: int
    title: str
    status: BookStatus
    error_message: Optional[str] = None
    progress_message: Optional[str] = None
    uploaded_at: datetime
    processed_at: Optional[datetime] = None
    pages_count: int
    words_count: int
    idioms_count: int

    model_config = ConfigDict(from_attributes=True)


# Paginated list wrappers
class PaginatedWords(BaseModel):
    words: List[WordOut]
    total_count: int
    page: int
    page_size: int

class PaginatedIdioms(BaseModel):
    idioms: List[IdiomOut]
    total_count: int
    page: int
    page_size: int
