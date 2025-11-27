"""Utilities for exporting functional specifications to PDF."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
import subprocess
from shutil import which
from typing import Any, Iterator, Optional

logger = logging.getLogger(__name__)


class PDFExportError(RuntimeError):
    """Raised when a specification PDF cannot be generated."""


@dataclass(slots=True)
class _RenderBlock:
    """Represents a logical block of content to render."""

    kind: str
    text: str = ""
    level: int = 0
    extra: str = ""
    note: str = ""
    alt_text: Optional[str] = None
    asset_path: Optional[str] = None
    rows: Optional[list[list[str]]] = None


class SpecificationPDFExporter:
    """Render Markdown specifications to PDF using a minimal layout."""

    _BULLET_RE = re.compile(r"^(?P<indent>\s*)([-*])\s+(?P<text>.+)$")
    _ORDERED_RE = re.compile(
        r"^(?P<indent>\s*)(?P<number>\d+)\.\s+(?P<text>.+)$"
    )
    _IMAGE_RE = re.compile(r"!\[(?P<alt>.*?)\]\((?P<path>.*?)\)")

    _UNICODE_TRANSLATION = str.maketrans(
        {
            "\u00a0": " ",  # non-breaking space
            "\u00ad": "-",  # soft hyphen
            "\u2010": "-",  # hyphen
            "\u2011": "-",  # non-breaking hyphen
            "\u2012": "-",  # figure dash
            "\u2013": "-",  # en dash
            "\u2014": "-",  # em dash
            "\u2015": "-",  # horizontal bar
            "\u2018": "'",  # left single quote
            "\u2019": "'",  # right single quote
            "\u201c": '"',  # left double quote
            "\u201d": '"',  # right double quote
            "\u202f": " ",  # narrow non-breaking space
            "\u2212": "-",  # minus sign
        }
    )

    def __init__(self, asset_root: Path) -> None:
        self._asset_root = Path(asset_root)

    def export(self, markdown_text: str, destination: Path) -> Path:
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:  # pragma: no cover - filesystem guard
            raise PDFExportError(
                f"Unable to create directory for PDF export: {destination}"
            ) from exc

        try:
            from fpdf import FPDF  # type: ignore[import]
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise PDFExportError(
                "fpdf2 is required to export specifications as PDF."
            ) from exc

        pdf: Any = FPDF(unit="mm", format="A4")
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.set_margin(15)
        pdf.add_page()
        pdf.set_title("Functional Specification")

        for block in self._iter_blocks(markdown_text):
            self._render_block(pdf, block)

        try:
            pdf.output(str(destination))
        except (OSError, RuntimeError) as exc:
            raise PDFExportError(
                f"Unable to write specification PDF: {destination}"
            ) from exc
        return destination

    def _iter_blocks(self, markdown_text: str) -> Iterator[_RenderBlock]:
        table_buffer: list[list[str]] = []

        for raw_line in markdown_text.splitlines():
            line = raw_line.rstrip("\n")
            stripped = line.strip()

            if not stripped and table_buffer:
                yield _RenderBlock(kind="table", rows=table_buffer)
                table_buffer = []

            if not stripped:
                yield _RenderBlock(kind="blank")
                continue

            image_match = self._IMAGE_RE.match(stripped)
            if image_match:
                yield _RenderBlock(
                    kind="image",
                    alt_text=image_match.group("alt").strip() or None,
                    asset_path=image_match.group("path").strip() or None,
                )
                continue

            if stripped.startswith("### "):
                yield _RenderBlock(
                    kind="heading3",
                    text=self._clean_inline(stripped[4:]),
                )
                continue
            if stripped.startswith("## "):
                yield _RenderBlock(
                    kind="heading2",
                    text=self._clean_inline(stripped[3:]),
                )
                continue
            if stripped.startswith("# "):
                yield _RenderBlock(
                    kind="heading1",
                    text=self._clean_inline(stripped[2:]),
                )
                continue
            if stripped.startswith("**") and stripped.endswith("**"):
                yield _RenderBlock(
                    kind="heading3",
                    text=self._clean_inline(stripped.strip("*")),
                )
                continue

            bullet_match = self._BULLET_RE.match(line)
            if bullet_match:
                if table_buffer:
                    yield _RenderBlock(kind="table", rows=table_buffer)
                    table_buffer = []
                indent_spaces = len(bullet_match.group("indent"))
                yield _RenderBlock(
                    kind="bullet",
                    text=self._clean_inline(bullet_match.group("text")),
                    level=indent_spaces // 2,
                )
                continue

            ordered_match = self._ORDERED_RE.match(line)
            if ordered_match:
                if table_buffer:
                    yield _RenderBlock(kind="table", rows=table_buffer)
                    table_buffer = []
                indent_spaces = len(ordered_match.group("indent"))
                yield _RenderBlock(
                    kind="numbered",
                    text=self._clean_inline(ordered_match.group("text")),
                    extra=ordered_match.group("number"),
                    level=indent_spaces // 2,
                )
                continue

            if stripped.startswith("|"):
                cells = [
                    self._clean_inline(cell)
                    for cell in stripped.strip("|").split("|")
                ]
                if all(
                    not cell.replace("-", "").replace(":", "").strip()
                    for cell in cells
                ):
                    continue
                table_buffer.append(cells)
                continue

            if table_buffer:
                yield _RenderBlock(kind="table", rows=table_buffer)
                table_buffer = []

            yield _RenderBlock(
                kind="paragraph",
                text=self._clean_inline(stripped),
            )

        if table_buffer:
            yield _RenderBlock(kind="table", rows=table_buffer)

    def _render_block(self, pdf: Any, block: _RenderBlock) -> None:
        if block.kind == "blank":
            pdf.ln(6)
            return

        if block.kind in {"heading1", "heading2", "heading3"}:
            font_size = 18 if block.kind == "heading1" else 15
            if block.kind == "heading3":
                font_size = 13
            pdf.set_font("Helvetica", "B", size=font_size)
            self._reset_to_margin(pdf)
            pdf.multi_cell(0, 8, self._safe_text(block.text))
            pdf.ln(2)
            pdf.set_font("Helvetica", size=11)
            return

        if block.kind == "paragraph":
            pdf.set_font("Helvetica", size=11)
            self._reset_to_margin(pdf)
            pdf.multi_cell(0, 6, self._safe_text(block.text))
            pdf.ln(2)
            return

        if block.kind == "table":
            self._render_table(pdf, block.rows or [])
            pdf.ln(2)
            return

        if block.kind == "bullet":
            indent = min(block.level, 8) * 4
            pdf.set_font("Helvetica", size=11)
            x = pdf.l_margin + indent
            pdf.set_x(x)
            bullet_text = f"- {block.text}"
            pdf.multi_cell(0, 6, self._safe_text(bullet_text))
            pdf.ln(1)
            return

        if block.kind == "numbered":
            indent = min(block.level, 8) * 4
            pdf.set_font("Helvetica", size=11)
            x = pdf.l_margin + indent
            pdf.set_x(x)
            if block.extra:
                numbered_text = f"{block.extra}. {block.text}"
            else:
                numbered_text = block.text
            pdf.multi_cell(0, 6, self._safe_text(numbered_text))
            pdf.ln(1)
            return

        if block.kind == "requirement":
            pdf.set_font("Helvetica", "B", size=11)
            self._reset_to_margin(pdf)
            pdf.multi_cell(0, 6, self._safe_text(block.text))
            pdf.set_font("Helvetica", size=11)
            if block.extra:
                self._reset_to_margin(pdf)
                pdf.multi_cell(
                    0,
                    6,
                    self._safe_text(f"Description: {block.extra}"),
                )
            if block.note:
                self._reset_to_margin(pdf)
                pdf.multi_cell(
                    0,
                    6,
                    self._safe_text(
                        "Business Rules / Data Dependency: "
                        f"{block.note}"
                    ),
                )
            pdf.ln(2)
            return

        if block.kind == "image":
            self._render_image(pdf, block)
            return

        logger.debug("Unhandled render block kind: %s", block.kind)

    def _render_table(self, pdf: Any, rows: list[list[str]]) -> None:
        if not rows:
            return

        available_width = pdf.w - pdf.l_margin - pdf.r_margin
        header = list(rows[0])
        data_rows = rows[1:] if len(rows) > 1 else []
        column_count = len(header)
        if column_count == 0:
            return

        pdf.set_font("Helvetica", size=9)
        preferred_widths = [24.0] * column_count
        if (
            column_count == 3
            and header[0].strip().lower() == "spec id"
            and "specification" in header[1].lower()
        ):
            header[2] = "Business Rules\nData Dependency"
            preferred_widths = [72.0, 58.0, 52.0]

        max_widths = [0.0] * column_count
        sample_rows = [header] + data_rows
        for row in sample_rows:
            for idx, cell in enumerate(row[:column_count]):
                safe = self._safe_text(cell)
                cell_width = pdf.get_string_width(safe) + 6
                if cell_width > max_widths[idx]:
                    max_widths[idx] = cell_width

        column_widths = [
            max(max_widths[idx], preferred_widths[idx])
            for idx in range(column_count)
        ]
        total_width = sum(column_widths) or 1.0
        if total_width > available_width:
            scale = available_width / total_width
            column_widths = [width * scale for width in column_widths]

        self._reset_to_margin(pdf)
        pdf.ln(1)
        self._draw_table_row(pdf, header, column_widths, header=True)
        for row in data_rows:
            self._draw_table_row(pdf, row, column_widths, header=False)

    def _draw_table_row(
        self,
        pdf: Any,
        cells: list[str],
        widths: list[float],
        *,
        header: bool,
    ) -> None:
        header_height = 7
        body_height = 6
        line_height = header_height if header else body_height
        x_start = pdf.l_margin
        y_start = pdf.get_y()
        max_height = line_height

        # Determine required row height
        for text, width in zip(cells, widths):
            pdf.set_font(
                "Helvetica",
                "B" if header else "",
                size=10 if header else 9,
            )
            lines = pdf.multi_cell(
                width - 3,
                line_height,
                self._safe_text(text),
                split_only=True,
            )
            height = line_height * max(1, len(lines))
            if height > max_height:
                max_height = height

        max_height += 2

        # Ensure room on page
        if y_start + max_height > pdf.h - pdf.b_margin:
            pdf.add_page()
            x_start = pdf.l_margin
            y_start = pdf.get_y()

        x_cursor = x_start
        for text, width in zip(cells, widths):
            pdf.rect(x_cursor, y_start, width, max_height)
            pdf.set_xy(x_cursor + 1.5, y_start + 1.5)
            pdf.set_font(
                "Helvetica",
                "B" if header else "",
                size=10 if header else 9,
            )
            pdf.multi_cell(
                width - 3,
                line_height,
                self._safe_text(text),
                border=0,
                new_x="LEFT",
                new_y="TOP",
            )
            x_cursor += width

        pdf.set_xy(pdf.l_margin, y_start + max_height)

    def _render_image(self, pdf: Any, block: _RenderBlock) -> None:
        if not block.asset_path:
            return
        image_path = self._resolve_asset(block.asset_path)
        caption = block.alt_text or "Diagram"
        if image_path is None or not image_path.exists():
            logger.warning(
                "Skipping image '%s'; file not found.",
                block.asset_path,
            )
            pdf.set_font("Helvetica", "I", size=10)
            pdf.multi_cell(
                0,
                5,
                self._safe_text(f"{caption}: {block.asset_path}"),
            )
            pdf.ln(2)
            pdf.set_font("Helvetica", size=11)
            return
        image_path = self._promote_bitmap(image_path)
        try:
            max_width = pdf.w - pdf.l_margin - pdf.r_margin
            pdf.ln(2)
            pdf.image(str(image_path), w=max_width)
            pdf.ln(2)
        except (OSError, RuntimeError, ValueError, KeyError) as exc:
            logger.warning(
                "Failed to embed image '%s': %s",
                image_path,
                exc,
            )
            pdf.set_font("Helvetica", "I", size=10)
            pdf.multi_cell(
                0,
                5,
                self._safe_text(f"{caption}: {block.asset_path}"),
            )
            pdf.ln(2)
            pdf.set_font("Helvetica", size=11)
            return

    def _clean_inline(self, text: str) -> str:
        cleaned = (
            text.replace("**", "")
            .replace("__", "")
            .replace("`", "")
            .replace("<br>", " ")
            .replace("\\|", "|")
        )
        cleaned = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1 (\2)", cleaned)
        return self._normalize_text(cleaned.strip())

    @staticmethod
    def _safe_text(text: str) -> str:
        text = SpecificationPDFExporter._normalize_text(text)
        try:
            text.encode("latin-1")
        except UnicodeEncodeError:
            return text.encode("latin-1", "replace").decode("latin-1")
        return text

    @staticmethod
    def _normalize_text(text: str) -> str:
        return text.translate(SpecificationPDFExporter._UNICODE_TRANSLATION)

    def _resolve_asset(self, relative_path: str) -> Optional[Path]:
        candidate = (self._asset_root / relative_path).resolve()
        try:
            candidate.relative_to(self._asset_root.resolve())
        except ValueError:
            logger.warning(
                "Ignoring image outside output directory: %s",
                relative_path,
            )
            return None
        return candidate

    @staticmethod
    def _reset_to_margin(pdf: Any) -> None:
        """Ensure the cursor is positioned at the left margin."""

        pdf.set_x(pdf.l_margin)

    def _promote_bitmap(self, image_path: Path) -> Path:
        """Prefer a bitmap version when rendering to PDF."""

        if image_path.suffix.lower() != ".svg":
            return image_path
        png_candidate = image_path.with_suffix(".png")
        if png_candidate.exists():
            return png_candidate
        dot_candidate = image_path.with_suffix(".dot")
        if not dot_candidate.exists():
            return image_path
        dot_executable = which("dot")
        if dot_executable is None:
            return image_path
        try:
            result = subprocess.run(  # noqa: S603
                [
                    dot_executable,
                    "-Tpng",
                    str(dot_candidate),
                    "-o",
                    str(png_candidate),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            return image_path
        if result.returncode != 0:
            logger.warning(
                "Graphviz conversion to PNG failed (exit %s): %s",
                result.returncode,
                result.stderr.strip(),
            )
            return image_path
        return png_candidate if png_candidate.exists() else image_path
