"""Convert stored translations into the form the game can render."""
import re

import arabic_reshaper
from bidi.algorithm import get_display

VISUAL_ORDER_LANGUAGES = {"arSA"}
LINE_BREAK = re.compile(r"(\r?\n)")


def display_text(text: str, lang_code: str) -> str:
    if lang_code not in VISUAL_ORDER_LANGUAGES:
        return text
    # The game neither joins Arabic letters nor lays text out right-to-left
    return "".join(
        part if LINE_BREAK.fullmatch(part) else get_display(arabic_reshaper.reshape(part), base_dir="L")
        for part in LINE_BREAK.split(text)
    )
