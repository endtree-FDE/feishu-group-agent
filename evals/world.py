# 回归测试用的虚构附件。不含真实聊天、客户名或密钥。
import os, struct, zipfile, zlib


def _xml(text):
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def write_docx(path, text):
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>%s</w:t></w:r></w:p></w:body></w:document>" % _xml(text)
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", xml)


def write_pptx(path, text):
    slide = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
        ' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        "<p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>%s</a:t></a:r></a:p>"
        "</p:txBody></p:sp></p:spTree></p:cSld></p:sld>" % _xml(text)
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("ppt/slides/slide1.xml", slide)


def write_xlsx(path, text):
    """标准库能写出的最小 xlsx。没装 openpyxl 时，读取端会说明缺库，这是预期。"""
    parts = {
        "[Content_Types].xml": """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>
</Types>""",
        "_rels/.rels": """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""",
        "xl/workbook.xml": """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>""",
        "xl/_rels/workbook.xml.rels": """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>
</Relationships>""",
        "xl/sharedStrings.xml": """<?xml version="1.0" encoding="UTF-8"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="1" uniqueCount="1"><si><t>%s</t></si></sst>""" % _xml(text),
        "xl/worksheets/sheet1.xml": """<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>
<row r="1"><c r="A1" t="s"><v>0</v></c></row></sheetData></worksheet>""",
    }
    with zipfile.ZipFile(path, "w") as zf:
        for name, body in parts.items():
            zf.writestr(name, body)


def write_pdf(path, text):
    """尽量用 pypdf 写一页可抽取的文字。没有 pypdf 时写一份最小 PDF，读取端会说明缺库。"""
    line = "".join(ch if 32 <= ord(ch) < 127 and ch not in "()\\" else "?" for ch in (text or "QINGSHI-TOKEN"))
    try:
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
        writer = PdfWriter()
        font = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        })
        font_ref = writer._add_object(font)
        writer.add_blank_page(width=400, height=200)
        page = writer.pages[-1]
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref}),
        })
        content = DecodedStreamObject()
        content.set_data(("BT /F1 12 Tf 36 100 Td (%s) Tj ET" % line).encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(content)
        writer.write(path)
        return
    except Exception:
        raw = ("BT /F1 12 Tf 36 100 Td (%s) Tj ET" % line).encode("ascii")
        objects = []
        objects.append(b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n")
        objects.append(b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n")
        objects.append(
            b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 200] "
            b"/Contents 4 0 R /Resources<< /Font<< /F1 5 0 R >> >> >>endobj\n"
        )
        objects.append(b"4 0 obj<< /Length %d >>stream\n" % len(raw) + raw + b"\nendstream\nendobj\n")
        objects.append(b"5 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj\n")
        out = bytearray(b"%PDF-1.1\n")
        offsets = [0]
        for obj in objects:
            offsets.append(len(out))
            out += obj
        xref = len(out)
        out += ("xref\n0 %d\n" % (len(offsets))).encode("ascii")
        out += b"0000000000 65535 f \n"
        for off in offsets[1:]:
            out += ("%010d 00000 n \n" % off).encode("ascii")
        out += ("trailer<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(offsets), xref)).encode("ascii")
        with open(path, "wb") as f:
            f.write(out)


def write_png(path):
    """1x1 的红色 PNG。图里没有文字，用来确认识图路径被走到。"""
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    raw = b"\x00\xff\x00\x00"
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    blob = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(blob)


def write_zip(path, inner_name, text):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(inner_name or "note.txt", text or "")


def materialize(spec, folder):
    """按用例描述写出一个文件，返回绝对路径。kind 只接受这几种虚构格式。"""
    os.makedirs(folder, exist_ok=True)
    kind = str(spec.get("kind") or "txt").lower()
    name = os.path.basename(str(spec.get("name") or ("file." + kind)))
    path = os.path.join(folder, name)
    text = str(spec.get("text") or "")
    if kind == "docx":
        write_docx(path, text)
    elif kind == "pptx":
        write_pptx(path, text)
    elif kind == "xlsx":
        write_xlsx(path, text)
    elif kind == "pdf":
        write_pdf(path, text)
    elif kind == "png":
        write_png(path)
    elif kind == "zip":
        write_zip(path, spec.get("inner") or "note.txt", text)
    else:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    return path
