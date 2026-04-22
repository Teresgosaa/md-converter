import streamlit as st

STEPS_MASKING    = ["Загрузка файла", "Выбор колонок", "Результат"]
STEPS_DECRYPTION = ["Загрузка файла", "Демаскирование", "Результат"]
STEPS_PDF_MD     = ["Загрузка файла", "Конвертация",    "Результат"]
STEPS_MD_MASK    = ["Загрузка файла", "Выбор объектов", "Результат"]
STEPS_MD_DECR    = ["Загрузка файлов", "Результат"]

# Keep old name for backward compatibility
STEPS = STEPS_MASKING


def render_steps(current: int, steps: list[str] | None = None) -> None:
    """Render step indicator. current is 1-based (1, 2, or 3).

    Args:
        current: current active step (1-based)
        steps:   list of step labels; defaults to STEPS_MASKING
    """
    if steps is None:
        steps = STEPS_MASKING
    cols = st.columns(len(steps))
    for i, (col, label) in enumerate(zip(cols, steps), start=1):
        if i == current:
            col.markdown(f"**:blue[Шаг {i}: {label}]**")
        elif i < current:
            col.markdown(f"~~Шаг {i}: {label}~~")
        else:
            col.markdown(f"Шаг {i}: {label}")
