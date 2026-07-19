"""Convert provider-authored markup fragments into display-safe plain text."""

from html import unescape
from html.parser import HTMLParser

from typing_extensions import override


class _TextExtractor(HTMLParser):
    """Collect text nodes while discarding tags and attributes."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    @override
    def handle_data(self, data: str) -> None:
        """Keep provider text content only."""
        self.parts.append(data)


def plain_text(value: str) -> str:
    """Decode entities, remove markup, and normalize provider whitespace."""
    parser = _TextExtractor()
    parser.feed(unescape(value))
    parser.close()
    return " ".join("".join(parser.parts).split())
