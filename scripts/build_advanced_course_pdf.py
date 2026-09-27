"""Build the advanced course PDF, checking every local target and PDF navigation.

Requires Pandoc, reportlab, pypdf and Windows Arial/Consolas. Builds offline.
Only pdf-manifest.json entries are included; author files are never collected.
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path, PurePosixPath
import re
import tempfile
from urllib.parse import unquote, urlsplit

from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import CondPageBreak, Flowable, PageBreak, Paragraph, Spacer
from reportlab.platypus.tableofcontents import TableOfContents

import build_course_pdf as shared

ROOT = shared.ROOT
ADVANCED = ROOT / "advanced-course"
MANIFEST = ADVANCED / "pdf-manifest.json"
DEFAULT_OUTPUT = ROOT / "output/pdf/advanced-ai-for-1c-course.pdf"


def load_manifest():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8-sig"))
    if set(manifest) != {"version", "title", "files", "externalDocuments"} or manifest["version"] != 1:
        raise ValueError("Unsupported advanced PDF manifest")
    files = manifest["files"]
    if not files or len(files) != len(set(files)):
        raise ValueError("Empty or duplicate PDF manifest entries")
    for name in files:
        path = PurePosixPath(name)
        target = (ADVANCED / name).resolve()
        if (path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name
                or shared.FORBIDDEN.search(name) or path.suffix != ".md"
                or not target.is_relative_to(ADVANCED.resolve())):
            raise ValueError(f"Forbidden advanced PDF entry: {name}")
        if not target.is_file():
            raise FileNotFoundError(target)
    public_base = {"course/" + name for name in shared.public_files()}
    for name, url in manifest["externalDocuments"].items():
        if name not in public_base or not url.startswith("https://github.com/fokusov/AI-Course/blob/main/" + name):
            raise ValueError(f"External document must be an explicit public course page: {name}")
        if url != "https://github.com/fokusov/AI-Course/blob/main/" + name:
            raise ValueError(f"Unexpected external document URL: {url}")
    return manifest


def resolve_link(source, target, allowed, headings, external_documents):
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc:
        return shared.local_target(source, target, allowed, headings)
    if parsed.query or parsed.path.startswith(("/", "\\")):
        raise ValueError(f"Unsupported local link in {source}: {target}")
    decoded = unquote(parsed.path).replace("\\", "/")
    candidate = (ROOT / source).parent / decoded if decoded else ROOT / source
    resolved_path = candidate.resolve()
    if not resolved_path.is_relative_to(ROOT.resolve()):
        raise ValueError(f"Link escapes repository in {source}: {target}")
    resolved = resolved_path.relative_to(ROOT.resolve()).as_posix()
    if resolved in external_documents:
        fragment = unquote(parsed.fragment)
        if fragment:
            external_headings = {n["c"][1][0] for n in shared.walk_nodes(shared.pandoc_ast(resolved, ROOT)) if n["t"] == "Header"}
            if fragment not in external_headings:
                raise ValueError(f"Unknown external-document heading: {target}")
        return external_documents[resolved] + ("#" + parsed.fragment if fragment else "")
    return shared.local_target(source, target, allowed, headings)


class MermaidDiagram(Flowable):
    """Render the course's linear TD flow plus labelled feedback edges as vectors.

    Intentionally reject other Mermaid syntax instead of silently losing branches.
    """

    def __init__(self, code, width):
        super().__init__()
        lines = [line.strip() for line in code.splitlines() if line.strip()]
        if not lines or lines.pop(0) != "flowchart TD":
            raise ValueError("PDF supports only a linear Mermaid flowchart TD")
        token = r"([A-Za-z][A-Za-z0-9_]*)(?:\[([^\]]+)\])?"
        edge = re.compile(token + r"\s+(-->|-\.\s+(.+?)\s+\.->)\s+" + token)
        labels, main, feedback = {}, [], []
        for line in lines:
            match = edge.fullmatch(line)
            if not match:
                raise ValueError(f"Unsupported Mermaid edge: {line}")
            left, left_label, arrow, caption, right, right_label = match.groups()
            for name, label in ((left, left_label), (right, right_label)):
                if label is not None:
                    if name in labels and labels[name] != label:
                        raise ValueError(f"Conflicting Mermaid node: {name}")
                    labels[name] = label
            if arrow == "-->":
                if not main:
                    main.append(left)
                if main[-1] != left or right in main:
                    raise ValueError("Main Mermaid path must be linear and acyclic")
                main.append(right)
            else:
                feedback.append((left, caption, right))
        if not main or any(n not in labels for n in main + [n for a, _, b in feedback for n in (a, b)]):
            raise ValueError("Mermaid node has no label")
        self.labels, self.main, self.feedback = labels, main, feedback
        self.width = width
        self.node_style = ParagraphStyle("diagram", fontName="Course", fontSize=9, leading=11, alignment=1)
        self.nodes = [Paragraph(shared.escape(labels[n]), self.node_style) for n in main]
        self.notes = [Paragraph(shared.escape(f"{labels[a]} → {caption} → {labels[b]}"),
                                ParagraphStyle("feedback", parent=self.node_style, alignment=0,
                                               fontSize=8, leading=11)) for a, caption, b in feedback]
        self.heights = [max(24, p.wrap(width - 70, 1000)[1] + 10) for p in self.nodes]
        self.note_heights = [p.wrap(width - 20, 1000)[1] for p in self.notes]
        self.height = sum(self.heights) + 11 * (len(main) - 1) + sum(self.note_heights) + 12 * len(self.notes) + 12

    def draw(self):
        canvas = self.canv
        y = self.height
        for i, (paragraph, height) in enumerate(zip(self.nodes, self.heights)):
            y -= height
            canvas.setStrokeColor(colors.HexColor("#8bb1ca"))
            canvas.setFillColor(colors.HexColor("#eef5f9"))
            canvas.roundRect(25, y, self.width - 50, height, 4, stroke=1, fill=1)
            _, text_height = paragraph.wrap(self.width - 70, height)
            paragraph.drawOn(canvas, 35, y + (height - text_height) / 2)
            if i < len(self.nodes) - 1:
                middle = self.width / 2
                canvas.line(middle, y, middle, y - 9)
                canvas.line(middle, y - 9, middle - 3, y - 6)
                canvas.line(middle, y - 9, middle + 3, y - 6)
                y -= 11
        for paragraph, height in zip(self.notes, self.note_heights):
            y -= height + 12
            paragraph.drawOn(canvas, 10, y)


def expand_details(block):
    if block["t"] != "RawBlock":
        return block
    fmt, content = block["c"]
    if fmt == "html" and content.strip() == "</details>":
        return None
    match = re.fullmatch(r"\s*<details>\s*<summary>([^<]+)</summary>\s*", content)
    if fmt != "html" or not match:
        raise ValueError(f"Unsupported HTML block: {content}")
    return {"t": "Para", "c": [{"t": "Strong", "c": [{"t": "Str", "c": html.unescape(match[1])}]}]}


def validate_pdf(path, destinations, expected_links):
    reader = PdfReader(path)
    page_ids = {(p.indirect_reference.idnum, p.indirect_reference.generation): i for i, p in enumerate(reader.pages)}
    actual_internal, actual_external = [], []
    for page in reader.pages:
        for reference in page.get("/Annots", []):
            annotation = reference.get_object()
            if annotation.get("/Subtype") != "/Link":
                continue
            action = annotation.get("/A", {})
            if action.get("/S") == "/URI":
                uri = str(action["/URI"])
                if urlsplit(uri).scheme not in {"https", "http", "mailto"}:
                    raise ValueError(f"Unsafe PDF URI: {uri}")
                actual_external.append(uri)
                continue
            target = annotation.get("/Dest", action.get("/D"))
            if not isinstance(target, list) or len(target) < 4 or target[1] != "/XYZ":
                raise ValueError(f"Unresolved PDF destination: {target}")
            target_page = page_ids.get((target[0].idnum, target[0].generation))
            if target_page is None or not 0 <= float(target[3]) <= float(reader.pages[target_page].mediabox.height):
                raise ValueError(f"PDF destination outside page: {target}")
            actual_internal.append((target_page, round(float(target[3]), 3)))
    wanted_internal = {key[1:] for key in expected_links if key.startswith("#")}
    wanted_external = {key for key in expected_links if not key.startswith("#")}
    positions = {(page, round(y, 3)) for page, y in destinations.values()}
    for key in wanted_internal:
        page, y = destinations[key]
        if (page, round(y, 3)) not in actual_internal:
            raise ValueError(f"PDF is missing internal link target: {key}")
    if any(position not in positions for position in actual_internal):
        raise ValueError("PDF link points outside known headings")
    if set(actual_external) != wanted_external:
        raise ValueError("PDF URLs differ from source URLs")

    def outline_positions(items):
        for item in items:
            if isinstance(item, list):
                yield from outline_positions(item)
            else:
                yield (reader.get_destination_page_number(item), round(float(item.top), 3))

    bookmarks = list(outline_positions(reader.outline))
    if set(bookmarks) != positions:
        raise ValueError("PDF bookmarks do not match source headings")
    return {"pages": len(reader.pages), "internal_link_annotations": len(actual_internal),
            "external_link_annotations": len(actual_external), "unique_external_urls": len(wanted_external),
            "checked_internal_targets": len(wanted_internal), "bookmarks": len(bookmarks)}


def build(output):
    manifest = load_manifest()
    paths = ["advanced-course/" + name for name in manifest["files"]]
    allowed = set(paths)
    documents = {path: shared.pandoc_ast(path, ROOT) for path in paths}
    headings = {}
    for path, blocks in documents.items():
        ids = [n["c"][1][0] for n in shared.walk_nodes(blocks) if n["t"] == "Header"]
        if not blocks or blocks[0]["t"] != "Header" or blocks[0]["c"][0] != 1:
            raise ValueError(f"Document must begin with H1: {path}")
        if len(ids) != len(set(ids)):
            raise ValueError(f"Duplicate heading IDs in {path}")
        headings[path] = set(ids)
    expected_links = []
    for source, blocks in documents.items():
        for node in shared.walk_nodes(blocks):
            if node["t"] == "Link":
                original = node["c"][2][0]
                destination = resolve_link(source, original, allowed, headings, manifest["externalDocuments"])
                expected_links.append(destination)
                if not destination.startswith("#"):
                    node["c"][2][0] = destination
    shared.register_fonts()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, prefix=output.stem + "-", suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        doc = shared.CourseDoc(temporary, manifest["title"], with_toc=True)
        styles = shared.styles()
        # Preserve readable type while avoiding a few trailing lines on a new page.
        for name in ("body", "quote"):
            styles[name].spaceAfter = 5
            styles[name].allowWidows = 0
            styles[name].allowOrphans = 0
        styles["h2"].spaceBefore = 12
        styles["h2"].spaceAfter = 7
        toc = TableOfContents()
        toc.levelStyles = [ParagraphStyle("contents", parent=styles["body"], leading=13, spaceBefore=4)]
        story = [Paragraph(shared.escape(manifest["title"]), styles["h1"]),
                 Paragraph("Расширенный курс · три практических проекта", styles["h2"]),
                 Paragraph("Оглавление и закладки ведут к разделам PDF. Ссылки на вводный курс открывают его страницы в GitHub; остальные внешние ссылки требуют интернета. Подсказки раскрыты для чтения.", styles["body"]),
                 Spacer(1, 10), Paragraph("Оглавление", styles["h2"]), toc]
        diagrams = details = 0
        for index, (source, blocks) in enumerate(documents.items()):
            if index == 0 or re.fullmatch(r"advanced-course/lessons/[^/]+/lesson\.md", source):
                story.append(PageBreak())
            else:
                story.extend([CondPageBreak(260), Spacer(1, 18)])
            for block in blocks:
                if block["t"] == "CodeBlock" and "mermaid" in block["c"][0][1]:
                    story.extend([MermaidDiagram(block["c"][1], doc.width), Spacer(1, 10)])
                    diagrams += 1
                    continue
                if block["t"] == "RawBlock" and "<details>" in block["c"][1]:
                    details += 1
                expanded = expand_details(block)
                if expanded:
                    shared.add_blocks(story, [expanded], source, allowed, headings, styles, doc.width)
        doc.multiBuild(story)
        expected_links.extend("#" + shared.key_for(source) for source in paths)
        report = validate_pdf(temporary, doc.destinations, expected_links)
        report.update({"documents": len(paths), "source_links": len(expected_links) - len(paths),
                       "diagrams": diagrams, "expanded_hints": details, "files": paths})
        temporary.replace(output)
        return report
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, help="Optional JSON navigation-check report")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.suffix.lower() != ".pdf" or output.is_relative_to(ADVANCED.resolve()):
        parser.error("Use a .pdf output outside the course source directory")
    if args.report and (args.report.suffix.lower() != ".json" or args.report.resolve().is_relative_to(ADVANCED.resolve())):
        parser.error("Use a .json report outside the course source directory")
    report = build(output)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"PDF: {output}")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
