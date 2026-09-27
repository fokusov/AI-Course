"""Build a readable, linked PDF from course/publication-manifest.yaml.

Requires Pandoc and Python package reportlab. No site build or network access.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import subprocess
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    XPreformatted,
)

ROOT = Path(__file__).resolve().parents[1]
COURSE = ROOT / "course"
MANIFEST = COURSE / "publication-manifest.yaml"
DEFAULT_OUTPUT = ROOT / "output" / "pdf" / "ai-for-1c-course.pdf"
FORBIDDEN = re.compile(r"(^|/)(instructor\.md|docs/author)(/|$)", re.I)


def public_files() -> list[str]:
    lines = MANIFEST.read_text(encoding="utf-8").splitlines()
    try:
        start = lines.index("publicFiles:") + 1
    except ValueError as exc:
        raise ValueError("Manifest has no publicFiles section") from exc
    result = []
    for line in lines[start:]:
        match = re.fullmatch(r"  - ([A-Za-z0-9_./-]+)", line)
        if match:
            name = match.group(1)
            if name.startswith("/") or ".." in PurePosixPath(name).parts or FORBIDDEN.search(name):
                raise ValueError(f"Forbidden manifest entry: {name}")
            if not name.endswith(".md"):
                raise ValueError(f"PDF cannot include non-Markdown entry: {name}")
            if not (COURSE / name).is_file():
                raise FileNotFoundError(COURSE / name)
            result.append(name)
        elif line and not line.startswith(" ") and not line.startswith("#"):
            break
        elif line.strip() and not line.startswith("#"):
            raise ValueError(f"Unexpected manifest entry: {line}")
    if not result or len(result) != len(set(result)):
        raise ValueError("Manifest is empty or contains duplicate files")
    return result


def reading_order(paths: list[str]) -> list[str]:
    lessons = sorted(p for p in paths if re.fullmatch(r"lessons/\d\d-[^/]+/lesson\.md", p))
    preferred = ["index.md", *lessons]
    return [p for p in preferred if p in paths] + [p for p in paths if p not in preferred]


def pandoc_ast(source: str, root: Path = COURSE) -> list[dict]:
    result = subprocess.run(
        ["pandoc", "--from=gfm", "--to=json", str(root / source)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return json.loads(result.stdout)["blocks"]


def walk_nodes(value):
    if isinstance(value, dict):
        if "t" in value:
            yield value
        for child in value.values():
            yield from walk_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_nodes(child)


def key_for(path: str, fragment: str = "") -> str:
    return "d" + hashlib.sha1(f"{path}#{fragment}".encode("utf-8")).hexdigest()[:16]


def local_target(source: str, target: str, allowed: set[str], headings: dict[str, set[str]]) -> str:
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc:
        if parsed.scheme not in {"https", "http", "mailto"}:
            raise ValueError(f"Unsupported external link in {source}: {target}")
        return target
    if parsed.query:
        raise ValueError(f"Unsupported local link query in {source}: {target}")
    path_part = unquote(parsed.path).replace("\\", "/")
    if path_part.startswith("/"):
        raise ValueError(f"Absolute local link in {source}: {target}")
    parts = list(PurePosixPath(source).parent.parts) if path_part else list(PurePosixPath(source).parts)
    if not path_part:
        resolved = source
    else:
        for part in PurePosixPath(path_part).parts:
            if part == "..":
                if not parts:
                    raise ValueError(f"Link escapes course in {source}: {target}")
                parts.pop()
            elif part != ".":
                parts.append(part)
        resolved = "/".join(parts)
    if resolved not in allowed or FORBIDDEN.search(resolved):
        raise ValueError(f"Link is outside public manifest in {source}: {target}")
    fragment = unquote(parsed.fragment)
    if fragment and fragment not in headings[resolved]:
        raise ValueError(f"Unknown heading in {source}: {target}")
    return "#" + key_for(resolved, fragment)


def escape(value: str) -> str:
    return html.escape(value, quote=True)


def inline(nodes: list[dict], source: str, allowed: set[str], headings: dict[str, set[str]]) -> str:
    parts = []
    for node in nodes:
        kind, value = node["t"], node.get("c")
        if kind == "Str":
            parts.append(escape(value))
        elif kind in {"Space", "SoftBreak"}:
            parts.append(" ")
        elif kind == "LineBreak":
            parts.append("<br/>")
        elif kind == "Code":
            parts.append(f'<font name="CourseMono" color="#24445e">{escape(value[1])}</font>')
        elif kind in {"Strong", "Emph"}:
            tag = "b" if kind == "Strong" else "i"
            parts.append(f"<{tag}>{inline(value, source, allowed, headings)}</{tag}>")
        elif kind == "Link":
            label = inline(value[1], source, allowed, headings)
            destination = local_target(source, value[2][0], allowed, headings)
            parts.append(f'<link href="{escape(destination)}" color="#12629a"><u>{label}</u></link>')
        else:
            raise ValueError(f"Unsupported inline Markdown node {kind} in {source}")
    return "".join(parts)


def plain_text(nodes: list[dict]) -> str:
    return "".join(str(n.get("c", "")) if n["t"] == "Str" else " " for n in nodes).strip()


class Heading(Paragraph):
    def __init__(self, text: str, style: ParagraphStyle, key: str, level: int, outline: str):
        super().__init__(text, style)
        self.key, self.level, self.outline = key, level, outline
        self.file_key = None

    def draw(self):
        super().draw()
        self.destination_position = (self.canv.getPageNumber() - 1,
                                     self.canv.absolutePosition(0, self.height)[1])
        self.canv.bookmarkHorizontal(self.key, 0, self.height)
        if self.file_key:
            self.canv.bookmarkHorizontal(self.file_key, 0, self.height)


class CourseDoc(BaseDocTemplate):
    def __init__(self, path: Path, title: str = "ИИ-разработка в 1С", with_toc: bool = False):
        super().__init__(str(path), pagesize=A4, leftMargin=49, rightMargin=49,
                         topMargin=51, bottomMargin=49, title=title)
        self.course_title = title
        self.with_toc = with_toc
        self.destinations = {}
        self.addPageTemplates(PageTemplate(id="course", frames=[Frame(
            self.leftMargin, self.bottomMargin, self.width, self.height,
            leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
        )], onPage=self.decorate))

    def decorate(self, canvas, doc):
        canvas.saveState()
        canvas.setFont("Course", 8)
        canvas.setFillColor(colors.HexColor("#607080"))
        canvas.drawString(self.leftMargin, 29, self.course_title)
        canvas.drawRightString(A4[0] - self.rightMargin, 29, str(doc.page))
        canvas.restoreState()

    def afterFlowable(self, flowable):
        if isinstance(flowable, Heading):
            position = flowable.destination_position
            self.destinations[flowable.key] = position
            if flowable.file_key:
                self.destinations[flowable.file_key] = position
                if self.with_toc:
                    self.notify('TOCEntry', (0, flowable.text, self.page, flowable.file_key))
            if flowable.outline:
                self.canv.addOutlineEntry(flowable.outline, flowable.key, level=flowable.level)


def register_fonts():
    fonts = Path("C:/Windows/Fonts")
    required = {"Course": "arial.ttf", "CourseBold": "arialbd.ttf",
                "CourseItalic": "ariali.ttf", "CourseMono": "consola.ttf"}
    for name, filename in required.items():
        path = fonts / filename
        if not path.is_file():
            raise FileNotFoundError(f"Required font is missing: {path}")
        pdfmetrics.registerFont(TTFont(name, str(path)))
    pdfmetrics.registerFontFamily("Course", normal="Course", bold="CourseBold",
                                  italic="CourseItalic", boldItalic="CourseBold")


def styles():
    body = ParagraphStyle("body", fontName="Course", fontSize=9.3, leading=13.7,
                          spaceAfter=7, textColor=colors.HexColor("#1d2b39"))
    return {
        "body": body,
        "h1": ParagraphStyle("h1", parent=body, fontName="CourseBold", fontSize=20,
                             leading=24, spaceBefore=8, spaceAfter=13,
                             textColor=colors.HexColor("#123c5b"), keepWithNext=True),
        "h2": ParagraphStyle("h2", parent=body, fontName="CourseBold", fontSize=14,
                             leading=18, spaceBefore=15, spaceAfter=8,
                             textColor=colors.HexColor("#16567b"), keepWithNext=True),
        "h3": ParagraphStyle("h3", parent=body, fontName="CourseBold", fontSize=11,
                             leading=15, spaceBefore=10, spaceAfter=5, keepWithNext=True),
        "quote": ParagraphStyle("quote", parent=body, leftIndent=13, rightIndent=8,
                               textColor=colors.HexColor("#3b5364"),
                               borderColor=colors.HexColor("#93b9cf"),
                               borderWidth=0, borderPadding=7),
        "code": ParagraphStyle("code", fontName="CourseMono", fontSize=7.4,
                              leading=10.3, spaceBefore=4, spaceAfter=9,
                              backColor=colors.HexColor("#f1f5f8"), borderPadding=8),
        "table": ParagraphStyle("table", parent=body, fontSize=8, leading=11, spaceAfter=0),
    }


def cell_blocks(blocks, source, allowed, headings, style):
    text = []
    for block in blocks:
        if block["t"] in {"Plain", "Para"}:
            text.append(inline(block["c"], source, allowed, headings))
        else:
            raise ValueError(f"Unsupported table cell node {block['t']} in {source}")
    return Paragraph("<br/>".join(text) or " ", style)


def add_blocks(out, blocks, source, allowed, headings, s, width, indent=0):
    for block in blocks:
        kind, value = block["t"], block.get("c")
        if kind == "Header":
            level, attr, nodes = value
            fragment = attr[0]
            heading = Heading(inline(nodes, source, allowed, headings),
                              s[f"h{min(level, 3)}"], key_for(source, fragment),
                              min(level - 1, 2), plain_text(nodes))
            if level == 1:
                heading.file_key = key_for(source)
            out.append(heading)
        elif kind in {"Para", "Plain"}:
            out.append(Paragraph(inline(value, source, allowed, headings), s["body"]))
        elif kind == "BlockQuote":
            for item in value:
                if item["t"] in {"Para", "Plain"}:
                    out.append(Paragraph(inline(item["c"], source, allowed, headings), s["quote"]))
                else:
                    add_blocks(out, [item], source, allowed, headings, s, width, indent)
        elif kind in {"BulletList", "OrderedList"}:
            items = value if kind == "BulletList" else value[1]
            start = 1 if kind == "BulletList" else value[0][0]
            for number, item in enumerate(items, start):
                prefix = "•" if kind == "BulletList" else f"{number}."
                if not item:
                    continue
                first, rest = item[0], item[1:]
                if first["t"] in {"Plain", "Para"}:
                    list_style = ParagraphStyle(f"list-{indent}-{number}", parent=s["body"],
                                                leftIndent=14 + indent, firstLineIndent=-14)
                    out.append(Paragraph(escape(prefix) + "  " + inline(first["c"], source, allowed, headings), list_style))
                else:
                    rest = item
                add_blocks(out, rest, source, allowed, headings, s, width, indent + 14)
        elif kind == "CodeBlock":
            code = value[1].expandtabs(4)
            wrapped = []
            for line in code.splitlines():
                if not line:
                    wrapped.append("")
                else:
                    while len(line) > 94:
                        wrapped.append(line[:94])
                        line = line[94:]
                    wrapped.append(line)
            out.append(XPreformatted(escape("\n".join(wrapped)), s["code"]))
        elif kind == "Table":
            head_rows = value[3][1]
            body_rows = [row for body in value[4] for row in body[3]]
            rows = []
            for row in [*head_rows, *body_rows]:
                cells = row[1]
                rows.append([cell_blocks(cell[4], source, allowed, headings, s["table"]) for cell in cells])
            if not rows:
                continue
            columns = len(rows[0])
            if any(len(row) != columns for row in rows):
                raise ValueError(f"Irregular table in {source}")
            table = Table(rows, colWidths=[width / columns] * columns, repeatRows=len(head_rows), hAlign="LEFT")
            table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, len(head_rows) - 1), colors.HexColor("#e6f0f5")),
                ("ROWBACKGROUNDS", (0, len(head_rows)), (-1, -1),
                 [colors.white, colors.HexColor("#f7fafc")]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#aac9da")),
            ]))
            out.extend([table, Spacer(1, 10)])
        else:
            raise ValueError(f"Unsupported Markdown block {kind} in {source}")


def build(output: Path) -> tuple[int, int]:
    paths = reading_order(public_files())
    allowed = set(paths)
    documents = {path: pandoc_ast(path) for path in paths}
    headings = {path: {node["c"][1][0] for node in walk_nodes(blocks)
                       if node["t"] == "Header"} for path, blocks in documents.items()}
    for path, blocks in documents.items():
        first = next((b for b in blocks if b["t"] == "Header"), None)
        if not first or first["c"][0] != 1:
            raise ValueError(f"Public document needs an H1: {path}")
    register_fonts()
    output.parent.mkdir(parents=True, exist_ok=True)
    doc = CourseDoc(output)
    s = styles()
    story = []
    for index, source in enumerate(paths):
        if index:
            story.append(PageBreak())
        blocks = documents[source]
        # Every file's H1 is also the destination for links without fragments.
        title = blocks[0]
        if title["t"] != "Header":
            raise ValueError(f"H1 must be first in {source}")
        add_blocks(story, blocks, source, allowed, headings, s, doc.width)
    doc.build(story)
    return len(paths), sum(1 for blocks in documents.values() for node in walk_nodes(blocks) if node["t"] == "Link")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    count, links = build(args.output.resolve())
    print(f"PDF: {args.output.resolve()} ({count} documents, {links} Markdown links)")


if __name__ == "__main__":
    main()
