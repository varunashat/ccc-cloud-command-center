"""
Reusable helpers for generating Scope-of-Work XML content that matches
the SSTech / Noventiq proposal template's existing styles exactly.

These were validated against the bundled template during skill
development (Tahoma body font, 0B408B heading blue, navy table
headers). Import this module's functions when assembling new SOW
content rather than hand-writing XML fragments from scratch.

Usage pattern:

    import sys
    sys.path.insert(0, "/path/to/sow-builder/scripts")
    from sow_xml_helpers import heading2, body, bullet, empty_p, phases_table

    parts = []
    parts.append(heading2("Objectives"))
    parts.append(body("Some paragraph text."))
    parts.append(bullet("A bullet point."))
    parts.append(phases_table(
        ["Phase", "Duration", "Key Activities"],
        [(2200, 1600, 5560)],  # widths, see note below
        [("Discovery", "Week 1", "Assessment and planning."), ...]
    ))
    new_content = "\n    ".join(parts)

Then locate the empty-paragraph zone between the "SCOPE OF WORK"
heading and the next Heading1 ("PAYMENT TERMS") in word/document.xml
and replace it with new_content, per SKILL.md Step 5.

IMPORTANT: All text passed to these functions is automatically
XML-escaped. Never pre-escape your input text, and never skip these
helpers in favor of raw f-strings for user-supplied or generated text
— unescaped '&', '<', '>' characters will break document validation.
"""

from xml.sax.saxutils import escape

TAH = 'w:ascii="Tahoma" w:hAnsi="Tahoma" w:cs="Tahoma"'

# A bullet numId that is defined in this template's numbering.xml but
# NOT referenced anywhere in the existing document body (verified safe
# to reuse without colliding with other lists in the doc). If you ever
# update the bundled template, re-verify this is still unused via:
#   grep -o 'w:numId w:val="[0-9]*"' word/document.xml | sort -u
DEFAULT_BULLET_NUMID = 9


def heading2(text):
    """A Heading2-styled subsection title in the template's blue."""
    text = escape(text)
    return f'''<w:p>
      <w:pPr>
        <w:pStyle w:val="Heading2"/>
        <w:rPr>
          <w:rFonts {TAH}/>
          <w:color w:val="0B408B"/>
        </w:rPr>
      </w:pPr>
      <w:r>
        <w:rPr>
          <w:rFonts {TAH}/>
          <w:color w:val="0B408B"/>
        </w:rPr>
        <w:t>{text}</w:t>
      </w:r>
    </w:p>'''


def body(text, bold=False):
    """A standard body paragraph, Tahoma 10pt (sz 20 = half-points)."""
    text = escape(text)
    b = '<w:b/>' if bold else ''
    return f'''<w:p>
      <w:pPr>
        <w:rPr>
          <w:rFonts {TAH}/>
          <w:sz w:val="20"/>
          <w:szCs w:val="20"/>
        </w:rPr>
      </w:pPr>
      <w:r>
        <w:rPr>
          <w:rFonts {TAH}/>
          {b}
          <w:sz w:val="20"/>
          <w:szCs w:val="20"/>
        </w:rPr>
        <w:t xml:space="preserve">{text}</w:t>
      </w:r>
    </w:p>'''


def bullet(text, numid=DEFAULT_BULLET_NUMID):
    """A single bulleted list item."""
    text = escape(text)
    return f'''<w:p>
      <w:pPr>
        <w:numPr>
          <w:ilvl w:val="0"/>
          <w:numId w:val="{numid}"/>
        </w:numPr>
        <w:rPr>
          <w:rFonts {TAH}/>
          <w:sz w:val="20"/>
          <w:szCs w:val="20"/>
        </w:rPr>
      </w:pPr>
      <w:r>
        <w:rPr>
          <w:rFonts {TAH}/>
          <w:sz w:val="20"/>
          <w:szCs w:val="20"/>
        </w:rPr>
        <w:t xml:space="preserve">{text}</w:t>
      </w:r>
    </w:p>'''


def empty_p():
    """A blank spacer paragraph between subsections."""
    return f'''<w:p>
      <w:pPr>
        <w:rPr>
          <w:rFonts {TAH}/>
          <w:sz w:val="20"/>
          <w:szCs w:val="20"/>
        </w:rPr>
      </w:pPr>
    </w:p>'''


def _table_header_cell(text, width):
    text = escape(text)
    return f'''<w:tc>
      <w:tcPr>
        <w:tcW w:w="{width}" w:type="dxa"/>
        <w:shd w:val="clear" w:color="auto" w:fill="0B408B"/>
        <w:vAlign w:val="center"/>
      </w:tcPr>
      <w:p>
        <w:pPr>
          <w:jc w:val="center"/>
          <w:rPr>
            <w:rFonts {TAH}/>
            <w:b/>
            <w:color w:val="FFFFFF"/>
            <w:sz w:val="18"/>
            <w:szCs w:val="18"/>
          </w:rPr>
        </w:pPr>
        <w:r>
          <w:rPr>
            <w:rFonts {TAH}/>
            <w:b/>
            <w:color w:val="FFFFFF"/>
            <w:sz w:val="18"/>
            <w:szCs w:val="18"/>
          </w:rPr>
          <w:t>{text}</w:t>
        </w:r>
      </w:p>
    </w:tc>'''


def _table_data_cell(text, width):
    text = escape(text)
    return f'''<w:tc>
      <w:tcPr>
        <w:tcW w:w="{width}" w:type="dxa"/>
        <w:vAlign w:val="center"/>
      </w:tcPr>
      <w:p>
        <w:pPr>
          <w:rPr>
            <w:rFonts {TAH}/>
            <w:sz w:val="20"/>
            <w:szCs w:val="20"/>
          </w:rPr>
        </w:pPr>
        <w:r>
          <w:rPr>
            <w:rFonts {TAH}/>
            <w:sz w:val="20"/>
            <w:szCs w:val="20"/>
          </w:rPr>
          <w:t xml:space="preserve">{text}</w:t>
        </w:r>
      </w:p>
    </w:tc>'''


def phases_table(headers, widths, rows):
    """
    A navy-header data table matching the Document Control table style.

    headers: list of column header strings, e.g. ["Phase", "Duration", "Key Activities"]
    widths: list of ints (DXA units) matching len(headers); must sum to 9360
            to match the page's content width.
    rows: list of tuples, each the same length as headers.
    """
    assert len(headers) == len(widths), "headers and widths must match in length"
    total = sum(widths)
    if total != 9360:
        raise ValueError(
            f"Column widths sum to {total}, expected 9360 (page content "
            "width). Adjust widths so they sum to 9360."
        )

    grid_cols = "".join(f'<w:gridCol w:w="{w}"/>' for w in widths)
    header_row = "<w:tr>" + "".join(
        _table_header_cell(h, w) for h, w in zip(headers, widths)
    ) + "</w:tr>"

    data_rows = ""
    for row in rows:
        assert len(row) == len(headers), f"Row {row} doesn't match header count"
        data_rows += "<w:tr>" + "".join(
            _table_data_cell(str(cell), w) for cell, w in zip(row, widths)
        ) + "</w:tr>"

    return f'''<w:tbl>
      <w:tblPr>
        <w:tblStyle w:val="TableGrid"/>
        <w:tblW w:w="9360" w:type="dxa"/>
        <w:tblLook w:val="04A0" w:firstRow="1" w:lastRow="0" w:firstColumn="1" w:lastColumn="0" w:noHBand="0" w:noVBand="1"/>
      </w:tblPr>
      <w:tblGrid>
        {grid_cols}
      </w:tblGrid>
      {header_row}
      {data_rows}
    </w:tbl>'''
