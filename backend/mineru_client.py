"""
Backend-side client for the Kaggle-hosted MinerU extraction service.

Architecture (matching the async design):
  Upload PDF → Return job_id immediately
             → Split into 50-page chunks
             → Process each chunk in background
             → Save each chunk result
             → Merge results at end
             → Mark job completed

This avoids holding a long HTTP connection open over the ngrok tunnel.
The free-tier ngrok drops any request that stays open longer than ~30 seconds,
which kills large file uploads AND long extraction waits.

The solution: use MINERU_KAGGLE_PATH so the Kaggle notebook reads the file
directly from its local disk (no file upload over ngrok at all). Each chunk
request is a tiny form-data POST (just page numbers), and each poll is a
single small GET — both complete well within the 30-second ngrok limit.
"""

import requests
import os
import time
import logging
import mimetypes

logger = logging.getLogger(__name__)

# Pages to extract (skip front matter like title page, TOC, etc.)
MINERU_START_PAGE = 8
MINERU_END_PAGE = 409  # inclusive

# Chunk size — 50 pages per job keeps each extraction job fast enough
# that it finishes long before Kaggle's idle-session cutoff.
CHUNK_SIZE = 50

# Per-chunk polling settings
POLL_INTERVAL_SECONDS = 10
CHUNK_MAX_WAIT_SECONDS = 60 * 15  # 15 min per chunk (generous for slow notebooks)

# Direct image request timeout settings
IMAGE_REQUEST_TIMEOUT_SECONDS = int(os.getenv("MINERU_IMAGE_REQUEST_TIMEOUT_SECONDS", "1800"))
BATCH_IMAGE_REQUEST_TIMEOUT_SECONDS = int(os.getenv("MINERU_BATCH_IMAGE_REQUEST_TIMEOUT_SECONDS", "1800"))
IMAGE_JOB_MAX_WAIT_SECONDS = int(os.getenv("MINERU_IMAGE_JOB_MAX_WAIT_SECONDS", "1800"))
BATCH_IMAGE_JOB_MAX_WAIT_SECONDS = int(os.getenv("MINERU_BATCH_IMAGE_JOB_MAX_WAIT_SECONDS", "1800"))

def _normalize_markdown_pages(data, source: str) -> list:
    """
    Convert MinerU response payloads into a non-empty list[str].

    MinerU endpoints are expected to return either:
      - {"pages": ["..."]}
      - {"markdown": "..."}
      - a raw list of page payloads

    Anything else is treated as an extraction failure so callers never
    mark a book as done with zero usable content.
    """
    def _coerce_page(page) -> str:
        if page is None:
            return ""
        if isinstance(page, str):
            return page.strip()
        if isinstance(page, dict):
            for key in ("markdown", "text", "content"):
                value = page.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return ""

    pages = []
    if isinstance(data, list):
        pages = [_coerce_page(page) for page in data]
    elif isinstance(data, dict):
        if isinstance(data.get("pages"), list):
            pages = [_coerce_page(page) for page in data.get("pages")]
        else:
            for key in ("markdown", "text", "content"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    pages = [value.strip()]
                    break
    else:
        raise RuntimeError(f"MinerU {source} returned an unsupported response type: {type(data).__name__}")

    pages = [page for page in pages if page]
    if not pages:
        raise RuntimeError(f"MinerU {source} returned no usable markdown content.")
    return pages


def _should_retry_response(response) -> bool:
    return response is not None and response.status_code in (502, 503, 504)


def _post_with_retry(url: str, **kwargs):
    last_error = None
    for attempt in range(1, 4):
        try:
            response = requests.post(url, **kwargs)
            if _should_retry_response(response):
                last_error = RuntimeError(f"HTTP {response.status_code}: {response.text[:500]}")
            else:
                return response
        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError) as exc:
            last_error = exc

        if attempt < 3:
            time.sleep(5 * attempt)

    raise RuntimeError(f"MinerU request failed after retries: {last_error}")


def _wrap_mineru_error(message: str) -> RuntimeError:
    lower = message.lower()
    if "libnvrtc-builtins.so.13.0" in lower or "nvrtc" in lower or "cuda" in lower:
        message += (
            "\n\nKaggle MinerU is still trying to use GPU. "
            "Restart the Kaggle server with CUDA_VISIBLE_DEVICES='' and MINERU_BACKEND=pipeline."
        )
    return RuntimeError(message)


def _submit_or_read_result(base_url: str, endpoint: str, files=None, data=None, timeout=30):
    response = _post_with_retry(
        f"{base_url}{endpoint}",
        files=files,
        data=data,
        timeout=timeout,
    )
    try:
        response.raise_for_status()
    except Exception:
        err_text = response.text if hasattr(response, "text") else "<unknown error>"
        raise _wrap_mineru_error(
            f"MinerU {endpoint} returned error: {getattr(response, 'status_code', '??')} - {err_text}"
        )
    payload = response.json()
    if isinstance(payload, dict):
        job_id = payload.get("job_id")
        if job_id:
            return ("job", job_id)

        if payload.get("status") == "failed":
            raise _wrap_mineru_error(f"MinerU {endpoint} failed immediately: {payload.get('error')}")

        if payload.get("pages") is not None or payload.get("markdown") is not None:
            return ("result", _normalize_markdown_pages(payload, endpoint))

    if isinstance(payload, list):
        return ("result", _normalize_markdown_pages(payload, endpoint))

    raise _wrap_mineru_error(f"MinerU {endpoint} returned an unsupported payload: {payload}")


def _poll_async_job(base_url: str, endpoint: str, job_id: str, max_wait_seconds: int) -> list:
    elapsed = 0
    while elapsed < max_wait_seconds:
        status_response = requests.get(f"{base_url}{endpoint}/{job_id}", timeout=15)
        status_response.raise_for_status()
        job = status_response.json()

        if job.get("status") == "done":
            pages = job.get("pages")
            if not pages:
                raise RuntimeError(f"MinerU job {job_id} completed but returned no pages.")
            return _normalize_markdown_pages(job, "async image extraction")

        if job.get("status") == "failed":
            raise _wrap_mineru_error(f"MinerU job {job_id} failed on Kaggle: {job.get('error')}")

        time.sleep(POLL_INTERVAL_SECONDS)
        elapsed += POLL_INTERVAL_SECONDS

    raise TimeoutError(f"MinerU job {job_id} did not finish within {max_wait_seconds}s.")


def _submit_chunk(base_url: str, kaggle_path: str, start_page: int, end_page: int) -> str:
    """
    Submit a single extraction chunk to the Kaggle MinerU service.
    Returns the job_id for polling.
    The request is tiny (form fields only) so it completes well within
    ngrok's 30-second request timeout.
    """
    response = requests.post(
        f"{base_url}/extract",
        data={
            "kaggle_path": kaggle_path,
            "start_page": start_page,
            "end_page": end_page,
        },
        timeout=30,  # well within ngrok limit — it's just form fields
    )
    try:
        response.raise_for_status()
    except Exception:
        try:
            err_text = response.text
        except Exception:
            err_text = "<could not read response>"
        raise RuntimeError(
            f"MinerU /extract returned {getattr(response, 'status_code', '??')} "
            f"for pages {start_page}-{end_page}: {err_text}"
        )
    return response.json()["job_id"]


def _poll_chunk(base_url: str, job_id: str, start_page: int, end_page: int) -> list:
    """
    Poll a single chunk job until it completes. Returns the list of page markdown strings.
    Each poll request is a tiny GET, finishes instantly.
    """
    elapsed = 0
    while elapsed < CHUNK_MAX_WAIT_SECONDS:
        status_response = requests.get(f"{base_url}/extract/{job_id}", timeout=15)
        status_response.raise_for_status()
        job = status_response.json()

        if job["status"] == "done":
            pages = job.get("pages")
            if not pages:
                raise RuntimeError(
                    f"MinerU job {job_id} (pages {start_page}-{end_page}) "
                    f"completed but returned no pages."
                )
            logger.info(
                f"Chunk {start_page}-{end_page}: job {job_id} done, "
                f"got {len(pages)} pages."
            )
            return pages

        if job["status"] == "failed":
            raise RuntimeError(
                f"MinerU job {job_id} (pages {start_page}-{end_page}) "
                f"failed on Kaggle: {job.get('error')}"
            )

        # Still processing — wait and retry
        logger.debug(
            f"Chunk {start_page}-{end_page}: job {job_id} still processing "
            f"({elapsed}s elapsed)…"
        )
        time.sleep(POLL_INTERVAL_SECONDS)
        elapsed += POLL_INTERVAL_SECONDS

    raise TimeoutError(
        f"MinerU job {job_id} (pages {start_page}-{end_page}) did not finish "
        f"within {CHUNK_MAX_WAIT_SECONDS}s. The Kaggle notebook may have gone "
        f"idle or the extraction is taking unusually long. Check the notebook."
    )


def run_mineru_extraction(
    pdf_path: str,
    kaggle_path: str = None,
    progress_callback=None,
) -> list:
    """
    Extract all pages from the Barron's PDF using the Kaggle MinerU service.

    Parameters
    ----------
    pdf_path : str
        Local path to the PDF (used only if kaggle_path is not set).
    kaggle_path : str, optional
        Path to the PDF on the Kaggle disk (e.g. /kaggle/input/my-dataset/book.pdf).
        When set, extraction skips the file upload entirely — the Kaggle notebook
        reads the file from its own disk. This is the recommended mode.
    progress_callback : callable, optional
        Called after each chunk completes with (chunk_index, total_chunks, pages_so_far).
        Use this to update a progress_message on the Book DB record.

    Returns
    -------
    list[str]
        One markdown string per page, in page order.
    """
    if not kaggle_path:
        # Direct file upload over ngrok is unreliable for large PDFs.
        # ngrok free tier drops connections after ~30 seconds, which is
        # not enough time to upload a multi-MB PDF.
        raise RuntimeError(
            "MINERU_KAGGLE_PATH is not set. Direct PDF upload over the ngrok "
            "tunnel is not supported for large files because ngrok's 30-second "
            "request limit causes upload timeouts.\n\n"
            "Fix: In your Kaggle notebook, run:\n"
            "  import glob; print(glob.glob('/kaggle/input/**/*.pdf', recursive=True))\n"
            "Then set MINERU_KAGGLE_PATH=<that path> in backend/.env and restart the backend."
        )

    base_url = os.environ["MINERU_SERVICE_URL"].rstrip("/")

    # Build chunk ranges: [start_page, start_page+CHUNK_SIZE-1], ...
    chunk_ranges = []
    page = MINERU_START_PAGE
    while page <= MINERU_END_PAGE:
        chunk_end = min(page + CHUNK_SIZE - 1, MINERU_END_PAGE)
        chunk_ranges.append((page, chunk_end))
        page = chunk_end + 1

    total_chunks = len(chunk_ranges)
    logger.info(
        f"Starting chunked MinerU extraction: {total_chunks} chunks × {CHUNK_SIZE} pages, "
        f"pages {MINERU_START_PAGE}–{MINERU_END_PAGE}, kaggle_path={kaggle_path}"
    )

    all_pages = []

    for chunk_idx, (start, end) in enumerate(chunk_ranges):
        chunk_num = chunk_idx + 1
        logger.info(f"Submitting chunk {chunk_num}/{total_chunks}: pages {start}–{end}")

        # Submit chunk — fast, just form fields
        job_id = _submit_chunk(base_url, kaggle_path, start, end)
        logger.info(f"Chunk {chunk_num}/{total_chunks}: job_id={job_id}, now polling…")

        # Poll until done
        chunk_pages = _poll_chunk(base_url, job_id, start, end)
        all_pages.extend(chunk_pages)

        logger.info(
            f"Chunk {chunk_num}/{total_chunks} complete. "
            f"Total pages so far: {len(all_pages)}"
        )

        # Notify caller so it can update DB progress_message
        if progress_callback:
            try:
                progress_callback(chunk_num, total_chunks, len(all_pages))
            except Exception as cb_err:
                logger.warning(f"progress_callback raised: {cb_err}")

    logger.info(
        f"MinerU extraction complete: {len(all_pages)} total pages extracted."
    )
    return all_pages


def run_mineru_image_extraction(image_path: str) -> list:
    """
    Extract text/markdown from a single image file using the Kaggle MinerU service.
    
    Uploads the image directly via a multipart/form-data POST request.
    Since it's a single image, the upload is fast and avoids the ngrok 30-second timeout.
    """
    base_url = os.environ["MINERU_SERVICE_URL"].rstrip("/")
    logger.info(f"Uploading image to MinerU service: {image_path}")
    
    with open(image_path, "rb") as f:
        mode, value = _submit_or_read_result(
            base_url,
            "/extract_image",
            files={"file": f},
            timeout=IMAGE_REQUEST_TIMEOUT_SECONDS,
        )

    if mode == "result":
        return value

    return _poll_async_job(
        base_url,
        "/extract_image",
        value,
        IMAGE_JOB_MAX_WAIT_SECONDS,
    )


def run_mineru_images_extraction(image_paths: list) -> list:
    """
    Extract text/markdown from multiple image files in one request.

    This uses the Kaggle /extract_images endpoint, which keeps the ordering
    stable and avoids repeated network round-trips for small image batches.
    """
    if not image_paths:
        raise RuntimeError("No image paths were provided for batch extraction.")

    base_url = os.environ["MINERU_SERVICE_URL"].rstrip("/")
    logger.info(f"Uploading {len(image_paths)} images to MinerU service in a batch.")

    files = []
    handles = []
    try:
        for image_path in image_paths:
            handle = open(image_path, "rb")
            handles.append(handle)
            mime_type = mimetypes.guess_type(image_path)[0] or "application/octet-stream"
            files.append(("files", (os.path.basename(image_path), handle, mime_type)))

        mode, value = _submit_or_read_result(
            base_url,
            "/extract_images",
            files=files,
            timeout=BATCH_IMAGE_REQUEST_TIMEOUT_SECONDS,
        )

        if mode == "result":
            return value

        return _poll_async_job(
            base_url,
            "/extract_images",
            value,
            BATCH_IMAGE_JOB_MAX_WAIT_SECONDS,
        )
    finally:
        for handle in handles:
            try:
                handle.close()
            except Exception:
                pass