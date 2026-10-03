"""Project language for human-facing output; protocol keys remain stable."""

LANGUAGES = {"en": "English", "ko": "한국어", "ja": "日本語", "zh-CN": "简体中文"}


def select_language(value=None, *, interactive=False):
    choices = ", ".join(f"{code}: {name}" for code, name in LANGUAGES.items())
    if value is None and interactive:
        while True:
            value = input(f"Primary language / 기본 언어 / 言語 / 语言 [{choices}] (en): ").strip()
            if not value or value in LANGUAGES:
                break
            print(f"Choose one of: {', '.join(LANGUAGES)}")
    value = value or "en"
    if value not in LANGUAGES:
        raise ValueError(f"Language must be one of: {', '.join(LANGUAGES)}")
    return value


def output_instruction(language):
    name = LANGUAGES[select_language(language)]
    return (
        f"Write human-facing summaries, questions, findings, next-task purposes and new track "
        f"documents in {name}. Preserve JSON keys, enum values, IDs, commands, paths, source "
        "quotes and existing code conventions. Explicit user language requests take precedence."
    )
