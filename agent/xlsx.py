"""A small .xlsx writer on the standard library (zipfile and XML, no package): sheets of rows with a styled header,
column widths, wrapped text, clickable links, row marks (picked, out), a frozen header and filters. Enough for the
run's account workbook (agent/report.py); it opens in Excel, Numbers and Google Sheets."""
import math
import re
import zipfile
from xml.sax.saxutils import escape, quoteattr

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "application/vnd.openxmlformats-officedocument.spreadsheetml"
BAD_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
MAX_CELL = 32767
LINE = 15           # points per line of 11pt text

STYLES = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="{MAIN}">
<fonts count="5">
<font><sz val="11"/><color rgb="FF1F2328"/><name val="Calibri"/><family val="2"/></font>
<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/><family val="2"/></font>
<font><u/><sz val="11"/><color rgb="FF0B57D0"/><name val="Calibri"/><family val="2"/></font>
<font><b/><sz val="14"/><color rgb="FF1F2328"/><name val="Calibri"/><family val="2"/></font>
<font><b/><sz val="11"/><color rgb="FF1F2328"/><name val="Calibri"/><family val="2"/></font>
</fonts>
<fills count="5">
<fill><patternFill patternType="none"/></fill>
<fill><patternFill patternType="gray125"/></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FF1F3A5F"/><bgColor indexed="64"/></patternFill></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFE3F2E6"/><bgColor indexed="64"/></patternFill></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFF2F3F5"/><bgColor indexed="64"/></patternFill></fill>
</fills>
<borders count="2">
<border/>
<border><bottom style="thin"><color rgb="FFD8DEE4"/></bottom></border>
</borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="11">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf>
<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
<xf numFmtId="0" fontId="2" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
<xf numFmtId="0" fontId="3" fillId="0" borderId="0" xfId="0" applyFont="1"/>
<xf numFmtId="0" fontId="4" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
<xf numFmtId="0" fontId="0" fillId="3" borderId="1" xfId="0" applyFill="1" applyBorder="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
<xf numFmtId="0" fontId="2" fillId="3" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
<xf numFmtId="0" fontId="0" fillId="4" borderId="1" xfId="0" applyFill="1" applyBorder="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
<xf numFmtId="0" fontId="2" fillId="4" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
</cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""

# (cell style, row mark) -> cellXfs index above. "note" is plain text that may run on into the empty cells beside it.
XF = {("header", None): 1, ("text", None): 2, ("link", None): 3, ("title", None): 4, ("bold", None): 5,
      ("text", "pick"): 6, ("link", "pick"): 7, ("text", "out"): 8, ("link", "out"): 9, ("note", None): 10}


class Cell:
    """A value with a style ("title", "bold", "header", "note") or a link. Plain values need no Cell."""
    __slots__ = ("value", "style", "url")

    def __init__(self, value, style=None, url=None):
        self.value, self.style, self.url = value, style, url


def link(text, url):
    """A clickable cell when url is a web address, else just the text."""
    ok = isinstance(url, str) and re.match(r"https?://", url)
    return Cell(text or url, url=url) if ok else Cell(text or "")


def col_name(i):
    """0 -> A, 25 -> Z, 26 -> AA."""
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _text(v):
    return BAD_XML.sub("", str(v))[:MAX_CELL]


def _lines(text, width):
    """Lines a wrapped cell needs, roughly: Excel never sizes a row itself, and Google Sheets does not either."""
    per = max(1, int(width * 1.15))
    return sum(max(1, math.ceil(len(part) / per)) for part in str(text).split("\n"))


def _cell_xml(ref, cell, mark):
    c = cell if isinstance(cell, Cell) else Cell(cell)
    kind = c.style or ("link" if c.url else "text")
    s = XF.get((kind, mark)) or XF.get((kind, None), XF[("text", None)])
    v = c.value
    if v is None or v == "":
        return f'<c r="{ref}" s="{s}"/>'
    if isinstance(v, bool):
        v = "yes" if v else "no"
    if isinstance(v, (int, float)):
        return f'<c r="{ref}" s="{s}"><v>{v}</v></c>'
    return f'<c r="{ref}" s="{s}" t="inlineStr"><is><t xml:space="preserve">{escape(_text(v))}</t></is></c>'


def _view(header, xs, first):
    """Frozen header row (and the first xs columns) on a table sheet; no gridlines on a notes sheet."""
    sel = ' tabSelected="1"' if first else ""
    if not header:
        return f'<sheetViews><sheetView workbookViewId="0" showGridLines="0"{sel}/></sheetViews>'
    top_left, pane = f"{col_name(xs)}2", "bottomRight" if xs else "bottomLeft"
    split = f'xSplit="{xs}" ' if xs else ""
    return (f'<sheetViews><sheetView workbookViewId="0"{sel}><pane {split}ySplit="1" topLeftCell="{top_left}" '
            f'activePane="{pane}" state="frozen"/><selection pane="{pane}" activeCell="{top_left}" sqref="{top_left}"/>'
            "</sheetView></sheetViews>")


def _sheet_xml(sheet, first):
    """-> (worksheet xml, its relationships xml or None, the filter range or None)"""
    widths = sheet["widths"]
    header = sheet.get("header")
    rows = ([[Cell(h, "header") for h in header]] if header else []) + list(sheet["rows"])
    marks = sheet.get("marks") or {}
    ncols = max([len(widths)] + [len(r) for r in rows])
    links, body = [], []
    for r, row in enumerate(rows, 1):
        mark = marks.get(r - 2 if header else r - 1)
        cells, lines, title = [], 1, False
        for i in range(ncols if mark else len(row)):   # a marked row is shaded across every column
            cell = row[i] if i < len(row) else ""
            c = cell if isinstance(cell, Cell) else Cell(cell)
            ref = f"{col_name(i)}{r}"
            cells.append(_cell_xml(ref, c, mark))
            if c.url:
                links.append((ref, c.url))
            if c.style == "title":
                title = True
            elif c.style != "note" and c.value not in (None, ""):
                lines = max(lines, _lines(c.value, widths[i] if i < len(widths) else 10))
        ht = 22 if title else min(409, LINE * lines + 4)
        body.append(f'<row r="{r}" ht="{ht}" customHeight="1">{"".join(cells)}</row>')

    last_col, last_row = col_name(ncols - 1), max(1, len(rows))
    cols = "".join(f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths))
    xml = [f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<worksheet xmlns="{MAIN}" xmlns:r="{REL}">',
           f'<dimension ref="A1:{last_col}{last_row}"/>', _view(header, sheet.get("freeze_cols", 0), first),
           f"<cols>{cols}</cols>" if cols else "", f'<sheetData>{"".join(body)}</sheetData>']
    filt = header and len(rows) > 1
    if filt:
        xml.append(f'<autoFilter ref="A1:{last_col}{last_row}"/>')
    if links:
        xml.append("<hyperlinks>" + "".join(f'<hyperlink ref="{ref}" r:id="rId{n}"/>'
                                             for n, (ref, _) in enumerate(links, 1)) + "</hyperlinks>")
    xml.append("</worksheet>")
    rels = None
    if links:
        rels = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="{PKG}">'
                + "".join(f'<Relationship Id="rId{n}" Type="{REL}/hyperlink" Target={quoteattr(_text(url))} '
                          'TargetMode="External"/>' for n, (_, url) in enumerate(links, 1))
                + "</Relationships>")
    return "".join(xml), rels, (f"$A$1:${last_col}${last_row}" if filt else None)


def sheet_name(name, taken):
    """Excel's rules: at most 31 characters, none of []:*?/\\, unique in the workbook."""
    base = re.sub(r"\s+", " ", re.sub(r"[\[\]:*?/\\]", " ", str(name))).strip()[:31] or "Sheet"
    out, n = base, 2
    while out.lower() in taken:
        out = f"{base[:28]} {n}"
        n += 1
    taken.add(out.lower())
    return out


def write(path, sheets):
    """sheets: [{"name", "widths": [column widths], "header": [titles] or None, "rows": [[value or Cell]],
    "marks": {data row index: "pick" | "out"}, "freeze_cols": n}]. A sheet with a header gets it styled, frozen and
    filterable; marked rows are shaded across every column. The first sheet opens first."""
    taken, names, parts, filters = set(), [], {}, []
    for n, sheet in enumerate(sheets, 1):
        name = sheet_name(sheet["name"], taken)
        names.append(name)
        xml, rels, ref = _sheet_xml(sheet, n == 1)
        parts[f"xl/worksheets/sheet{n}.xml"] = xml
        if rels:
            parts[f"xl/worksheets/_rels/sheet{n}.xml.rels"] = rels
        if ref:
            quoted = "'" + name.replace("'", "''") + "'"
            filters.append(f'<definedName name="_xlnm._FilterDatabase" localSheetId="{n - 1}" hidden="1">'
                           f"{escape(quoted)}!{ref}</definedName>")
    n = len(sheets)
    head = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    parts["[Content_Types].xml"] = (
        head + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        f'<Override PartName="/xl/workbook.xml" ContentType="{CT}.sheet.main+xml"/>'
        f'<Override PartName="/xl/styles.xml" ContentType="{CT}.styles+xml"/>'
        + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="{CT}.worksheet+xml"/>'
                  for i in range(1, n + 1)) + "</Types>")
    parts["_rels/.rels"] = (head + f'<Relationships xmlns="{PKG}"><Relationship Id="rId1" '
                            f'Type="{REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>')
    parts["xl/workbook.xml"] = (
        head + f'<workbook xmlns="{MAIN}" xmlns:r="{REL}"><bookViews><workbookView activeTab="0"/></bookViews><sheets>'
        + "".join(f'<sheet name={quoteattr(nm)} sheetId="{i}" r:id="rId{i}"/>' for i, nm in enumerate(names, 1))
        + "</sheets>" + (f"<definedNames>{''.join(filters)}</definedNames>" if filters else "") + "</workbook>")
    parts["xl/_rels/workbook.xml.rels"] = (
        head + f'<Relationships xmlns="{PKG}">'
        + "".join(f'<Relationship Id="rId{i}" Type="{REL}/worksheet" Target="worksheets/sheet{i}.xml"/>'
                  for i in range(1, n + 1))
        + f'<Relationship Id="rId{n + 1}" Type="{REL}/styles" Target="styles.xml"/></Relationships>')
    parts["xl/styles.xml"] = STYLES
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name in ["[Content_Types].xml", "_rels/.rels", "xl/workbook.xml", "xl/_rels/workbook.xml.rels",
                     "xl/styles.xml"] + sorted(k for k in parts if k.startswith("xl/worksheets/")):
            z.writestr(name, parts[name])
    return path
