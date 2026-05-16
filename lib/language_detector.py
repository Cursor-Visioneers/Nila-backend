"""Language detection helpers."""

import re

from langdetect import LangDetectException, detect


def detect_language(text: str) -> str:
    text = text or ""

    # Hard rule: Sinhala Unicode block
    if re.search(r"[\u0D80-\u0DFF]", text):
        return "si"

    # Tamil Unicode block
    if re.search(r"[\u0B80-\u0BFF]", text):
        return "ta"

    try:
        lang = detect(text)
        if lang == "si":
            return "si"
        if lang == "ta":
            return "ta"
        return "en"
    except LangDetectException:
        return "en"
    except Exception:
        return "en"
