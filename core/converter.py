"""Конвертация любого поддерживаемого формата в plain-text (Markdown).

Является тонкой обёрткой над docling и core.ocr.
Используется в md_masking — до шага detect_entities().

Поддерживаемые форматы:
    .md / .txt  — возвращаются без изменений
    .pdf        — docling (текстовый PDF) или Tesseract OCR (скан)
    .docx       — docling
    .doc        — docling
    .pptx       — docling
    .odt        — docling

Возвращаемые значения:
    (text: str, warning: str | None)
    warning != None означает, что конвертация прошла с оговорками
    (например, скан распознан через OCR).
    При ошибке бросается RuntimeError с человекочитаемым сообщением.
"""
from __future__ import annotations

import os
import tempfile

# Форматы, требующие конвертации
CONVERTIBLE_TYPES = ["pdf", "docx", "doc", "pptx", "odt"]
# Форматы, принимаемые напрямую (без конвертации)
PASSTHROUGH_TYPES = ["md", "txt"]
# Все допустимые расширения для file_uploader
ACCEPTED_TYPES = PASSTHROUGH_TYPES + CONVERTIBLE_TYPES

# Порог: если docling вернул меньше символов — считаем скан
_SCAN_THRESHOLD = 50


def file_to_markdown(
    file_bytes: bytes,
    file_name: str,
    ocr_lang: str = "rus+eng",
    force_ocr: bool = False,
) -> tuple[str, str | None]:
    """Конвертировать байты загруженного файла в Markdown-текст.

    Args:
        file_bytes: содержимое файла.
        file_name:  оригинальное имя (используется для определения расширения).
        ocr_lang:   язык Tesseract (актуально только для PDF-скан).
        force_ocr:  принудительно использовать OCR вместо markitdown.

    Returns:
        (text, warning) — warning = None если всё хорошо.

    Raises:
        RuntimeError: неустранимая ошибка конвертации.
    """
    ext = _get_ext(file_name)

    # --- Passthrough: MD и TXT ---
    if ext in PASSTHROUGH_TYPES:
        text = file_bytes.decode("utf-8", errors="replace")
        return text, None

    # --- Требует временного файла ---
    tmp_path = _save_temp(file_bytes, file_name)
    try:
        return _convert_from_path(tmp_path, ext, ocr_lang=ocr_lang, force_ocr=force_ocr)
    finally:
        _remove_temp(tmp_path)


# ---------------------------------------------------------------------------
# Внутренние функции
# ---------------------------------------------------------------------------

def _get_ext(file_name: str) -> str:
    return file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""


def _save_temp(data: bytes, name: str) -> str:
    upload_dir = os.path.join(tempfile.gettempdir(), "enigma_uploads")
    os.makedirs(upload_dir, exist_ok=True)
    path = os.path.join(upload_dir, name)
    with open(path, "wb") as f:
        f.write(data)
    return path


def _remove_temp(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _convert_from_path(
    path: str,
    ext: str,
    ocr_lang: str,
    force_ocr: bool,
) -> tuple[str, str | None]:
    is_pdf = ext == "pdf"

    # PDF-скан: OCR по умолчанию (force_ocr) или если docling вернул мало текста
    if is_pdf and force_ocr:
        return _ocr_to_md(path, ocr_lang)

    # Все остальные форматы + текстовый PDF — docling
    text, error = _docling(path)
    if error:
        raise RuntimeError(error)

    # Авто-детект скана: мало текста → OCR
    if is_pdf and len(text.replace(" ", "").replace("\n", "")) < _SCAN_THRESHOLD:
        warning = (
            "PDF похож на скан: markitdown не нашёл текст. "
            "Запускаем Tesseract OCR автоматически."
        )
        text, _ = _ocr_to_md(path, ocr_lang)
        return text, warning

    return text, None


def _docling(path: str) -> tuple[str, str | None]:
    try:
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.datamodel.base_models import InputFormat

        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_ocr = False       # отключаем внутренний RapidOCR — он падает на больших сканах
        pipeline_options.images_scale = 1.0   # по умолчанию 2.0; снижает расход памяти в 4 раза

        converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
            }
        )
        result = converter.convert(path)
        return result.document.export_to_markdown(), None
    except ImportError:
        return "", "Установите docling: pip install docling"
    except MemoryError:
        return "", (
            "Недостаточно памяти для обработки документа. "
            "Попробуйте файл меньшего объёма или переключитесь в режим OCR."
        )
    except Exception as exc:
        if "bad_alloc" in str(exc) or "MemoryError" in str(exc):
            return "", (
                "Недостаточно памяти для обработки документа. "
                "Попробуйте файл меньшего объёма или переключитесь в режим OCR."
            )
        return "", f"Ошибка конвертации: {exc}"


def _ocr_to_md(path: str, lang: str) -> tuple[str, str | None]:
    try:
        from core.ocr import ocr_pdf
        from core.output import generate_ocr_md
        pages = ocr_pdf(path, lang=lang)
        if not any(p["text"] for p in pages):
            raise RuntimeError(
                "OCR не смог распознать текст. "
                "Проверьте качество скана и наличие языкового пакета Tesseract."
            )
        md = generate_ocr_md(pages).decode("utf-8", errors="replace")
        return md, "Файл распознан через Tesseract OCR."
    except ImportError as exc:
        raise RuntimeError(f"OCR недоступен: {exc}") from exc
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Ошибка OCR: {exc}") from exc
