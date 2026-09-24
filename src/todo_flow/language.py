"""Project language for human-facing output; protocol keys remain stable."""

LANGUAGES = {"en": "English", "ko": "한국어"}


def select_language(value=None, *, interactive=False):
    if value is None and interactive:
        while True:
            value = input("Primary language / 기본 언어 [en: English, ko: 한국어] (en): ").strip()
            if not value or value in LANGUAGES:
                break
            print("Choose en or ko / en 또는 ko를 선택하세요.")
    value = value or "en"
    if value not in LANGUAGES:
        raise ValueError("Language must be en or ko")
    return value


def output_instruction(language):
    name = LANGUAGES[select_language(language)]
    return (
        f"Write human-facing summaries, questions, findings, next-task purposes and new track "
        f"documents in {name}. Preserve JSON keys, enum values, IDs, commands, paths, source "
        "quotes and existing code conventions. Explicit user language requests take precedence."
    )
