from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape

from .cards import Sheet

MM_PER_INCH = 25.4
PX_PER_MM = 96 / MM_PER_INCH
MAX_DIGIT_PX = 7

STYLE_OF = {"": 1, "under": 1, "cap": 2, "title": 3, "void": 0, "head": 4, "note": 5}

STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="4">
<font><sz val="8"/><name val="Arial Narrow"/></font>
<font><sz val="8"/><name val="Arial Narrow"/></font>
<font><sz val="6"/><name val="Arial Narrow"/></font>
<font><b/><sz val="9"/><name val="Arial Narrow"/></font>
</fonts>
<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>
<borders count="2">
<border><left/><right/><top/><bottom/><diagonal/></border>
<border><left style="thin"><color rgb="FF000000"/></left><right style="thin"><color rgb="FF000000"/></right><top style="thin"><color rgb="FF000000"/></top><bottom style="thin"><color rgb="FF000000"/></bottom><diagonal/></border>
</borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="6">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="49" fontId="1" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1" applyAlignment="1" applyNumberFormat="1"><alignment vertical="center" indent="0"/></xf>
<xf numFmtId="49" fontId="2" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1" applyAlignment="1" applyNumberFormat="1"><alignment horizontal="center" vertical="center"/></xf>
<xf numFmtId="49" fontId="3" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1" applyAlignment="1" applyNumberFormat="1"><alignment vertical="center"/></xf>
<xf numFmtId="49" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1" applyNumberFormat="1"><alignment horizontal="right" vertical="center"/></xf>
<xf numFmtId="49" fontId="2" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1" applyAlignment="1" applyNumberFormat="1"><alignment wrapText="1" vertical="center"/></xf>
</cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""


def column_name(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        name = chr(65 + rest) + name
    return name


def column_width_chars(mm: float) -> float:
    px = mm * PX_PER_MM
    return max(0.1, round((px - 5) / MAX_DIGIT_PX, 4))


def _edges(sheet: Sheet) -> list[float]:
    edges = {0.0}
    for page in sheet.pages:
        for row in page:
            x = 0.0
            for width, *_ in row.cells:
                x = round(x + width, 3)
                edges.add(x)
    return sorted(edges)


def _cell(ref: str, style: int, text: str = "") -> str:
    if not text:
        return f'<c r="{ref}" s="{style}"/>'
    return f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{escape(text)}</t></is></c>'


def sheet_xml(sheet: Sheet) -> str:
    edges = _edges(sheet)
    position = {x: i for i, x in enumerate(edges)}
    last_col = len(edges) - 2
    cols = "".join(
        f'<col min="{i + 1}" max="{i + 1}" width="{column_width_chars(edges[i + 1] - edges[i])}" customWidth="1"/>'
        for i in range(len(edges) - 1))
    rows, merges, breaks = [], [], []
    r = 0
    for page_no, page in enumerate(sheet.pages):
        for row in page:
            r += 1
            x = 0.0
            cells = []
            for width, text, kind, *span in row.cells:
                start = position[round(x, 3)]
                x = round(x + width, 3)
                end = position[x] - 1
                down = r + (span[0] if span else 1) - 1
                style = STYLE_OF.get(kind, 1)
                cells.append(_cell(f"{column_name(start)}{r}", style, text))
                for c in range(start + 1, end + 1):
                    cells.append(_cell(f"{column_name(c)}{r}", style))
                if kind != "under" and (end > start or down > r):
                    merges.append(f"{column_name(start)}{r}:{column_name(end)}{down}")
            height_pt = round(row.height_mm * 72 / MM_PER_INCH, 2)
            rows.append(f'<row r="{r}" ht="{height_pt}" customHeight="1">{"".join(cells)}</row>')
        if page_no < len(sheet.pages) - 1:
            breaks.append(f'<brk id="{r}" max="16383" man="1"/>')
    merge_xml = f'<mergeCells count="{len(merges)}">' + "".join(f'<mergeCell ref="{m}"/>' for m in merges) + "</mergeCells>" if merges else ""
    break_xml = f'<rowBreaks count="{len(breaks)}" manualBreakCount="{len(breaks)}">{"".join(breaks)}</rowBreaks>' if breaks else ""
    margin = round(5.5 / MM_PER_INCH, 4)
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheetPr><pageSetUpPr fitToPage="1"/></sheetPr>'
            '<sheetViews><sheetView workbookViewId="0" showGridLines="0"/></sheetViews>'
            '<sheetFormatPr defaultRowHeight="12"/>'
            f"<cols>{cols}</cols><sheetData>{''.join(rows)}</sheetData>{merge_xml}"
            '<printOptions horizontalCentered="1"/>'
            f'<pageMargins left="{margin}" right="{margin}" top="{margin}" bottom="{margin}" header="0" footer="0"/>'
            f'<pageSetup paperSize="9" orientation="landscape" fitToWidth="1" fitToHeight="{len(sheet.pages)}"/>'
            f"{break_xml}"
            f'<ignoredErrors><ignoredError sqref="A1:{column_name(last_col)}{max(r, 1)}" numberStoredAsText="1"/></ignoredErrors>'
            "</worksheet>")


def workbook(sheets: list[Sheet]) -> bytes:
    names = "".join(f'<sheet name="{escape(s.title[:31])}" sheetId="{i + 1}" r:id="rId{i + 1}"/>' for i, s in enumerate(sheets))
    rels = "".join(
        f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i + 1}.xml"/>'
        for i in range(len(sheets)))
    rels += (f'<Relationship Id="rId{len(sheets) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
             'Target="styles.xml"/>')
    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for i in range(len(sheets)))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                   f"{overrides}</Types>")
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                   "</Relationships>")
        z.writestr("xl/workbook.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                   f"<sheets>{names}</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>')
        z.writestr("xl/styles.xml", STYLES)
        for i, s in enumerate(sheets):
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", sheet_xml(s))
    return buf.getvalue()
