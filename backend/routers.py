import os
import shutil
import uuid
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, BackgroundTasks, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from database import get_db
from models import Book, Page, Word, Definition, SampleSentence, Idiom, BookStatus
from schemas import (
    BookUploadResponse, BookStatusResponse, 
    PaginatedWords, WordOut, PaginatedIdioms, IdiomOut
)
from services import process_book, reparse_book_entities

router = APIRouter()

# ----------------- BOOK ENDPOINTS -----------------

@router.post("/books/upload", response_model=BookUploadResponse)
def upload_book(
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    title: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Upload a PDF vocabulary book, save it, and start async Extraction."""
    allowed_extensions = ('.pdf', '.png', '.jpg', '.jpeg', '.zip')
    
    if not files:
        raise HTTPException(status_code=400, detail="No files provided.")

    upload_dir = os.getenv("UPLOAD_DIR", "./uploads")
    os.makedirs(upload_dir, exist_ok=True)

    file_uuid = uuid.uuid4().hex
    
    # If single PDF uploaded via 'files' parameter
    if len(files) == 1 and files[0].filename.lower().endswith('.pdf'):
        safe_filename = f"{file_uuid}_{files[0].filename}"
        file_path = os.path.join(upload_dir, safe_filename)
        try:
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(files[0].file, buffer)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to save file: {str(e)}")
        book_title = title if title else os.path.splitext(files[0].filename)[0]
    else:
        # Multiple images (or a single image) - package as Zip
        safe_filename = f"{file_uuid}_images.zip"
        file_path = os.path.join(upload_dir, safe_filename)
        
        # Write files to zip directly
        import zipfile
        try:
            with zipfile.ZipFile(file_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for f in files:
                    if not f.filename.lower().endswith(('.png', '.jpg', '.jpeg')):
                        continue
                    file_bytes = f.file.read()
                    zf.writestr(f.filename, file_bytes)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to create zip: {str(e)}")
        
        book_title = title if title else "Image Upload"
    
    # Create database entry
    book = Book(
        title=book_title,
        filename=safe_filename,
        status=BookStatus.uploaded
    )
    db.add(book)
    db.commit()
    db.refresh(book)
    
    # Start background extraction job
    background_tasks.add_task(process_book, book.id)
    
    return {
        "book_id": book.id,
        "title": book.title,
        "filename": book.filename,
        "status": book.status
    }


@router.get("/books/{book_id}/status", response_model=BookStatusResponse)
def get_book_status(book_id: int, db: Session = Depends(get_db)):
    """Retrieve the processing status and stats of a book."""
    book = db.query(Book).filter(Book.id == book_id).first()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found.")
        
    # Count stats
    pages_count = db.query(func.count(Page.id)).filter(Page.book_id == book_id).scalar() or 0
    words_count = db.query(func.count(Word.id)).filter(Word.book_id == book_id).scalar() or 0
    idioms_count = db.query(func.count(Idiom.id)).filter(Idiom.book_id == book_id).scalar() or 0
    
    return BookStatusResponse(
        book_id=book.id,
        title=book.title,
        status=book.status,
        error_message=book.error_message,
        uploaded_at=book.uploaded_at,
        processed_at=book.processed_at,
        pages_count=pages_count,
        words_count=words_count,
        idioms_count=idioms_count
    )


@router.get("/books", response_model=List[BookStatusResponse])
def list_books(db: Session = Depends(get_db)):
    """Retrieve list of all books in the database."""
    books = db.query(Book).order_by(Book.id).all()
    response = []
    for book in books:
        pages_count = db.query(func.count(Page.id)).filter(Page.book_id == book.id).scalar() or 0
        words_count = db.query(func.count(Word.id)).filter(Word.book_id == book.id).scalar() or 0
        idioms_count = db.query(func.count(Idiom.id)).filter(Idiom.book_id == book.id).scalar() or 0
        response.append(
            BookStatusResponse(
                book_id=book.id,
                title=book.title,
                status=book.status,
                error_message=book.error_message,
                uploaded_at=book.uploaded_at,
                processed_at=book.processed_at,
                pages_count=pages_count,
                words_count=words_count,
                idioms_count=idioms_count
            )
        )
    return response


@router.post("/books/{book_id}/retry")
def retry_book_processing(
    book_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """Reset book status and trigger background processing task again."""
    book = db.query(Book).filter(Book.id == book_id).first()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found.")
    
    # 1. Reset book status
    book.status = BookStatus.uploaded
    book.error_message = None
    
    # 2. Delete existing processing artifacts (pages, words, idioms) if any,
    # so we have a clean slate for the retry.
    db.query(Idiom).filter(Idiom.book_id == book_id).delete()
    
    # Fetch words to delete definitions and sample sentences first
    words_to_delete = db.query(Word).filter(Word.book_id == book_id).all()
    word_ids = [w.id for w in words_to_delete]
    if word_ids:
        db.query(Definition).filter(Definition.word_id.in_(word_ids)).delete(synchronize_session=False)
        db.query(SampleSentence).filter(SampleSentence.word_id.in_(word_ids)).delete(synchronize_session=False)
        db.query(Word).filter(Word.id.in_(word_ids)).delete(synchronize_session=False)
        
    db.query(Page).filter(Page.book_id == book_id).delete()
    
    db.commit()
    db.refresh(book)
    
    # Start background extraction job
    background_tasks.add_task(process_book, book.id)
    
    return {
        "book_id": book.id,
        "title": book.title,
        "status": book.status,
        "message": "Processing restarted in background."
    }

@router.post("/books/{book_id}/reparse")
def reparse_book_processing(
    book_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """Re-parse the words, definitions, and idioms from stored pages without re-running MinerU."""
    book = db.query(Book).filter(Book.id == book_id).first()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found.")
    
    background_tasks.add_task(reparse_book_entities, book.id)
    
    return {
        "book_id": book.id,
        "title": book.title,
        "status": "processing",
        "message": "Reparsing job started in background."
    }


# ----------------- WORD ENDPOINTS -----------------

@router.get("/words", response_model=PaginatedWords)
def list_words(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    book_id: Optional[int] = Query(None),
    search: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """Retrieve words with definitions and pagination, optional filtering & search."""
    query = db.query(Word)
    
    if book_id is not None:
        query = query.filter(Word.book_id == book_id)
        
    if search:
        query = query.filter(Word.lemma.ilike(f"%{search}%"))
        
    total_count = query.count()
    
    # Pagination
    offset = (page - 1) * page_size
    words = query.order_by(Word.lemma).offset(offset).limit(page_size).all()
    
    return PaginatedWords(
        words=words,
        total_count=total_count,
        page=page,
        page_size=page_size
    )


@router.get("/words/{word_id}", response_model=WordOut)
def get_word(word_id: int, db: Session = Depends(get_db)):
    """Retrieve a single word with definitions and sample sentences."""
    word = db.query(Word).filter(Word.id == word_id).first()
    if not word:
        raise HTTPException(status_code=404, detail="Word not found.")
    return word


# ----------------- IDIOM ENDPOINTS -----------------

@router.get("/idioms", response_model=PaginatedIdioms)
def list_idioms(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    book_id: Optional[int] = Query(None),
    db: Session = Depends(get_db)
):
    """Retrieve a list of idioms with pagination, filterable by book."""
    query = db.query(Idiom)
    
    if book_id is not None:
        query = query.filter(Idiom.book_id == book_id)
        
    total_count = query.count()
    
    offset = (page - 1) * page_size
    idioms = query.order_by(Idiom.phrase).offset(offset).limit(page_size).all()
    
    return PaginatedIdioms(
        idioms=idioms,
        total_count=total_count,
        page=page,
        page_size=page_size
    )
