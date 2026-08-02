import os
import requests
import json
import logging
import re
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session
from database import SessionLocal
from models import Book, Page, Word, Definition, SampleSentence, Idiom, BookStatus, DefinitionSource
from mineru_client import run_mineru_extraction, run_mineru_image_extraction, run_mineru_images_extraction
import barrons_1100_unit_parser as parser
import difflib
import time

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

def fetch_word_metadata(word: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Helper to fetch phonetic pronunciation, part of speech, and definition."""
    pronunciation = None
    part_of_speech = None
    definition = None
    
    # 1. Phonetics & POS via Free Dictionary (may rate limit)
    try:
        url = f"https://api.dictionaryapi.dev/api/v2/entries/en/{word}"
        response = requests.get(url, timeout=3)
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list) and len(data) > 0:
                phonetics = data[0].get("phonetics", [])
                for p in phonetics:
                    text = p.get("text")
                    if text:
                        pronunciation = text
                        break
                if not pronunciation:
                    pronunciation = data[0].get("phonetic")
                    
                meanings = data[0].get("meanings", [])
                if meanings:
                    part_of_speech = meanings[0].get("partOfSpeech")
    except Exception:
        pass
        
    # 2. Resilient Definition via Datamuse
    try:
        url = f"https://api.datamuse.com/words?sp={word}&md=d"
        response = requests.get(url, timeout=3)
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list) and len(data) > 0:
                defs = data[0].get("defs", [])
                if defs:
                    # Datamuse format: "adj\tMeaning text."
                    raw_def = defs[0]
                    parts = raw_def.split("\t")
                    if len(parts) > 1:
                        definition = parts[1].strip()
                        if not part_of_speech:
                            part_of_speech = parts[0].strip()
                    else:
                        definition = raw_def
    except Exception as e:
        logger.warning(f"Error fetching definition for '{word}': {e}")
        
    return pronunciation, part_of_speech, definition

def clean_ocr_lemma(raw_lemma: str) -> str:
    """Uses difflib to fix OCR typos using dictionary validation."""
    clean = raw_lemma.strip().lower()
    
    # Try fetching metadata to see if it's already valid
    url = f"https://api.dictionaryapi.dev/api/v2/entries/en/{clean}"
    try:
        if requests.get(url, timeout=3).status_code == 200:
            return clean
    except:
        pass
        
    # Standard fix-list for unit 1-6 etc that frequently OCR fail
    common_words = [
        "voracious", "indiscriminate", "eminent", "steeped", "replete",
        "badger", "implore", "drudgery", "interminable", "perceive"
    ]
    matches = difflib.get_close_matches(clean, common_words, n=1, cutoff=0.6)
    if matches:
        return matches[0]
        
    return clean



def process_book(book_id: int):
    """Background task to extract and process words from the uploaded PDF book."""
    db: Session = SessionLocal()
    book = db.query(Book).filter(Book.id == book_id).first()
    if not book:
        logger.error(f"Book with id {book_id} not found.")
        db.close()
        return

    # Update status to processing
    book.status = BookStatus.processing
    db.commit()

    try:
        # Get host PDF path
        upload_dir = os.getenv("UPLOAD_DIR", "./uploads")
        pdf_path = os.path.join(upload_dir, book.filename)
        is_image = book.filename.lower().endswith(('.png', '.jpg', '.jpeg'))
        is_zip = book.filename.lower().endswith('.zip')
        
        logger.info(f"Starting MinerU extraction for book {book.title} (ID: {book_id}) at {pdf_path}")
        
        def _progress_callback(chunk_num: int, total_chunks: int, pages_done: int):
            """Write live chunk progress to book.progress_message so frontend can poll it."""
            try:
                book.progress_message = (
                    f"Processing chunk {chunk_num}/{total_chunks} "
                    f"({pages_done} pages extracted so far)…"
                )
                db.commit()
            except Exception as cb_err:
                logger.warning(f"Could not update progress_message: {cb_err}")

        # Branch processing by file type
        if is_image:
            logger.info("Sourced file is an image. Running direct image extraction.")
            # single page directly returned
            pages_markdown = run_mineru_image_extraction(pdf_path)
        elif is_zip:
            logger.info("Sourced file is a zip of images. Running batch image extraction.")
            import zipfile
            import tempfile
            
            with tempfile.TemporaryDirectory() as temp_dir:
                with zipfile.ZipFile(pdf_path, 'r') as zf:
                    zf.extractall(temp_dir)

                image_files = [
                    os.path.join(temp_dir, f)
                    for f in sorted(os.listdir(temp_dir))
                    if f.lower().endswith(('.png', '.jpg', '.jpeg'))
                ]
                if not image_files:
                    raise RuntimeError("No image files were found inside the uploaded zip archive.")

                pages_markdown = run_mineru_images_extraction(image_files)
                _progress_callback(len(image_files), len(image_files), len(pages_markdown))

        else:
            # Run extraction. Prefer using MINERU_KAGGLE_PATH env to avoid uploading large files via the ngrok tunnel.
            kaggle_path = os.getenv("MINERU_KAGGLE_PATH")
            if kaggle_path:
                logger.info("Using MINERU_KAGGLE_PATH to instruct MinerU to pull the file from Kaggle disk.")
                pages_markdown = run_mineru_extraction(
                    pdf_path, kaggle_path=kaggle_path, progress_callback=_progress_callback
                )
            else:
                pages_markdown = run_mineru_extraction(
                    pdf_path, progress_callback=_progress_callback
                )

        pages_markdown = [page for page in pages_markdown if isinstance(page, str) and page.strip()]
        if not pages_markdown:
            raise RuntimeError(
                "MinerU completed without returning any usable page content. "
                "Check the Kaggle service response contract and logs."
            )

        logger.info(f"MinerU extraction completed. Extracted {len(pages_markdown)} pages.")

        daily_page_count = 0
        book_answers = {}  # running answer key map: {absolute_item_number: letter}
        
        # Step 1: Parse and store pages & raw entities in database
        pages_created = []
        for i, page_text in enumerate(pages_markdown):
            page_number = i + 1
            
            # Split sections
            sections = parser.split_sections(page_text)
            
            # Parse definitions quiz if present
            definitions_quiz = parser.parse_definitions_quiz(sections.get('Definitions', '')) if sections.get('Definitions') else {}
            
            # Store in Page table (JSONB raw layer)
            parsed_json = {
                "new_words": sections.get("NEW WORDS", ""),
                "sample_sentences": sections.get("Sample Sentences", ""),
                "definitions": sections.get("Definitions", ""),
                "definitions_quiz": definitions_quiz,
                "idiom": sections.get("TODAY'S IDIOM", "")
            }
            
            page_db = Page(
                book_id=book_id,
                page_number=page_number,
                raw_mineru_output=page_text,
                parsed_sections=parsed_json,
                processing_confidence=1.0 if (sections.get("NEW WORDS") or sections.get("Definitions")) else 0.5
            )
            db.add(page_db)
            db.flush()  # gets page_db.id
            pages_created.append((page_db.id, page_text, sections, definitions_quiz))
            
            # Heuristic to detect and parse answer key pages
            # If the page contains a significant number of matches like "6-d", parse it
            answers = parser.parse_answer_key_page(page_text)
            if len(answers) >= 3:
                logger.info(f"Page {page_number} detected as answer key page with {len(answers)} keys.")
                book_answers.update(answers)

        logger.info(f"Total parsed answer keys: {len(book_answers)}")

        # Step 2: Extract normalized entities (words, sentences, idioms)
        for page_id, page_text, sections, definitions_quiz in pages_created:
            # Check if this page contains new vocab words or definitions
            if sections.get("NEW WORDS") or sections.get("Definitions"):
                daily_page_count += 1
                number_offset = 10 * (daily_page_count - 1)
                
                # Parse headwords strictly from Definitions section if present
                defs_text = sections.get("Definitions") or sections.get("definitions", "")
                parsed_words = []
                
                if defs_text:
                    entries = parser.parse_vocab_definitions_direct(defs_text)
                    parsed_words = [{"lemma": e["word"], "part_of_speech": e["part_of_speech"], "item_number": e["item_number"], "shown_letter": e["shown_letter"]} for e in entries]
                
                # Fallback to NEW WORDS or preamble if Definitions section is missing or unparseable
                if not parsed_words:
                    parsed_words_old = parser.parse_new_words(sections.get("NEW WORDS", ""))
                    if not parsed_words_old:
                        preamble_text = sections.get("preamble", "")
                        if preamble_text:
                            parsed_words_old = parser.parse_new_words(preamble_text)
                            
                    parsed_words = [{"lemma": w.get("lemma") if isinstance(w, dict) else w, "part_of_speech": w.get("part_of_speech") if isinstance(w, dict) else None, "item_number": 6 + i, "shown_letter": None} for i, w in enumerate(parsed_words_old)]

                # Create word records
                words_list = []
                for idx, word_data in enumerate(parsed_words):
                    raw_lemma = word_data.get("lemma")
                    lemma = clean_ocr_lemma(raw_lemma)
                    part_of_speech = word_data.get("part_of_speech")
                    
                    # Fetch pronunciation, part of speech, definition via API
                    api_pron, api_pos, api_def = fetch_word_metadata(lemma)
                    pronunciation = word_data.get("pronunciation_raw", "") or api_pron
                    part_of_speech = part_of_speech or api_pos
                    
                    local_item_number = word_data["item_number"]
                    shown_letter = word_data["shown_letter"]

                    word_db = Word(
                        book_id=book_id,
                        page_id=page_id,
                        lemma=lemma,
                        pronunciation=pronunciation,
                        part_of_speech=part_of_speech,
                        definition_quiz={"api": api_def} if api_def else None, # Store API def for UI processing
                        pending_answer_letter=shown_letter,
                        answer_key_item_number=local_item_number, # store local item number, offset will be applied in resolution
                        is_resolved="resolved" if api_def else "pending"
                    )
                    db.add(word_db)
                    db.flush()
                    
                    if api_def:
                        definition_db = Definition(
                            word_id=word_db.id,
                            text=api_def,
                            source=DefinitionSource.dictionary_api,
                            confidence=0.8
                        )
                        db.add(definition_db)
                        
                    words_list.append((word_db, idx))

                # Parse sample sentences
                if hasattr(parser, "parse_sample_sentences"):
                    parsed_sentences = parser.parse_sample_sentences(sections.get("Sample Sentences", ""))
                else:
                    parsed_sentences = parser.parse_unit(page_text).sample_sentences
                for s_idx, sentence in enumerate(parsed_sentences):
                    # Match sentence to word
                    matched_word_id = None
                    # First try to find word matching lemma inside the sentence
                    for word_db, idx in words_list:
                        lemma_clean = word_db.lemma.lower()
                        # Match word boundary or prefix (e.g. steeped matches steeped)
                        if re.search(r'\b' + re.escape(lemma_clean) + r'[a-z]*\b', sentence.lower()):
                            matched_word_id = word_db.id
                            break
                    
                    # Fallback to order matches if not found
                    if not matched_word_id and len(words_list) > 0:
                        # Fallback by estimating order based on list index
                        word_idx = s_idx % len(words_list)
                        matched_word_id = words_list[word_idx][0].id
                        
                    if matched_word_id:
                        sentence_db = SampleSentence(
                            word_id=matched_word_id,
                            sentence_text=sentence,
                            source="book"
                        )
                        db.add(sentence_db)

            # Check if this page contains an idiom
            if sections.get("TODAY'S IDIOM"):
                parsed_idm = parser.parse_idiom(sections.get("TODAY'S IDIOM", ""))
                if parsed_idm and parsed_idm.get("phrase"):
                    idiom_db = Idiom(
                        book_id=book_id,
                        page_id=page_id,
                        phrase=parsed_idm["phrase"],
                        meaning=parsed_idm["meaning"],
                        example=parsed_idm["example"]
                    )
                    db.add(idiom_db)
                    
        db.commit()

        # Step 3: Resolution Pass using book_answers (combining offset based on page index)
        # Fetch all pending words for this book
        pending_words = db.query(Word).filter(Word.book_id == book_id, Word.is_resolved == "pending").all()
        
        # We need to map page_id to their daily page index/offset
        # Get all vocabulary page ids in order
        vocab_pages = db.query(Page.id).filter(Page.book_id == book_id).order_by(Page.page_number).all()
        vocab_page_ids = [p_id for (p_id,) in vocab_pages]
        
        # Build page-to-offset mapping
        page_offsets = {}
        daily_count = 0
        for p_id in vocab_page_ids:
            # Let's inspect the page parsed sections to see if it has new words
            page_obj = db.query(Page).filter(Page.id == p_id).first()
            if page_obj and page_obj.parsed_sections and page_obj.parsed_sections.get("new_words"):
                daily_count += 1
                page_offsets[p_id] = 10 * (daily_count - 1)

        logger.info(f"Resolution offsets mapped for {len(page_offsets)} vocabulary pages.")

        for word in pending_words:
            offset = page_offsets.get(word.page_id, 0)
            local_num = word.answer_key_item_number or 6
            absolute_num = local_num + offset
            
            # Update absolute item number inside DB for tracking
            word.answer_key_item_number = absolute_num
            
            letter = book_answers.get(absolute_num)
            if letter:
                word.pending_answer_letter = letter
                
                # Fetch definitions quiz from the word itself (we stored it in Step 2)
                definitions_quiz = word.definition_quiz or {}
                def_text = definitions_quiz.get(letter)
                if def_text:
                    word.definition_quiz = {letter: def_text}
                    definition_db = Definition(
                        word_id=word.id,
                        text=def_text,
                        source=DefinitionSource.book,
                        confidence=0.9
                    )
                    db.add(definition_db)
                    word.is_resolved = "resolved"
                else:
                    word.is_resolved = "failed"
            else:
                # No answer key exists for this word. Use API fallback.
                _, _, api_def = fetch_word_metadata(word.lemma)
                if api_def:
                    definition_db = Definition(
                        word_id=word.id,
                        text=api_def,
                        source=DefinitionSource.dictionary_api,
                        confidence=0.8
                    )
                    db.add(definition_db)
                    word.is_resolved = "resolved"
                    word.definition_quiz = None # Reset since we couldn't match a letter
                else:
                    word.is_resolved = "failed"
                    word.definition_quiz = None
                
        db.commit()

        # Update book status to done
        book.status = BookStatus.done
        book.processed_at = datetime.utcnow()
        db.commit()
        logger.info(f"Processing book ID {book_id} succeeded.")
        
    except Exception as e:
        logger.exception(f"Error processing book ID {book_id}: {e}")
        db.rollback()
        # Set book status to failed
        book.status = BookStatus.failed
        book.error_message = str(e)
        db.commit()
        
        db.close()

def reparse_book_entities(book_id: int):
    """Re-parse the words, definitions, and idioms from stored pages without re-running MinerU."""
    db: Session = SessionLocal()
    book = db.query(Book).filter(Book.id == book_id).first()
    if not book:
        logger.error(f"Book with id {book_id} not found.")
        db.close()
        return

    book.status = BookStatus.processing
    book.progress_message = "Re-parsing from stored pages..."
    db.commit()

    try:
        # Clear old parsed entities
        db.query(Idiom).filter(Idiom.book_id == book_id).delete()
        words_to_delete = db.query(Word).filter(Word.book_id == book_id).all()
        word_ids = [w.id for w in words_to_delete]
        if word_ids:
            db.query(Definition).filter(Definition.word_id.in_(word_ids)).delete(synchronize_session=False)
            db.query(SampleSentence).filter(SampleSentence.word_id.in_(word_ids)).delete(synchronize_session=False)
            db.query(Word).filter(Word.id.in_(word_ids)).delete(synchronize_session=False)
        db.commit()

        # Fetch stored pages
        pages = db.query(Page).filter(Page.book_id == book_id).order_by(Page.page_number).all()
        if not pages:
            raise RuntimeError("No parsed pages available to re-parse. Please upload/retry properly.")

        pages_markdown = [p.raw_mineru_output for p in pages if p.raw_mineru_output]
        
        # We need to recreate the `pages_created` list structure to feed into Step 2 reuse
        # In a real refactor, Step 2 and Step 3 would be broken out into helper functions.
        # But we can just loop over pages to recreate entities.
        daily_page_count = 0
        book_answers = {}

        for p in pages:
            page_text = p.raw_mineru_output
            page_number = p.page_number
            sections = p.parsed_sections or parser.split_sections(page_text)
            definitions_quiz = sections.get("definitions_quiz", {}) 

            # Update book_answers if found
            answers = parser.parse_answer_key_page(page_text)
            if len(answers) >= 3:
                book_answers.update(answers)

            # Step 2 logic ...
            if sections.get("new_words") or sections.get("definitions") or sections.get("NEW WORDS") or sections.get("Definitions"):
                daily_page_count += 1
                number_offset = 10 * (daily_page_count - 1)
                defs_text = sections.get("Definitions") or sections.get("definitions", "")
                parsed_words = []
                
                if defs_text:
                    entries = parser.parse_vocab_definitions_direct(defs_text)
                    parsed_words = [{"lemma": e["word"], "part_of_speech": e["part_of_speech"], "item_number": e["item_number"], "shown_letter": e["shown_letter"]} for e in entries]
                
                if not parsed_words:
                    parsed_words_old = parser.parse_new_words(sections.get("NEW WORDS") or sections.get("new_words", ""))
                    if not parsed_words_old:
                        preamble_text = sections.get("preamble", "")
                        if preamble_text:
                            parsed_words_old = parser.parse_new_words(preamble_text)
                            
                    parsed_words = [{"lemma": w.get("lemma") if isinstance(w, dict) else w, "part_of_speech": w.get("part_of_speech") if isinstance(w, dict) else None, "item_number": 6 + i, "shown_letter": None} for i, w in enumerate(parsed_words_old)]

                words_list = []
                for idx, word_data in enumerate(parsed_words):
                    raw_lemma = word_data.get("lemma")
                    lemma = clean_ocr_lemma(raw_lemma)
                    part_of_speech = word_data.get("part_of_speech")
                    
                    api_pron, api_pos, api_def = fetch_word_metadata(lemma)
                    pronunciation = word_data.get("pronunciation_raw", "") or api_pron
                    part_of_speech = part_of_speech or api_pos
                    
                    local_item_number = word_data["item_number"]
                    shown_letter = word_data["shown_letter"]

                    word_db = Word(
                        book_id=book_id,
                        page_id=p.id,
                        lemma=lemma,
                        pronunciation=pronunciation,
                        part_of_speech=part_of_speech,
                        definition_quiz={"api": api_def} if api_def else None,
                        pending_answer_letter=shown_letter,
                        answer_key_item_number=local_item_number,
                        is_resolved="resolved" if api_def else "pending"
                    )
                    db.add(word_db)
                    db.flush()
                    
                    if api_def:
                        definition_db = Definition(
                            word_id=word_db.id,
                            text=api_def,
                            source=DefinitionSource.dictionary_api,
                            confidence=0.8
                        )
                        db.add(definition_db)
                        
                    words_list.append((word_db, idx))

                # Parse sample sentences
                sentences_text = sections.get("Sample Sentences") or sections.get("sample_sentences", "")
                if hasattr(parser, "parse_sample_sentences"):
                    parsed_sentences = parser.parse_sample_sentences(sentences_text)
                else:
                    parsed_sentences = parser.parse_unit(page_text).sample_sentences
                    
                for s_idx, sentence in enumerate(parsed_sentences):
                    matched_word_id = None
                    for word_db, idx in words_list:
                        if re.search(r'\b' + re.escape(word_db.lemma.lower()) + r'[a-z]*\b', sentence.lower()):
                            matched_word_id = word_db.id
                            break
                    if not matched_word_id and len(words_list) > 0:
                        word_idx = s_idx % len(words_list)
                        matched_word_id = words_list[word_idx][0].id
                        
                    if matched_word_id:
                        sentence_db = SampleSentence(word_id=matched_word_id, sentence_text=sentence, source="book")
                        db.add(sentence_db)

            # Idiom check
            idiom_text = sections.get("TODAY'S IDIOM") or sections.get("idiom", "")
            if idiom_text:
                parsed_idm = parser.parse_idiom(idiom_text)
                if parsed_idm and parsed_idm.get("phrase"):
                    idiom_db = Idiom(
                        book_id=book_id, page_id=p.id,
                        phrase=parsed_idm["phrase"], meaning=parsed_idm["meaning"], example=parsed_idm["example"]
                    )
                    db.add(idiom_db)
                    
        db.commit()

        # Step 3: Resolution Pass (mirrored from process_book)
        pending_words = db.query(Word).filter(Word.book_id == book_id, Word.is_resolved == "pending").all()
        vocab_pages = db.query(Page.id).filter(Page.book_id == book_id).order_by(Page.page_number).all()
        vocab_page_ids = [p_id for (p_id,) in vocab_pages]
        
        page_offsets = {}
        daily_count = 0
        for p_id in vocab_page_ids:
            page_obj = db.query(Page).filter(Page.id == p_id).first()
            if page_obj and page_obj.parsed_sections and (page_obj.parsed_sections.get("new_words") or page_obj.parsed_sections.get("NEW WORDS")):
                daily_count += 1
                page_offsets[p_id] = 10 * (daily_count - 1)

        for word in pending_words:
            offset = page_offsets.get(word.page_id, 0)
            local_num = word.answer_key_item_number or 6
            absolute_num = local_num + offset
            word.answer_key_item_number = absolute_num
            
            letter = book_answers.get(absolute_num)
            if letter:
                word.pending_answer_letter = letter
                definitions_quiz = word.definition_quiz or {}
                def_text = definitions_quiz.get(letter)
                if def_text:
                    word.definition_quiz = {letter: def_text}
                    definition_db = Definition(word_id=word.id, text=def_text, source=DefinitionSource.book, confidence=0.9)
                    db.add(definition_db)
                    word.is_resolved = "resolved"
                else:
                    word.is_resolved = "failed"
            else:
                _, _, api_def = fetch_word_metadata(word.lemma)
                if api_def:
                    definition_db = Definition(word_id=word.id, text=api_def, source=DefinitionSource.dictionary_api, confidence=0.8)
                    db.add(definition_db)
                    word.is_resolved = "resolved"
                    word.definition_quiz = {"api": api_def}
                else:
                    word.is_resolved = "failed"
                    word.definition_quiz = None
                
        db.commit()
        book.status = BookStatus.done
        book.processed_at = datetime.utcnow()
        book.progress_message = "Reparsing complete."
        db.commit()
        logger.info(f"Reparsing book ID {book_id} succeeded.")
        
    except Exception as e:
        logger.exception(f"Error reparsing book ID {book_id}: {e}")
        db.rollback()
        book.status = BookStatus.failed
        book.error_message = str(e)
        book.progress_message = "Reparsing failed."
        db.commit()
        
    finally:
        db.close()
