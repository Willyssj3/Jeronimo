"""Construcción de EPUB de prueba (con texto inventado para los tests)."""

from __future__ import annotations

import zipfile
from pathlib import Path

XHTML = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="{lang}" xml:lang="{lang}">
<head><title>{title}</title><link rel="stylesheet" type="text/css" href="style.css"/></head>
<body>
{body}
</body>
</html>
"""

NAV = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="{lang}" xml:lang="{lang}">
<head><title>Contents</title></head>
<body>
<nav epub:type="toc" id="toc"><h1>Contents</h1>
<ol>
{items}
</ol>
</nav>
</body>
</html>
"""

NCX = """<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1" xml:lang="{lang}">
<head><meta name="dtb:uid" content="test-book"/></head>
<docTitle><text>{title}</text></docTitle>
<navMap>
{points}
</navMap>
</ncx>
"""

OPF = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid" xml:lang="{lang}">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:identifier id="uid">test-book</dc:identifier>
<dc:title>{title}</dc:title>
<dc:creator>Test Author</dc:creator>
<dc:language>{lang}</dc:language>
</metadata>
<manifest>
<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
<item id="css" href="style.css" media-type="text/css"/>
<item id="img" href="images/pic.png" media-type="image/png"/>
{items}
</manifest>
<spine toc="ncx">
{refs}
</spine>
</package>
"""

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)


def make_epub(path: Path, chapters: list[tuple[str, str]], *, lang: str = "en",
              title: str = "A Test Book") -> Path:
    """Crea un EPUB 3 con capítulos ``(título, cuerpo_xhtml)``."""
    path = Path(path)
    items, refs, nav_items, points = [], [], [], []
    with zipfile.ZipFile(path, "w") as z:
        info = zipfile.ZipInfo("mimetype")
        info.compress_type = zipfile.ZIP_STORED
        z.writestr(info, "application/epub+zip")
        z.writestr("META-INF/container.xml", (
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
            '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
            "</rootfiles></container>"))
        for i, (ch_title, body) in enumerate(chapters, 1):
            name = f"ch{i:02d}.xhtml"
            z.writestr(f"OEBPS/text/{name}", XHTML.format(lang=lang, title=ch_title, body=body))
            items.append(f'<item id="c{i}" href="text/{name}" media-type="application/xhtml+xml"/>')
            refs.append(f'<itemref idref="c{i}"/>')
            nav_items.append(f'<li><a href="text/{name}">{ch_title}</a></li>')
            points.append(
                f'<navPoint id="p{i}" playOrder="{i}"><navLabel><text>{ch_title}</text></navLabel>'
                f'<content src="text/{name}"/></navPoint>')
        z.writestr("OEBPS/nav.xhtml", NAV.format(lang=lang, items="\n".join(nav_items)))
        z.writestr("OEBPS/toc.ncx", NCX.format(lang=lang, title=title, points="\n".join(points)))
        z.writestr("OEBPS/style.css", "p { text-indent: 1em; }")
        z.writestr("OEBPS/images/pic.png", PNG)
        z.writestr("OEBPS/content.opf", OPF.format(
            lang=lang, title=title, items="\n".join(items), refs="\n".join(refs)))
    return path
