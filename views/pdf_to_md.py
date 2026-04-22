"""File → Markdown / OCR conversion view (3-step flow).

Supported formats: PDF, DOCX, PPTX, XLSX, CSV, JSON.

Two conversion paths for PDF:
- Text-based PDF  → markitdown (fast, preserves structure)
- Scanned PDF     → Tesseract OCR via core.ocr (pdf2image + pytesseract)

The path is chosen automatically: if markitdown returns < 50 non-whitespace
chars the file is treated as a scan and the OCR branch is activated.
The user can also force OCR manually via a checkbox on the convert step.
"""
from __future__ import annotations

import os
import tempfile

import streamlit as st

from ui.step_indicator import render_steps, STEPS_PDF_MD

_STAGE         = "pdf_md_stage"
_STAGE_UPLOAD  = "upload"
_STAGE_CONVERT = "convert"
_STAGE_RESULT  = "result"

_FILE_PATH    = "pdf_md_file_path"
_FILE_NAME    = "pdf_md_file_name"
_FILE_SIZE    = "pdf_md_file_size"
_MD_RESULT    = "pdf_md_result"
_OCR_PAGES    = "pdf_md_ocr_pages"
_IS_OCR       = "pdf_md_is_ocr"

_SUPPORTED_TYPES = ["pdf", "docx", "pptx", "xlsx", "csv", "json"]

_LANG_OPTIONS = {
    "Русский + Английский": "rus+eng",
    "Только русский":       "rus",
    "Только английский":    "eng",
}

_TYPE_LABELS = {
    "pdf":  "PDF документ",
    "docx": "Word документ",
    "pptx": "PowerPoint презентация",
    "xlsx": "Excel таблица",
    "csv":  "CSV файл",
    "json": "JSON файл",
}


def render() -> None:
    st.header("Конвертация в Markdown")
    stage = st.session_state.get(_STAGE, _STAGE_UPLOAD)
    if stage == _STAGE_UPLOAD:
        _render_step_upload()
    elif stage == _STAGE_CONVERT:
        _render_step_convert()
    elif stage == _STAGE_RESULT:
        _render_step_result()


def _render_step_upload() -> None:
    render_steps(current=1, steps=STEPS_PDF_MD)
    st.subheader("Загрузите файл")

    uploaded = st.file_uploader(
        " ",
        type=_SUPPORTED_TYPES,
        key="pdf_md_uploader",
    )

    if uploaded is not None:
        if st.button("Далее", type="primary", width='stretch'):
            upload_dir = os.path.join(tempfile.gettempdir(), "enigma_uploads")
            os.makedirs(upload_dir, exist_ok=True)
            file_path = os.path.join(upload_dir, uploaded.name)
            file_bytes = uploaded.read()
            with open(file_path, "wb") as f:
                f.write(file_bytes)

            st.session_state[_FILE_PATH] = file_path
            st.session_state[_FILE_NAME] = uploaded.name
            st.session_state[_FILE_SIZE] = len(file_bytes)
            st.session_state[_STAGE]     = _STAGE_CONVERT
            st.rerun()


def _render_step_convert() -> None:
    render_steps(current=2, steps=STEPS_PDF_MD)
    file_name  = st.session_state.get(_FILE_NAME, "файл")
    file_size  = st.session_state.get(_FILE_SIZE, 0)
    ext        = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    type_label = _TYPE_LABELS.get(ext, "Файл")
    size_str   = f"{file_size / 1_048_576:.2f} MB" if file_size >= 1_048_576 else f"{file_size / 1024:.1f} KB"
    is_pdf     = ext == "pdf"

    st.subheader("Конвертация")
    st.markdown(
        f"""
        <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;
                    padding:1rem 1.2rem;margin-bottom:1rem;display:flex;gap:1rem;align-items:center">
            <span style="font-size:2rem">{_file_emoji(ext)}</span>
            <div>
                <div style="font-weight:600;color:#1e293b">{file_name}</div>
                <div style="font-size:0.85rem;color:#64748b">{type_label} • {size_str}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if is_pdf:
        n_pages = _get_pdf_page_count(st.session_state[_FILE_PATH])
        if n_pages and n_pages > 50:
            st.warning(
                f"Документ содержит {n_pages} страниц. "
                "Конвертация может занять несколько минут и потребовать много памяти. "
                "Для сканов рекомендуется режим OCR."
            )

    force_ocr = False
    ocr_lang  = "rus+eng"
    ocr_engine = "tesseract"
    if is_pdf:
        force_ocr = st.checkbox(
            "Сканированный PDF (OCR)",
            value=False,
            help="Включите если PDF является сканом без выделяемого текста.",
        )
        if force_ocr:
            ocr_engine = st.radio(
                "Движок OCR",
                options=["EasyOCR (лучше для русского)", "Tesseract"],
                index=0,
                horizontal=True,
            )
            ocr_engine = "easyocr" if ocr_engine.startswith("Easy") else "tesseract"
            lang_label = st.selectbox(
                "Язык распознавания",
                options=list(_LANG_OPTIONS.keys()),
                index=0,
            )
            ocr_lang = _LANG_OPTIONS[lang_label]
            st.info("⏰ OCR обрабатывает каждую страницу отдельно — для многостраничных документов ожидайте несколько минут.")

    col_back, col_convert = st.columns([1, 1])
    with col_back:
        if st.button("Назад", width='stretch'):
            _cleanup()
            st.rerun()
    with col_convert:
        if st.button("Конвертировать", type="primary", width='stretch'):
            file_path = st.session_state[_FILE_PATH]
            if is_pdf and force_ocr:
                spinner_text = "Распознаём текст через EasyOCR…" if ocr_engine == "easyocr" else "Распознаём текст через Tesseract…"
                with st.spinner(spinner_text):
                    pages, error = _run_ocr(file_path, lang=ocr_lang, engine=ocr_engine)
                if error:
                    st.error(error)
                else:
                    st.session_state[_OCR_PAGES] = pages
                    st.session_state[_IS_OCR]    = True
                    st.session_state[_STAGE]     = _STAGE_RESULT
                    st.rerun()
            else:
                with st.spinner("Конвертируем…"):
                    md_text, error = _convert_docling(file_path)
                if error:
                    st.error(error)
                elif is_pdf and len(md_text.replace(" ", "").replace("\n", "")) < 50:
                    st.warning(
                        "PDF не содержит извлекаемого текста — вероятно, это скан. "
                        "Включите флажок **«Сканированный PDF (OCR)»** и запустите конвертацию заново."
                    )
                else:
                    from core.md_postprocess import postprocess
                    md_text = postprocess(md_text)
                    st.session_state[_MD_RESULT] = md_text
                    st.session_state[_IS_OCR]    = False
                    st.session_state[_STAGE]     = _STAGE_RESULT
                    st.rerun()


def _render_step_result() -> None:
    render_steps(current=3, steps=STEPS_PDF_MD)
    is_ocr    = st.session_state.get(_IS_OCR, False)
    file_name = st.session_state.get(_FILE_NAME, "файл")
    base      = file_name.rsplit(".", 1)[0] if "." in file_name else file_name

    st.subheader("Результат конвертации")
    if is_ocr:
        _render_ocr_result(base)
    else:
        _render_md_result(base)

    col_back, col_reset = st.columns([1, 1])
    with col_back:
        if st.button("Назад к конвертации", width='stretch'):
            st.session_state.pop(_MD_RESULT, None)
            st.session_state.pop(_OCR_PAGES, None)
            st.session_state.pop(_IS_OCR, None)
            st.session_state[_STAGE] = _STAGE_CONVERT
            st.rerun()
    with col_reset:
        if st.button("Сбросить", width='stretch'):
            _cleanup()
            st.rerun()


def _render_ocr_result(base: str) -> None:
    from core.output import generate_ocr_md, generate_ocr_txt

    pages = st.session_state[_OCR_PAGES]
    total_chars = sum(len(p["text"]) for p in pages)
    total_words = sum(len(p["text"].split()) for p in pages)

    col1, col2, col3 = st.columns(3)
    col1.metric("Страниц",  str(len(pages)))
    col2.metric("Символов", f"{total_chars:,}")
    col3.metric("Слов",     f"{total_words:,}")

    st.markdown("**Превью — страница 1**")
    preview = pages[0]["text"] if pages else ""
    st.code(preview[:1000] + ("…" if len(preview) > 1000 else ""), language="")

    st.markdown("Скачать результат")
    dl1, dl2 = st.columns(2)
    with dl1:
        st.download_button(
            label="Скачать .md",
            data=generate_ocr_md(pages),
            file_name=f"{base}_ocr.md",
            mime="text/markdown",
            width='stretch',
            type="primary",
        )
    with dl2:
        st.download_button(
            label="Скачать .txt",
            data=generate_ocr_txt(pages),
            file_name=f"{base}_ocr.txt",
            mime="text/plain",
            width='stretch',
            type="primary",
        )


def _render_md_result(base: str) -> None:
    md_text = st.session_state[_MD_RESULT]

    col1, col2, col3 = st.columns(3)
    col1.metric("Строк",    f"{len(md_text.splitlines()):,}")
    col2.metric("Символов", f"{len(md_text):,}")
    col3.metric("Слов",     f"{len(md_text.split()):,}")

    st.markdown("**Превью (первые 1000 символов)**")
    st.code(md_text[:1000] + ("…" if len(md_text) > 1000 else ""), language="markdown")

    st.download_button(
        label="Скачать .md файл",
        data=md_text.encode("utf-8"),
        file_name=f"{base}.md",
        mime="text/markdown",
        width='stretch',
        type="primary",
    )


def _render_ocr_result(base: str) -> None:
    from core.output import generate_ocr_md, generate_ocr_txt

    pages = st.session_state[_OCR_PAGES]
    total_chars = sum(len(p["text"]) for p in pages)
    total_words = sum(len(p["text"].split()) for p in pages)

    col1, col2, col3 = st.columns(3)
    col1.metric("Страниц",  str(len(pages)))
    col2.metric("Символов", f"{total_chars:,}")
    col3.metric("Слов",     f"{total_words:,}")

    st.markdown("**Превью — страница 1**")
    preview = pages[0]["text"] if pages else ""
    st.code(
        preview[:1000] + ("…" if len(preview) > 1000 else ""),
        language="",
    )

    st.markdown("Скачать результат")
    dl1, dl2 = st.columns(2)
    with dl1:
        st.download_button(
            label="Скачать .md",
            data=generate_ocr_md(pages),
            file_name=f"{base}_ocr.md",
            mime="text/markdown",
            width='stretch',
            type="primary",
        )
    with dl2:
        st.download_button(
            label="Скачать .txt",
            data=generate_ocr_txt(pages),
            file_name=f"{base}_ocr.txt",
            mime="text/plain",
            width='stretch',
            type="primary",
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _file_emoji(ext: str) -> str:
    return {
        "pdf":  "📄",
        "docx": "📝",
        "pptx": "📊",
        "xlsx": "📊",
        "csv":  "📃",
        "json": "📄",
    }.get(ext, "📄")


def _is_scanned(text: str) -> bool:
    from core.ocr import is_scanned_pdf
    return is_scanned_pdf(text)


def _convert_docling(file_path: str) -> tuple[str, str | None]:
    from core.converter import _docling
    return _docling(file_path)


def _run_ocr(file_path: str, lang: str = "rus+eng", engine: str = "easyocr") -> tuple[list[dict], str | None]:
    try:
        if engine == "easyocr":
            from core.ocr import ocr_pdf_easyocr
            pages = ocr_pdf_easyocr(file_path, lang=lang)
        else:
            from core.ocr import ocr_pdf
            pages = ocr_pdf(file_path, lang=lang)
        if not any(p["text"] for p in pages):
            return pages, "OCR не смог распознать текст. Проверьте качество скана."
        return pages, None
    except ImportError as e:
        return [], str(e)
    except RuntimeError as e:
        return [], str(e)
    except Exception as e:
        return [], f"Ошибка OCR: {e}"


def _get_pdf_page_count(file_path: str) -> int | None:
    try:
        import pypdfium2
        doc = pypdfium2.PdfDocument(file_path)
        n = len(doc)
        doc.close()
        return n
    except Exception:
        return None


def _cleanup() -> None:
    file_path = st.session_state.get(_FILE_PATH)
    if file_path and os.path.exists(file_path):
        try:
            os.remove(file_path)
        except OSError:
            pass
    for key in [_FILE_PATH, _FILE_NAME, _FILE_SIZE, _MD_RESULT, _OCR_PAGES, _IS_OCR, _STAGE]:
        st.session_state.pop(key, None)
