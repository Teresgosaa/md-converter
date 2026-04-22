"""OCR engine for scanned PDF files.

Pipeline:
    PDF  →  per-page images (pdf2image / poppler)
         →  Tesseract OCR  (pytesseract)
         →  list[{page, text}]

Design decisions:
- dpi=300 is the sweet spot: good quality without excessive memory usage.
- lang defaults to "rus+eng" — covers the most common mixed-language documents.
- Returns structured list so callers can choose output format freely
  (plain text, Markdown, JSON — see core/output.py).
- On Windows, tesseract_cmd and poppler_path are set automatically.
  Override via TESSERACT_CMD / POPPLER_PATH env variables if installed elsewhere.
"""
from __future__ import annotations

import os
import platform

# Prevent PyTorch dynamo deadlock in Streamlit's threaded environment
os.environ.setdefault("TORCH_COMPILE_DISABLE", "1")


# ---------------------------------------------------------------------------
# Windows path candidates
# ---------------------------------------------------------------------------

_TESSERACT_CANDIDATES = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
]

_POPPLER_CANDIDATES = [
    r"C:\poppler\poppler-25.12.0\Library\bin",
    r"C:\poppler\Library\bin",
]


def _configure_tesseract() -> None:
    """Set pytesseract.tesseract_cmd on Windows.

    Resolution order:
    1. TESSERACT_CMD environment variable
    2. Known Windows install paths (tries each until found)
    3. Assume tesseract is in PATH (Linux / macOS)
    """
    import pytesseract

    env_path = os.environ.get("TESSERACT_CMD")
    if env_path:
        pytesseract.pytesseract.tesseract_cmd = env_path
        return

    if platform.system() == "Windows":
        for candidate in _TESSERACT_CANDIDATES:
            if os.path.isfile(candidate):
                pytesseract.pytesseract.tesseract_cmd = candidate
                return


def _get_poppler_path() -> str | None:
    """Return poppler bin path on Windows, or None on Linux/macOS.

    Resolution order:
    1. POPPLER_PATH environment variable
    2. Known Windows install paths (tries each until found)
    3. None — assume poppler is in system PATH
    """
    env_path = os.environ.get("POPPLER_PATH")
    if env_path:
        return env_path

    if platform.system() == "Windows":
        for candidate in _POPPLER_CANDIDATES:
            if os.path.isdir(candidate):
                return candidate

    return None


def _preprocess_image(img: "Image.Image") -> "Image.Image":
    """Improve scan quality before OCR.

    Pipeline: grayscale → median denoise → contrast boost → sharpen.
    Works for both Tesseract and EasyOCR — no extra dependencies beyond PIL.
    """
    from PIL import ImageFilter, ImageEnhance

    img = img.convert("L")                          # grayscale
    img = img.filter(ImageFilter.MedianFilter(3))   # remove salt-and-pepper noise
    img = ImageEnhance.Contrast(img).enhance(2.0)   # boost contrast
    img = img.filter(ImageFilter.SHARPEN)           # sharpen edges
    return img


def _check_deps() -> None:
    """Raise ImportError with install hint if optional OCR deps are missing."""
    missing = []
    try:
        import pdf2image  # noqa: F401
    except ImportError:
        missing.append("pdf2image")
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        missing.append("pytesseract")
    if missing:
        raise ImportError(
            f"Для OCR необходимо установить: {', '.join(missing)}. "
            "Выполните: pip install " + " ".join(missing)
        )


def ocr_pdf(
    file_path: str,
    lang: str = "rus+eng",
    dpi: int = 300,
) -> list[dict]:
    """Run Tesseract OCR on every page of a scanned PDF.

    Args:
        file_path: absolute path to the PDF file.
        lang:      Tesseract language string, e.g. "rus", "eng", "rus+eng".
        dpi:       rendering resolution; 300 recommended for most scans.

    Returns:
        List of dicts: [{"page": 1, "text": "..."}, {"page": 2, ...}, ...]

    Raises:
        ImportError: if pdf2image or pytesseract is not installed.
        RuntimeError: if Tesseract binary is not found.
        Exception:   propagates pdf2image / Tesseract errors as-is.
    """
    _check_deps()

    from pdf2image import convert_from_path
    import pytesseract

    _configure_tesseract()

    # Verify Tesseract is accessible before starting the (potentially long) conversion
    try:
        pytesseract.get_tesseract_version()
    except pytesseract.TesseractNotFoundError:
        raise RuntimeError(
            "Tesseract не найден. Убедитесь, что он установлен и доступен в PATH. "
            "Windows: C:\\Program Files\\Tesseract-OCR\\tesseract.exe  "
            "Linux: sudo apt install tesseract-ocr tesseract-ocr-rus  "
            "macOS: brew install tesseract tesseract-lang  "
            "Или задайте переменную окружения TESSERACT_CMD с полным путём к tesseract.exe"
        )

    poppler_path = _get_poppler_path()
    images = convert_from_path(file_path, dpi=dpi, poppler_path=poppler_path)

    pages: list[dict] = []
    for i, img in enumerate(images, start=1):
        img = _preprocess_image(img)
        text = pytesseract.image_to_string(img, lang=lang)
        pages.append({"page": i, "text": text.strip()})

    return pages


def ocr_pdf_easyocr(
    file_path: str,
    lang: str = "rus+eng",
    dpi: int = 300,
) -> list[dict]:
    """Run EasyOCR on every page of a scanned PDF.

    Args:
        file_path: absolute path to the PDF file.
        lang:      language string — "rus+eng", "rus", "eng" (same format as Tesseract).
        dpi:       rendering resolution.

    Returns:
        List of dicts: [{"page": 1, "text": "..."}, ...]
    """
    try:
        import easyocr
    except ImportError:
        raise ImportError("Установите EasyOCR: pip install easyocr")

    try:
        from pdf2image import convert_from_path
    except ImportError:
        raise ImportError("Установите pdf2image: pip install pdf2image")

    # Map Tesseract-style lang string to EasyOCR language list
    _lang_map = {"rus": "ru", "eng": "en"}
    parts = [p.strip() for p in lang.split("+")]
    easyocr_langs = [_lang_map.get(p, p) for p in parts if p in _lang_map]
    if not easyocr_langs:
        easyocr_langs = ["ru", "en"]

    poppler_path = _get_poppler_path()
    images = convert_from_path(file_path, dpi=dpi, poppler_path=poppler_path)

    import numpy as np

    # Cache reader to avoid PyTorch deadlock on repeated calls in Streamlit
    cache_key = tuple(sorted(easyocr_langs))
    if not hasattr(ocr_pdf_easyocr, "_readers"):
        ocr_pdf_easyocr._readers = {}
    if cache_key not in ocr_pdf_easyocr._readers:
        ocr_pdf_easyocr._readers[cache_key] = easyocr.Reader(easyocr_langs, gpu=False)
    reader = ocr_pdf_easyocr._readers[cache_key]

    pages: list[dict] = []
    for i, img in enumerate(images, start=1):
        result = reader.readtext(np.array(_preprocess_image(img)), detail=0, paragraph=True)
        text = "\n".join(result)
        pages.append({"page": i, "text": text.strip()})

    return pages


def is_scanned_pdf(text_from_markitdown: str) -> bool:
    """Heuristic: PDF is likely a scan if markitdown extracted very little text.

    A threshold of 50 non-whitespace characters is generous enough to avoid
    false positives on cover pages while catching genuinely empty scans.
    """
    return len(text_from_markitdown.strip()) < 50
