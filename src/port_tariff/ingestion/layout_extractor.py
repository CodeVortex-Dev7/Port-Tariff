"""Extract positioned text blocks from PDF via pdfminer.six layout tree."""

from dataclasses import dataclass
from typing import List, Tuple

from pdfminer.high_level import extract_pages
from pdfminer.layout import LAParams, LTChar, LTTextBox, LTTextLine

from port_tariff.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class TextBlock:
    """A positioned text block on a PDF page."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    font_sizes: Tuple[float, ...]
    page_width: float
    page_height: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def x_center(self) -> float:
        return (self.x0 + self.x1) / 2.0


def extract_text_blocks(pdf_path: str) -> List[List[TextBlock]]:
    """Return a list of pages, each a list of TextBlock objects.

    boxes_flow=1.0 makes pdfminer weight vertical position over horizontal,
    keeping columns separate in the initial layout grouping.
    """
    laparams = LAParams(
        boxes_flow=1.0,
        line_overlap=0.5,
        char_margin=2.0,
        line_margin=0.5,
        word_margin=0.1,
        detect_vertical=False,
        all_texts=False,
    )

    pages: List[List[TextBlock]] = []

    for page_layout in extract_pages(pdf_path, laparams=laparams):
        pw = float(page_layout.width)
        ph = float(page_layout.height)
        blocks: List[TextBlock] = []

        for element in page_layout:
            if not isinstance(element, LTTextBox):
                continue

            text = element.get_text().strip()
            if not text:
                continue

            sizes = []
            for line in element:
                if not isinstance(line, LTTextLine):
                    continue
                for ch in line:
                    if isinstance(ch, LTChar):
                        sizes.append(round(float(ch.size), 1))

            blocks.append(
                TextBlock(
                    text=text,
                    x0=float(element.x0),
                    y0=float(element.y0),
                    x1=float(element.x1),
                    y1=float(element.y1),
                    font_sizes=tuple(sizes),
                    page_width=pw,
                    page_height=ph,
                )
            )

        pages.append(blocks)

    logger.info(
        "layout_extractor.completed",
        pages=len(pages),
        total_blocks=sum(len(p) for p in pages),
    )
    return pages
