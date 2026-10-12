"""Lectura de EPUB, extracción de segmentos traducibles y reconstrucción del
libro traducido (o bilingüe) conservando estructura, estilos e imágenes."""

from __future__ import annotations

import html
import io
import posixpath
import re
import zipfile
from dataclasses import dataclass
from html.entities import name2codepoint
from pathlib import Path
from urllib.parse import unquote

from lxml import etree

from . import inline
from .inline import localname

NS = {
    "c": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
    "ncx": "http://www.daisy.org/z3986/2005/ncx/",
    "enc": "http://www.w3.org/2001/04/xmlenc#",
}
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
HTML_TYPES = {"application/xhtml+xml", "text/html"}
NCX_TYPE = "application/x-dtbncx+xml"

# Elementos de bloque: si un elemento contiene alguno, no es un párrafo "hoja".
BLOCK_LEVEL = {
    "address", "article", "aside", "blockquote", "body", "caption", "center",
    "dd", "details", "dialog", "div", "dl", "dt", "fieldset", "figcaption",
    "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header",
    "hgroup", "hr", "html", "legend", "li", "main", "nav", "ol", "p", "pre",
    "section", "summary", "table", "tbody", "td", "tfoot", "th", "thead",
    "tr", "ul",
}
# Elementos que pueden ser un segmento traducible.
SEGMENT_TAGS = {
    "p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "dt", "dd", "blockquote",
    "figcaption", "caption", "td", "th", "div", "section", "article", "aside",
    "header", "footer", "address", "summary", "legend", "figure", "center",
    "main", "hgroup", "details",
}
# Contenedores donde se envuelve el texto suelto que convive con bloques.
CONTAINER_TAGS = SEGMENT_TAGS | {"body", "nav"}
# Nunca se traducen ni se recorren.
SKIP_TAGS = {"script", "style", "pre", "svg", "math", "head", "noscript", "template", "code"}
# En modo bilingüe, la traducción va dentro del mismo elemento (no como hermano).
INSIDE_TAGS = {"td", "th", "li", "dt", "dd", "span", "caption", "figcaption", "summary", "legend"}

RUN_CLASS = "tdl-run"
TRAD_CLASS = "tdl-trad"
BILINGUAL_CSS = (
    ".tdl-trad{display:block;margin-top:.3em;padding-left:.5em;"
    "border-left:2px solid #999;}"
    "p.tdl-trad,h1.tdl-trad,h2.tdl-trad,h3.tdl-trad,h4.tdl-trad,"
    "h5.tdl-trad,h6.tdl-trad,blockquote.tdl-trad,div.tdl-trad{margin-top:.2em;}"
)


class EpubError(Exception):
    """Error al leer o escribir un EPUB (mensaje pensado para el usuario)."""


# Códigos ISO 639-2 frecuentes en metadatos de EPUB.
_LANG3 = {
    "eng": "en", "spa": "es", "fre": "fr", "fra": "fr", "ger": "de", "deu": "de", "ita": "it",
    "por": "pt", "kor": "ko", "jpn": "ja", "chi": "zh", "zho": "zh", "rus": "ru", "cat": "ca",
    "glg": "gl", "baq": "eu", "eus": "eu", "dut": "nl", "nld": "nl",
}


def normalize_lang(code: str) -> str:
    base = (code or "").strip().replace("_", "-").split("-")[0].lower()
    return _LANG3.get(base, base)


@dataclass
class Document:
    """Un archivo traducible del libro (capítulo, índice…)."""

    index: int
    path: str  # ruta dentro del ZIP
    media_type: str
    kind: str  # "xhtml" | "ncx"
    in_spine: bool
    linear: bool = True


@dataclass
class Segment:
    """Un párrafo (o título, celda, elemento de lista) a traducir."""

    n: int
    tag: str
    source: str  # texto marcado

    @property
    def plain(self) -> str:
        return inline.strip_marks(self.source)


# ---------------------------------------------------------------------------
# Utilidades de XML


_ENTITY_RE = re.compile(rb"&([A-Za-z][A-Za-z0-9]{1,31});")
_XML_ENTITIES = {b"amp", b"lt", b"gt", b"quot", b"apos"}


def _fix_entities(data: bytes) -> bytes:
    """Convierte entidades HTML con nombre (&nbsp;, &mdash;…) en numéricas,
    porque un parser XML sin DTD no las conoce."""

    def repl(m: re.Match) -> bytes:
        name = m.group(1)
        if name in _XML_ENTITIES:
            return m.group(0)
        cp = name2codepoint.get(name.decode("ascii"))
        return b"&#%d;" % cp if cp is not None else m.group(0)

    return _ENTITY_RE.sub(repl, data)


def parse_xml(data: bytes) -> etree._ElementTree:
    data = _fix_entities(data)
    parser = etree.XMLParser(resolve_entities=False, huge_tree=True, remove_blank_text=False)
    try:
        return etree.fromstring(data, parser).getroottree()
    except etree.XMLSyntaxError:
        lenient = etree.XMLParser(recover=True, resolve_entities=False, huge_tree=True)
        root = etree.fromstring(data, lenient)
        if root is None:
            raise
        return root.getroottree()


def serialize(tree: etree._ElementTree) -> bytes:
    return etree.tostring(tree, xml_declaration=True, encoding="utf-8")


def _contains_block(el) -> bool:
    return any(localname(d) in BLOCK_LEVEL for d in el.iterdescendants())


def _is_blockish(el) -> bool:
    return isinstance(el.tag, str) and (localname(el) in BLOCK_LEVEL or _contains_block(el))


def _append_text(parent, text: str) -> None:
    if len(parent):
        parent[-1].tail = (parent[-1].tail or "") + text
    else:
        parent.text = (parent.text or "") + text


def _same_ns(el, name: str) -> str:
    ns = etree.QName(el).namespace
    return f"{{{ns}}}{name}" if ns else name


def _group_has_letters(group) -> bool:
    for it in group:
        if isinstance(it, str):
            if inline.has_letters(it):
                return True
        elif isinstance(it.tag, str) and inline.has_letters("".join(it.itertext())):
            return True
    return False


def _wrap_runs(el) -> None:
    """Envuelve en <span class="tdl-run"> el texto suelto que convive con
    bloques dentro de un contenedor, p. ej. ``<li>Texto<ul>…</ul></li>``.
    Visualmente no cambia nada y así ese texto se puede traducir como un
    segmento más."""
    items: list = []
    if el.text:
        items.append(el.text)
    for child in el:
        items.append(child)
        if child.tail:
            items.append(child.tail)

    groups: list[tuple[bool, list]] = []
    for it in items:
        block = not isinstance(it, str) and _is_blockish(it)
        if block:
            groups.append((True, [it]))
        elif groups and not groups[-1][0]:
            groups[-1][1].append(it)
        else:
            groups.append((False, [it]))

    if not any(not block and _group_has_letters(g) for block, g in groups):
        return

    for child in list(el):
        child.tail = None
        el.remove(child)
    el.text = None
    for block, group in groups:
        if block:
            el.append(group[0])
            continue
        if _group_has_letters(group):
            span = el.makeelement(_same_ns(el, "span"), {"class": RUN_CLASS})
            for it in group:
                if isinstance(it, str):
                    _append_text(span, it)
                else:
                    span.append(it)
            el.append(span)
        else:
            for it in group:
                if isinstance(it, str):
                    _append_text(el, it)
                else:
                    el.append(it)


def _is_run(el) -> bool:
    return localname(el) == "span" and RUN_CLASS in (el.get("class") or "").split()


def find_segments_xhtml(root) -> list:
    """Devuelve, en orden de lectura, los elementos hoja con texto traducible.

    Modifica el árbol de forma inocua (envoltorio de texto suelto), por eso
    siempre hay que llamarla sobre un árbol recién leído: así el resultado es
    idéntico al extraer y al reconstruir."""
    bodies = root.xpath("//*[local-name()='body']")
    if not bodies:
        return []
    found: list = []

    def visit(el) -> None:
        name = localname(el)
        if name in SKIP_TAGS or el.get("translate") == "no":
            return
        if not _contains_block(el):
            if (name in SEGMENT_TAGS or _is_run(el)) and inline.has_letters(inline.element_text(el)):
                found.append(el)
            return
        if name in CONTAINER_TAGS:
            _wrap_runs(el)
        for child in el:
            if isinstance(child.tag, str):
                visit(child)

    visit(bodies[0])
    return found


def find_segments_ncx(root) -> list:
    return [
        t for t in root.iter(f"{{{NS['ncx']}}}text")
        if t.getparent() is not None
        and localname(t.getparent()) == "navlabel"
        and inline.has_letters(t.text or "")
    ]


# ---------------------------------------------------------------------------
# Libro


class EpubBook:
    """Un EPUB abierto en modo lectura."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        try:
            self._zip = zipfile.ZipFile(self.path)
        except (zipfile.BadZipFile, FileNotFoundError) as e:
            raise EpubError(f"No se pudo abrir el EPUB «{self.path.name}»: {e}") from e
        self._names = set(self._zip.namelist())
        try:
            container = etree.fromstring(self._zip.read("META-INF/container.xml"))
            rootfile = container.find(".//c:rootfile", NS)
            self.opf_path = rootfile.get("full-path")
            self.opf = parse_xml(self._zip.read(self.opf_path))
        except (KeyError, AttributeError, etree.XMLSyntaxError) as e:
            raise EpubError(f"«{self.path.name}» no parece un EPUB válido.") from e
        self.opf_dir = posixpath.dirname(self.opf_path)
        self._load_manifest()
        self._check_drm()

    # -- lectura de la estructura -------------------------------------------

    def _resolve(self, href: str) -> str:
        href = unquote(href.split("#", 1)[0])
        return posixpath.normpath(posixpath.join(self.opf_dir, href)) if self.opf_dir else posixpath.normpath(href)

    def _load_manifest(self) -> None:
        root = self.opf.getroot()
        self.manifest: dict[str, dict] = {}
        for item in root.iterfind(".//opf:manifest/opf:item", NS):
            self.manifest[item.get("id")] = {
                "path": self._resolve(item.get("href", "")),
                "media_type": item.get("media-type", ""),
                "properties": (item.get("properties") or "").split(),
            }
        spine = root.find(".//opf:spine", NS)
        docs: list[Document] = []
        seen: set[str] = set()
        if spine is not None:
            for ref in spine.iterfind("opf:itemref", NS):
                item = self.manifest.get(ref.get("idref"))
                if not item or item["media_type"] not in HTML_TYPES or item["path"] in seen:
                    continue
                if item["path"] not in self._names:
                    continue
                seen.add(item["path"])
                docs.append(Document(
                    index=len(docs), path=item["path"], media_type=item["media_type"],
                    kind="xhtml", in_spine=True, linear=ref.get("linear", "yes") != "no",
                ))
        # Índice EPUB 3 (nav) fuera del spine.
        for item in self.manifest.values():
            if "nav" in item["properties"] and item["path"] not in seen and item["path"] in self._names:
                seen.add(item["path"])
                docs.append(Document(index=len(docs), path=item["path"], media_type=item["media_type"],
                                     kind="xhtml", in_spine=False, linear=False))
        # Índice EPUB 2 (NCX).
        ncx_id = spine.get("toc") if spine is not None else None
        ncx = self.manifest.get(ncx_id) if ncx_id else None
        if ncx is None:
            ncx = next((i for i in self.manifest.values() if i["media_type"] == NCX_TYPE), None)
        if ncx and ncx["path"] in self._names and ncx["path"] not in seen:
            docs.append(Document(index=len(docs), path=ncx["path"], media_type=NCX_TYPE,
                                 kind="ncx", in_spine=False, linear=False))
        if not any(d.in_spine for d in docs):
            raise EpubError(f"«{self.path.name}» no tiene capítulos legibles.")
        self.documents = docs

    def _check_drm(self) -> None:
        if "META-INF/encryption.xml" not in self._names:
            return
        try:
            enc = etree.fromstring(self._zip.read("META-INF/encryption.xml"))
        except etree.XMLSyntaxError:
            return
        encrypted = {
            posixpath.normpath(unquote(ref.get("URI", "")))
            for ref in enc.iter(f"{{{NS['enc']}}}CipherReference")
        }
        if any(d.path in encrypted for d in self.documents):
            raise EpubError(
                f"«{self.path.name}» está protegido con DRM. La app solo puede abrir "
                "EPUB sin DRM (por ejemplo, los que compraste en tiendas sin DRM o los tuyos propios)."
            )

    # -- metadatos ----------------------------------------------------------

    def _dc(self, name: str) -> str:
        el = self.opf.getroot().find(f".//dc:{name}", NS)
        return inline.element_text(el) if el is not None else ""

    @property
    def title(self) -> str:
        return self._dc("title") or self.path.stem

    @property
    def author(self) -> str:
        return self._dc("creator").strip(" ;,.")

    @property
    def language(self) -> str:
        """Código de idioma de dos letras (``eng`` y ``en-US`` pasan a ``en``)."""
        return normalize_lang(self._dc("language"))

    def metadata(self) -> dict:
        """Metadatos del libro, para mostrarlos y editarlos."""
        root = self.opf.getroot()

        def texts(name: str) -> list[str]:
            out = []
            for el in root.iterfind(f".//dc:{name}", NS):
                text = " ".join(inline.element_text(el).split())
                if text:
                    out.append(text)
            return out

        series = number = ""
        for m in root.iterfind(".//opf:metadata/opf:meta", NS):
            if m.get("name") == "calibre:series":
                series = m.get("content", "")
            elif m.get("name") == "calibre:series_index":
                number = m.get("content", "")
            elif m.get("property") == "belongs-to-collection" and not series:
                series = (m.text or "").strip()
            elif m.get("property") == "group-position" and not number:
                number = (m.text or "").strip()
        isbn = ""
        for ident in texts("identifier"):
            digits = re.sub(r"(?i)^urn:isbn:|[-\s]", "", ident)
            if re.fullmatch(r"(97[89]\d{9}[\dXx]|\d{9}[\dXx])", digits):
                isbn = digits
                break
        description = " ".join(re.sub(r"<[^>]+>", " ", html.unescape((texts("description") or [""])[0])).split())
        return {"titulo": self.title, "autor": ", ".join(texts("creator")), "editorial": (texts("publisher") or [""])[0],
                "descripcion": description, "fecha": (texts("date") or [""])[0][:10], "temas": ", ".join(texts("subject")),
                "serie": series, "serie_numero": number.removesuffix(".0"), "isbn": isbn, "idioma": self.language}

    def toc_labels(self) -> dict[str, str]:
        """Ruta de documento -> título según el índice (NAV o NCX)."""
        labels: dict[str, str] = {}
        for doc in self.documents:
            if doc.kind == "ncx":
                root = parse_xml(self.read(doc.path)).getroot()
                for point in root.iter(f"{{{NS['ncx']}}}navPoint"):
                    text = point.find(f"{{{NS['ncx']}}}navLabel/{{{NS['ncx']}}}text")
                    content = point.find(f"{{{NS['ncx']}}}content")
                    if text is not None and content is not None and content.get("src"):
                        path = self._resolve_from(doc.path, content.get("src"))
                        labels.setdefault(path, inline.element_text(text))
            elif not doc.in_spine:
                root = parse_xml(self.read(doc.path)).getroot()
                for a in root.xpath("//*[local-name()='nav']//*[local-name()='a'][@href]"):
                    path = self._resolve_from(doc.path, a.get("href"))
                    labels.setdefault(path, inline.element_text(a))
        return {k: v for k, v in labels.items() if v}

    def _resolve_from(self, base_path: str, href: str) -> str:
        href = unquote(href.split("#", 1)[0])
        return posixpath.normpath(posixpath.join(posixpath.dirname(base_path), href))

    def read(self, path: str) -> bytes:
        return self._zip.read(path)

    def close(self) -> None:
        self._zip.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- segmentos ----------------------------------------------------------

    def _load(self, doc: Document):
        """Lee el documento y devuelve (árbol, elementos segmento)."""
        tree = parse_xml(self.read(doc.path))
        root = tree.getroot()
        segs = find_segments_ncx(root) if doc.kind == "ncx" else find_segments_xhtml(root)
        return tree, segs

    def segments(self, doc: Document) -> list[Segment]:
        _tree, elements = self._load(doc)
        out = []
        for n, el in enumerate(elements):
            if doc.kind == "ncx":
                text = html.escape(inline.WS_RE.sub(" ", el.text or "").strip(" "), quote=False)
            else:
                text = inline.to_marked(el).text
            out.append(Segment(n=n, tag=localname(el), source=text))
        return out

    def doc_title(self, doc: Document, segments: list[Segment] | None = None,
                  toc: dict[str, str] | None = None) -> str:
        """Título legible de un documento: su entrada del índice o su primer encabezado."""
        if doc.kind == "ncx":
            return "Índice (NCX)"
        if not doc.in_spine:
            return "Índice"
        label = (toc if toc is not None else self.toc_labels()).get(doc.path)
        if label:
            return label[:80]
        segments = segments if segments is not None else self.segments(doc)
        for s in segments:
            if s.tag in {"h1", "h2", "h3"}:
                return s.plain[:80]
        if segments:
            return segments[0].plain[:60]
        return posixpath.basename(doc.path)

    # -- escritura ----------------------------------------------------------

    def render_document(self, doc: Document, translations: list[str | None], *,
                        target_lang: str, bilingual: bool) -> tuple[bytes, list[str]]:
        """Devuelve el documento con las traducciones aplicadas y avisos."""
        tree, elements = self._load(doc)
        root = tree.getroot()
        warnings: list[str] = []
        changed = False
        for n, el in enumerate(elements):
            t = translations[n] if n < len(translations) else None
            if not t:
                continue
            changed = True
            if doc.kind == "ncx":
                el.text = html.unescape(inline.strip_marks(t))
                continue
            marked = inline.to_marked(el)
            fixed, warns = inline.repair(t, marked.slots)
            warnings.extend(f"{doc.path} §{n}: {w}" for w in warns)
            if not bilingual:
                inline.apply_marked(el, fixed, marked.slots)
                continue
            if localname(el) in INSIDE_TAGS:
                el.append(el.makeelement(_same_ns(el, "br"), {}))
                holder = el.makeelement(_same_ns(el, "span"), {"class": TRAD_CLASS})
                el.append(holder)
            else:
                attrib = {k: v for k, v in el.attrib.items() if k != "id"}
                attrib["class"] = ((attrib.get("class") or "") + " " + TRAD_CLASS).strip()
                holder = el.makeelement(el.tag, attrib)
                tail = el.tail
                el.addnext(holder)
                el.tail = "\n"
                holder.tail = tail
            holder.set("lang", target_lang)
            holder.set(XML_LANG, target_lang)
            inline.apply_marked(holder, fixed, marked.slots, strip_ids=True)

        if changed and doc.kind == "xhtml":
            if bilingual:
                heads = root.xpath("//*[local-name()='head']")
                if heads:
                    style = heads[0].makeelement(_same_ns(heads[0], "style"), {"type": "text/css"})
                    style.text = BILINGUAL_CSS
                    heads[0].append(style)
            else:
                _set_lang(root, target_lang)
        elif changed and doc.kind == "ncx" and not bilingual:
            root.set(XML_LANG, target_lang)
        return serialize(tree), warnings

    def cover_item(self) -> dict | None:
        """La imagen de portada declarada en el OPF (EPUB 3, EPUB 2 o por su nombre)."""
        images = {k: v for k, v in self.manifest.items()
                  if v["media_type"].startswith("image/") and v["path"] in self._names}
        for k, v in images.items():
            if "cover-image" in v["properties"]:
                return dict(v, id=k)
        meta = self.opf.getroot().find(".//opf:metadata/opf:meta[@name='cover']", NS)
        if meta is not None and meta.get("content") in images:
            return dict(images[meta.get("content")], id=meta.get("content"))
        for k, v in images.items():
            name = (k + " " + posixpath.basename(v["path"])).lower()
            if "cover" in name or "portada" in name:
                return dict(v, id=k)
        return None

    def render_opf(self, *, target_lang: str, bilingual: bool, title: str | None,
                   new_cover: dict | None = None, metadata: dict | None = None) -> bytes:
        tree = parse_xml(self.read(self.opf_path))
        root = tree.getroot()
        meta = root.find("opf:metadata", NS)
        if metadata and meta is not None:
            _apply_metadata(root, meta, metadata)
        if new_cover is not None:
            # Portada añadida: nuevo elemento del manifiesto marcado como portada (EPUB 3 y 2).
            manifest = root.find("opf:manifest", NS)
            for item in manifest.iterfind("opf:item", NS):
                props = [x for x in (item.get("properties") or "").split() if x != "cover-image"]
                if props:
                    item.set("properties", " ".join(props))
                elif item.get("properties") is not None:
                    del item.attrib["properties"]
            el = etree.SubElement(manifest, f"{{{NS['opf']}}}item")
            el.set("id", new_cover["id"])
            el.set("href", new_cover["href"])
            el.set("media-type", new_cover["media_type"])
            if root.get("version", "").startswith("3"):
                el.set("properties", "cover-image")
            if meta is not None:
                for old in meta.findall("opf:meta[@name='cover']", NS):
                    meta.remove(old)
                cov = etree.SubElement(meta, f"{{{NS['opf']}}}meta")
                cov.set("name", "cover")
                cov.set("content", new_cover["id"])
        langs = root.findall(".//dc:language", NS)
        if bilingual:
            if meta is not None and not any((l.text or "").strip() == target_lang for l in langs):
                el = etree.SubElement(meta, f"{{{NS['dc']}}}language")
                el.text = target_lang
        else:
            if langs:
                langs[0].text = target_lang
            elif meta is not None:
                etree.SubElement(meta, f"{{{NS['dc']}}}language").text = target_lang
            if root.get(XML_LANG):
                root.set(XML_LANG, target_lang)
        if title:
            t = root.find(".//dc:title", NS)
            if t is not None:
                t.text = title
        return serialize(tree)

    def write(self, out_path: str | Path, translations: dict[int, list[str | None]], *,
              target_lang: str, bilingual: bool = False, title: str | None = None, cover=None,
              metadata: dict | None = None) -> list[str]:
        """Escribe el EPUB traducido. ``translations`` va de índice de
        documento a la lista de traducciones (marcadas) por segmento; ``None``
        deja el segmento original. ``cover``, si se da, es una función
        ``(formato, imagen_original, proporción) -> bytes`` que crea la portada
        nueva. Devuelve avisos para revisión."""
        warnings: list[str] = []
        modified: dict[str, bytes] = {}
        added: dict[str, bytes] = {}
        new_cover = None
        if cover is not None:
            item = self.cover_item()
            original = self.read(item["path"]) if item else None
            aspect = 1.5
            if original:
                try:
                    from PIL import Image

                    with Image.open(io.BytesIO(original)) as im:
                        aspect = min(1.8, max(1.2, im.height / im.width))
                except Exception:
                    pass
            if item and item["media_type"] in ("image/jpeg", "image/png"):
                modified[item["path"]] = cover("PNG" if item["media_type"] == "image/png" else "JPEG", original, aspect)
            else:
                href = "jeronimo-portada.jpg"
                path = posixpath.join(self.opf_dir, href) if self.opf_dir else href
                added[path] = cover("JPEG", original, aspect)
                new_cover = {"id": "jeronimo-portada", "href": href, "media_type": "image/jpeg"}
        for doc in self.documents:
            tr = translations.get(doc.index)
            if not tr or not any(tr):
                continue
            data, warns = self.render_document(doc, tr, target_lang=target_lang, bilingual=bilingual)
            modified[doc.path] = data
            warnings.extend(warns)
        modified[self.opf_path] = self.render_opf(target_lang=target_lang, bilingual=bilingual, title=title,
                                                  new_cover=new_cover, metadata=metadata)

        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_path.with_suffix(out_path.suffix + ".tmp")
        with zipfile.ZipFile(tmp, "w") as zout:
            mimetype = self.read("mimetype") if "mimetype" in self._names else b"application/epub+zip"
            info = zipfile.ZipInfo("mimetype", date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            zout.writestr(info, mimetype.strip())
            for src in self._zip.infolist():
                if src.filename == "mimetype":
                    continue
                info = zipfile.ZipInfo(src.filename, date_time=src.date_time)
                info.external_attr = src.external_attr
                if src.is_dir():
                    zout.writestr(info, b"")
                    continue
                info.compress_type = zipfile.ZIP_DEFLATED
                zout.writestr(info, modified.get(src.filename) or self.read(src.filename))
            for name, data in added.items():
                info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                zout.writestr(info, data)
        tmp.replace(out_path)
        return warnings


def _names(value: str) -> list[str]:
    return [n.strip() for n in re.split(r"[;,]|\s+y\s+|\s+and\s+|\s+&\s+", value or "") if n.strip()]


def _apply_metadata(root, meta, m: dict) -> None:
    """Escribe en el OPF los metadatos editados (EPUB 3 con «refines», EPUB 2 con atributos)."""
    dc, opf = NS["dc"], NS["opf"]
    epub3 = root.get("version", "").startswith("3")

    def drop(el) -> None:
        if el.get("id"):
            for ref in meta.findall(f"opf:meta[@refines='#{el.get('id')}']", NS):
                meta.remove(ref)
        meta.remove(el)

    # En EPUB 2 los atributos van con el prefijo «opf:», como esperan los lectores.
    opf_ns = None if epub3 else {"opf": opf}

    def add(name: str, text: str, ident: str = "", role: str = "") -> None:
        el = etree.SubElement(meta, f"{{{dc}}}{name}", nsmap=opf_ns)
        el.text = text
        if ident:
            el.set("id", ident)
        if role:
            if epub3 and ident:
                ref = etree.SubElement(meta, f"{{{opf}}}meta", refines=f"#{ident}", property="role", scheme="marc:relators")
                ref.text = role
            elif not epub3:
                el.set(f"{{{opf}}}role", role)

    def roles(el) -> set[str]:
        out = {el.get(f"{{{opf}}}role", "")}
        if el.get("id"):
            out |= {(r.text or "").strip() for r in meta.findall(f"opf:meta[@refines='#{el.get('id')}']", NS)
                    if r.get("property") == "role"}
        return out

    def single(name: str, value: str) -> None:
        if not value:
            return
        found = meta.findall(f"dc:{name}", NS)
        for el in found[1:]:
            drop(el)
        if found:
            found[0].text = value
        else:
            add(name, value)

    if m.get("titulo"):
        found = meta.find("dc:title", NS)
        if found is not None:
            found.text = m["titulo"]
        else:
            add("title", m["titulo"])
    if m.get("autor"):
        for el in meta.findall("dc:creator", NS):
            drop(el)
        for k, name in enumerate(_names(m["autor"])):
            add("creator", name, f"autor{k + 1}", "aut")
    if m.get("traductor"):
        for el in meta.findall("dc:contributor", NS) + meta.findall("dc:creator", NS):
            if "trl" in roles(el):
                drop(el)
        for k, name in enumerate(_names(m["traductor"])):
            add("contributor", name, f"traduccion{k + 1}", "trl")
    single("publisher", m.get("editorial", ""))
    single("description", m.get("descripcion", ""))
    single("date", m.get("fecha", ""))
    if m.get("temas"):
        for el in meta.findall("dc:subject", NS):
            drop(el)
        for subject in _names(m["temas"]):
            add("subject", subject)
    if m.get("serie"):
        for el in meta.findall("opf:meta", NS):
            if el.get("name") in ("calibre:series", "calibre:series_index") or el.get("property") == "belongs-to-collection":
                drop(el)
        etree.SubElement(meta, f"{{{opf}}}meta", name="calibre:series", content=m["serie"])
        if m.get("serie_numero"):
            etree.SubElement(meta, f"{{{opf}}}meta", name="calibre:series_index", content=str(m["serie_numero"]))
        if epub3:
            col = etree.SubElement(meta, f"{{{opf}}}meta", id="serie", property="belongs-to-collection")
            col.text = m["serie"]
            etree.SubElement(meta, f"{{{opf}}}meta", refines="#serie", property="collection-type").text = "series"
            if m.get("serie_numero"):
                etree.SubElement(meta, f"{{{opf}}}meta", refines="#serie", property="group-position").text = str(m["serie_numero"])
    if m.get("isbn"):
        for el in meta.findall("dc:identifier[@id='isbn-traduccion']", NS):
            drop(el)
        el = etree.SubElement(meta, f"{{{dc}}}identifier", id="isbn-traduccion", nsmap=opf_ns)
        if epub3:
            el.text = f"urn:isbn:{m['isbn']}"
        else:
            el.text = m["isbn"]
            el.set(f"{{{opf}}}scheme", "ISBN")


def _set_lang(root, lang: str) -> None:
    root.set("lang", lang)
    root.set(XML_LANG, lang)
    for body in root.xpath("//*[local-name()='body']"):
        if body.get("lang"):
            body.set("lang", lang)
        if body.get(XML_LANG):
            body.set(XML_LANG, lang)
