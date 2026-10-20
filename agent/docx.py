"""Markdown to a Word document (.docx) on the standard library (zipfile and XML, no package), for the run's emails
and report. Headings (#, ##, ###), paragraphs, bullet lists, pipe tables, **bold**, `code`, [links](https://...) and
Kevin's [bracketed notes] in grey italics. That is all emails.md and report.md use."""
import re
import zipfile
from xml.sax.saxutils import escape, quoteattr

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "application/vnd.openxmlformats-officedocument.wordprocessingml"
BAD_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
TEXT_WIDTH = 10080      # twips between the margins of a Letter page with 0.75 inch margins
INLINE = re.compile(r"\*\*(?P<b>.+?)\*\*|`(?P<code>[^`]+)`|\[(?P<lt>[^\]]+)\]\((?P<url>https?://[^)\s]+)\)")

STYLES = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="{W}">
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Calibri" w:cs="Calibri"/>
<w:color w:val="1F2328"/><w:sz w:val="22"/><w:szCs w:val="22"/><w:lang w:val="en-US"/></w:rPr></w:rPrDefault>
<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="276" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>
<w:pPr><w:keepNext/><w:spacing w:before="120" w:after="200"/><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:color w:val="15233D"/><w:sz w:val="34"/><w:szCs w:val="34"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>
<w:pPr><w:keepNext/><w:spacing w:before="320" w:after="100"/><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:b/><w:color w:val="15233D"/><w:sz w:val="27"/><w:szCs w:val="27"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>
<w:pPr><w:keepNext/><w:spacing w:before="240" w:after="60"/><w:outlineLvl w:val="2"/></w:pPr><w:rPr><w:b/><w:color w:val="2C58A6"/><w:sz w:val="23"/><w:szCs w:val="23"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/><w:basedOn w:val="Normal"/><w:qFormat/>
<w:pPr><w:spacing w:after="60"/><w:ind w:left="720"/></w:pPr></w:style>
<w:style w:type="paragraph" w:styleId="TableText"><w:name w:val="Table Text"/><w:basedOn w:val="Normal"/>
<w:pPr><w:spacing w:after="0" w:line="240" w:lineRule="auto"/></w:pPr><w:rPr><w:sz w:val="18"/><w:szCs w:val="18"/></w:rPr></w:style>
<w:style w:type="character" w:styleId="Hyperlink"><w:name w:val="Hyperlink"/><w:rPr><w:color w:val="2C58A6"/><w:u w:val="single"/></w:rPr></w:style>
</w:styles>"""

NUMBERING = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="{W}"><w:abstractNum w:abstractNumId="0"><w:multiLevelType w:val="hybridMultilevel"/>
<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="•"/><w:lvlJc w:val="left"/>
<w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/></w:rPr></w:lvl>
</w:abstractNum><w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num></w:numbering>"""


def _t(text):
    return f'<w:t xml:space="preserve">{escape(BAD_XML.sub("", text))}</w:t>'


class _Doc:
    def __init__(self):
        self.links, self.body = [], []

    def runs(self, text, rpr=""):
        """Inline markdown -> runs: bold, code and links, the rest as plain text."""
        out, pos = [], 0
        for m in INLINE.finditer(text):
            if m.start() > pos:
                out.append(f"<w:r>{rpr and f'<w:rPr>{rpr}</w:rPr>'}{_t(text[pos:m.start()])}</w:r>")
            if m.group("b") is not None:
                out.append(f"<w:r><w:rPr><w:b/>{rpr}</w:rPr>{_t(m.group('b'))}</w:r>")
            elif m.group("code") is not None:
                out.append(f'<w:r><w:rPr><w:rFonts w:ascii="Consolas" w:hAnsi="Consolas"/>{rpr}</w:rPr>{_t(m.group("code"))}</w:r>')
            else:
                self.links.append(m.group("url"))
                out.append(f'<w:hyperlink r:id="rIdL{len(self.links)}"><w:r><w:rPr><w:rStyle w:val="Hyperlink"/>{rpr}'
                           f"</w:rPr>{_t(m.group('lt'))}</w:r></w:hyperlink>")
            pos = m.end()
        if pos < len(text) or not out:
            out.append(f"<w:r>{rpr and f'<w:rPr>{rpr}</w:rPr>'}{_t(text[pos:])}</w:r>")
        return "".join(out)

    def para(self, text, style=None, extra="", rpr=""):
        ppr = (f'<w:pStyle w:val="{style}"/>' if style else "") + extra
        self.body.append(f"<w:p>{ppr and f'<w:pPr>{ppr}</w:pPr>'}{self.runs(text, rpr)}</w:p>")

    def table(self, rows):
        n = max(len(r) for r in rows)
        rows = [r + [""] * (n - len(r)) for r in rows]
        weight = [min(max(len(r[i]) for r in rows), 60) + 6 for i in range(n)]
        widths = [int(TEXT_WIDTH * w / sum(weight)) for w in weight]
        border = '<w:{0} w:val="single" w:sz="4" w:space="0" w:color="C9D2DD"/>'
        xml = [f'<w:tbl><w:tblPr><w:tblW w:w="{TEXT_WIDTH}" w:type="dxa"/><w:tblBorders>'
               + "".join(border.format(s) for s in ("top", "left", "bottom", "right", "insideH", "insideV"))
               + '</w:tblBorders><w:tblCellMar><w:top w:w="60" w:type="dxa"/><w:left w:w="90" w:type="dxa"/>'
               '<w:bottom w:w="60" w:type="dxa"/><w:right w:w="90" w:type="dxa"/></w:tblCellMar></w:tblPr>'
               "<w:tblGrid>" + "".join(f'<w:gridCol w:w="{w}"/>' for w in widths) + "</w:tblGrid>"]
        for i, row in enumerate(rows):
            head = i == 0
            xml.append("<w:tr>" + ("<w:trPr><w:tblHeader/><w:cantSplit/></w:trPr>" if head else "<w:trPr><w:cantSplit/></w:trPr>"))
            for w, cell in zip(widths, row):
                shade = '<w:shd w:val="clear" w:color="auto" w:fill="1F3A5F"/>' if head else ""
                rpr = '<w:b/><w:color w:val="FFFFFF"/>' if head else ""
                xml.append(f'<w:tc><w:tcPr><w:tcW w:w="{w}" w:type="dxa"/>{shade}</w:tcPr>'
                           f'<w:p><w:pPr><w:pStyle w:val="TableText"/></w:pPr>{self.runs(cell.strip(), rpr)}</w:p></w:tc>')
            xml.append("</w:tr>")
        xml.append("</w:tbl>")
        self.body.append("".join(xml))
        self.body.append('<w:p><w:pPr><w:spacing w:after="0"/></w:pPr></w:p>')   # Word needs a paragraph after a table


def _cells(line):
    return line.strip().strip("|").split("|")


def from_markdown(md, break_before=None):
    """-> the document's parts. break_before: a heading level (2 puts every ## on a new page)."""
    doc, lines, i, first = _Doc(), md.splitlines(), 0, True
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        h = re.match(r"(#{1,3})\s+(.*)", line)
        if h:
            level = len(h.group(1))
            brk = "<w:pageBreakBefore/>" if break_before == level and not first else ""
            doc.para(h.group(2).strip(), f"Heading{level}", brk)
            first = first and level == 1
            i += 1
            continue
        if line.lstrip().startswith("|"):
            rows = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                if not re.fullmatch(r"\s*\|?[\s:|-]+\|?\s*", lines[i]):   # the |---|---| line
                    rows.append(_cells(lines[i]))
                i += 1
            doc.table(rows)
            continue
        b = re.match(r"\s*[-*]\s+(.*)", line)
        if b:
            doc.para(b.group(1), "ListParagraph", '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>')
            i += 1
            continue
        text = [line.strip()]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(r"(#{1,3}\s|\s*[-*]\s|\s*\|)", lines[i]):
            text.append(lines[i].strip())
            i += 1
        text = " ".join(text)
        if text.startswith("[") and text.endswith("]") and not INLINE.match(text):
            doc.para(text, rpr='<w:i/><w:color w:val="5A687D"/>')     # a note for the reader, not sent
        else:
            doc.para(text)
    return doc


def write(path, md, title=None, break_before=None):
    doc = from_markdown(md, break_before)
    head = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    body = ("".join(doc.body) + '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1080" w:right="1080" '
            'w:bottom="1080" w:left="1080" w:header="720" w:footer="720" w:gutter="0"/></w:sectPr>')
    parts = {
        "[Content_Types].xml": head + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        f'<Override PartName="/word/document.xml" ContentType="{CT}.document.main+xml"/>'
        f'<Override PartName="/word/styles.xml" ContentType="{CT}.styles+xml"/>'
        f'<Override PartName="/word/numbering.xml" ContentType="{CT}.numbering+xml"/>'
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        "</Types>",
        "_rels/.rels": head + f'<Relationships xmlns="{PKG}"><Relationship Id="rId1" Type="{REL}/officeDocument" '
        'Target="word/document.xml"/><Relationship Id="rId2" '
        'Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" '
        'Target="docProps/core.xml"/></Relationships>',
        "docProps/core.xml": head + '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/'
        'metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f"<dc:title>{escape(title or '')}</dc:title></cp:coreProperties>",
        "word/_rels/document.xml.rels": head + f'<Relationships xmlns="{PKG}">'
        f'<Relationship Id="rIdS" Type="{REL}/styles" Target="styles.xml"/>'
        f'<Relationship Id="rIdN" Type="{REL}/numbering" Target="numbering.xml"/>'
        + "".join(f'<Relationship Id="rIdL{n}" Type="{REL}/hyperlink" Target={quoteattr(BAD_XML.sub("", url))} '
                  'TargetMode="External"/>' for n, url in enumerate(doc.links, 1)) + "</Relationships>",
        "word/document.xml": head + f'<w:document xmlns:w="{W}" xmlns:r="{REL}"><w:body>{body}</w:body></w:document>',
        "word/styles.xml": STYLES,
        "word/numbering.xml": NUMBERING,
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in parts.items():
            z.writestr(name, text)
    return path
