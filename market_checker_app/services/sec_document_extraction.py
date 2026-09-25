from __future__ import annotations

from html.parser import HTMLParser
import re


class _ReadableText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._hidden = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._hidden += 1
        elif tag in {"p", "br", "div", "tr", "h1", "h2", "h3"}:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._hidden:
            self._hidden -= 1
        elif tag in {"p", "div", "tr", "h1", "h2", "h3"}:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._hidden:
            self.parts.append(data)


def extract_sec_item_excerpts(document: bytes, *, form: str) -> tuple[tuple[str, str], ...]:
    """Return bounded, cited 8-K sections for review, not interpreted claims."""
    if form.upper().removesuffix("/A") != "8-K":
        return ()
    parser = _ReadableText()
    parser.feed(document.decode("utf-8", errors="replace"))
    plain = " ".join(" ".join(parser.parts).split())
    pattern = re.compile(r"\bItem\s+(\d{1,2}\.\d{2})\b", re.IGNORECASE)
    matches = list(pattern.finditer(plain))
    excerpts: list[tuple[str, str]] = []
    seen: set[str] = set()
    for index, match in enumerate(matches):
        code = match.group(1)
        if code in seen:
            continue
        seen.add(code)
        end = matches[index + 1].start() if index + 1 < len(matches) else len(plain)
        excerpt = plain[match.end():min(end, match.end() + 700)].strip(" .:-")
        if excerpt:
            excerpts.append((f"Item {code}", excerpt))
        if len(excerpts) >= 3:
            break
    return tuple(excerpts)
