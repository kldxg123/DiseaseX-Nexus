from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser


@dataclass(frozen=True)
class ExtractedContent:
    title: str | None
    body_text: str


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title_parts: list[str] = []
        self.body_parts: list[str] = []
        self._in_title = False
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title_parts.append(data)
        else:
            self.body_parts.append(data)


def _squash_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def extract_content(html: str) -> ExtractedContent:
    parser = _HTMLTextExtractor()
    parser.feed(html)
    title = _squash_whitespace(" ".join(parser.title_parts)) or None
    body_text = _squash_whitespace(" ".join(parser.body_parts))
    return ExtractedContent(title=title, body_text=body_text)
