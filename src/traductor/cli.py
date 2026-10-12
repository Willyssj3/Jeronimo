"""Línea de comandos. La app de escritorio usa este mismo motor."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import knowledge
from .epub import EpubError
from .pdf2epub import PdfError
from .project import Project
from .providers import KINDS, ProviderError, make_provider
from .translate import Translator


def _provider(project: Project):
    return make_provider(project.config["proveedor"])


def _progress_bar(label: str):
    def show(fraction: float) -> None:
        width = 30
        filled = int(width * fraction)
        sys.stderr.write(f"\r{label} [{'#' * filled}{'.' * (width - filled)}] {fraction:4.0%}")
        if fraction >= 1:
            sys.stderr.write("\n")
        sys.stderr.flush()
    return show


def cmd_nuevo(a) -> None:
    project = Project.create(a.carpeta, a.libro, target_lang=a.destino, variant=a.variante)
    chapters = [c for c in project.chapters() if c.total]
    words = sum(c.words for c in chapters)
    print(f"Proyecto creado en «{project.root}».")
    print(f"Libro: {project.config.get('titulo')} — {project.config.get('autor') or 'autor desconocido'}")
    print(f"Idioma: {project.source_lang} → {project.target_lang}. {len(chapters)} documentos, ~{words:,} palabras.")


def cmd_referencia(a) -> None:
    project = Project(a.carpeta)
    stats = project.add_reference(a.original, a.traduccion, name=a.nombre,
                                  progress=_progress_bar("Emparejando"))
    print(f"Pares guardados: {stats.pairs} (párrafos: {stats.src_paragraphs} originales, "
          f"{stats.tgt_paragraphs} traducidos).")
    print(f"Cobertura: {stats.coverage:.0%} · pares 1 a 1: {stats.one_to_one:.0%} · calidad: {stats.quality}.")


def cmd_proveedor(a) -> None:
    project = Project(a.carpeta)
    cfg = project.config["proveedor"]
    cfg["tipo"] = a.tipo
    if a.modelo is not None:
        cfg["modelo"] = a.modelo
    if a.url is not None:
        cfg["base_url"] = a.url or None
    if a.esfuerzo is not None:
        cfg["esfuerzo"] = a.esfuerzo
    project.save_config()
    print(f"Proveedor: {KINDS.get(a.tipo, a.tipo)} · modelo: {cfg.get('modelo') or '(por defecto)'}")


def cmd_modelos(a) -> None:
    project = Project(a.carpeta)
    for name in _provider(project).list_models():
        print(name)


def cmd_preparar(a) -> None:
    project = Project(a.carpeta)
    steps = tuple(a.solo) if a.solo else ("estilo", "glosario", "analisis", "terminos")
    knowledge.prepare(project, _provider(project), steps=steps, log=print)
    print("Listo. Revisa y edita si quieres:")
    for name in ("estilo.md", "glosario.json", "biblia.json"):
        path = project.knowledge_dir / name
        if path.exists():
            print(f"  {path}")


def cmd_traducir(a) -> None:
    project = Project(a.carpeta)
    tr = Translator(project, _provider(project), log=print)
    if a.capitulo is not None:
        targets = [a.capitulo]
    elif a.todo:
        targets = [c.index for c in project.chapters()
                   if c.total and c.done < c.total and c.status != "omitido"]
    else:
        nxt = tr.next_chapter()
        targets = [nxt] if nxt is not None else []
    if not targets:
        print("No quedan capítulos pendientes.")
        return
    for index in targets:
        report = tr.translate_chapter(index, progress=lambda d, t: _progress_bar("Traduciendo")(d / max(1, t)))
        print(f"Capítulo {index}: {report.translated} párrafos traducidos.")
        if report.missing:
            print(f"  Sin traducir (reintenta o hazlo a mano): {report.missing}")
        for w in report.warnings[:20]:
            print(f"  Aviso {w}")
        if project.config.get("revision") == "capitulo" and not a.todo:
            print("Revisa el capítulo y apruébalo con «traductor aprobar» antes de seguir.")


def cmd_aprobar(a) -> None:
    project = Project(a.carpeta)
    project.set_approved(a.capitulo, not a.deshacer)
    print("Capítulo aprobado: sus párrafos servirán de ejemplo para los siguientes."
          if not a.deshacer else "Aprobación retirada.")


def cmd_omitir(a) -> None:
    project = Project(a.carpeta)
    for index in a.capitulos:
        project.set_skipped(index, not a.deshacer)
    print("Hecho: esos documentos " + ("se traducirán." if a.deshacer else "no se traducirán (quedan en el idioma original)."))


def cmd_estado(a) -> None:
    project = Project(a.carpeta)
    cfg = project.config
    print(f"{cfg.get('nombre')} ({project.source_lang} → {project.target_lang}, variante {cfg.get('variante')})")
    prov = cfg["proveedor"]
    print(f"IA: {KINDS.get(prov.get('tipo'), prov.get('tipo'))} · modelo {prov.get('modelo') or '(por defecto)'}")
    refs = project.references()
    print(f"Referencias: {len(refs)}" + "".join(
        f"\n  - {r['nombre']}: {r['estadisticas']['pairs']} pares, calidad {r['estadisticas']['quality']}"
        for r in refs))
    print(f"Guía de estilo: {'sí' if project.style_guide() else 'no'} · glosario: {len(project.glossary())} términos")
    total = done = 0
    for c in project.chapters():
        if not c.total:
            continue
        total += c.total
        done += c.done
        print(f"  [{c.index:3d}] {c.status:<10} {c.done:4d}/{c.total:<4d} {c.title[:60]}")
    print(f"Progreso total: {done}/{total} párrafos ({done / max(1, total):.0%}).")
    for model, usage in cfg.get("uso", {}).items():
        from .providers.anthropic_provider import estimate_cost
        from .providers.base import Usage

        cost = estimate_cost(model, Usage(**usage))
        extra = f" · ~{cost:.2f} USD" if cost is not None else ""
        print(f"Uso de {model}: {usage.get('input_tokens', 0):,} tokens de entrada, "
              f"{usage.get('output_tokens', 0):,} de salida{extra}")


def cmd_exportar(a) -> None:
    project = Project(a.carpeta)
    out, warnings = project.export(bilingual=a.bilingue, out_path=a.salida)
    print(f"EPUB generado: {out}")
    if warnings:
        print(f"{len(warnings)} aviso(s) de formato; revisa esos párrafos:")
        for w in warnings[:20]:
            print(f"  {w}")


def cmd_pdf2epub(a) -> None:
    from .pdf2epub import convert

    out, report = convert(a.pdf, a.salida, title=a.titulo, author=a.autor, language=a.idioma,
                          progress=lambda f, msg=None: _progress_bar("Convirtiendo")(f))
    print(f"EPUB generado: {out}")
    print(f"«{report.title}» · {report.author or 'autor desconocido'} · idioma {report.language}")
    print(f"{report.pages} páginas · {report.chapters} capítulos · {report.notes} notas · "
          f"{report.removed_lines} encabezados/números de página quitados")
    if report.dropped_pages:
        print(f"Páginas omitidas (índice impreso): {report.dropped_pages}")
    for w in report.warnings:
        print(f"Aviso: {w}")


def cmd_prompt(a) -> None:
    project = Project(a.carpeta)
    tr = Translator(project, None)
    res = tr.manual_prompt(a.capitulo)
    if res is None:
        print("Ese capítulo ya está traducido.", file=sys.stderr)
        return
    text, ids = res
    if a.salida:
        Path(a.salida).write_text(text, encoding="utf-8")
        print(f"Prompt guardado en {a.salida} (párrafos {ids[0]}–{ids[-1]}).", file=sys.stderr)
    else:
        print(text)


def cmd_importar(a) -> None:
    project = Project(a.carpeta)
    tr = Translator(project, None)
    text = Path(a.respuesta).read_text(encoding="utf-8")
    saved, missing = tr.manual_import(a.capitulo, text)
    print(f"Guardados {saved} párrafos." + (f" Sin texto en la respuesta: {missing}" if missing else ""))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="traductor", description="Traductor de libros EPUB con IA.")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("nuevo", help="crea un proyecto a partir de un EPUB")
    s.add_argument("carpeta")
    s.add_argument("libro")
    s.add_argument("--destino", default="es", help="idioma de destino (es, pt, fr…)")
    s.add_argument("--variante", default="auto", help="auto (como la oficial), es-419, es-AR, es-ES…")
    s.set_defaults(func=cmd_nuevo)

    s = sub.add_parser("referencia", help="añade un libro del autor con su traducción oficial")
    s.add_argument("carpeta")
    s.add_argument("original")
    s.add_argument("traduccion")
    s.add_argument("--nombre")
    s.set_defaults(func=cmd_referencia)

    s = sub.add_parser("proveedor", help="elige la IA")
    s.add_argument("carpeta")
    s.add_argument("--tipo", required=True, choices=sorted(KINDS))
    s.add_argument("--modelo")
    s.add_argument("--url", help="dirección del servidor (solo si no es la habitual)")
    s.add_argument("--esfuerzo", choices=["low", "medium", "high", "xhigh", "max"], help="solo Claude")
    s.set_defaults(func=cmd_proveedor)

    s = sub.add_parser("modelos", help="lista los modelos disponibles en la IA elegida")
    s.add_argument("carpeta")
    s.set_defaults(func=cmd_modelos)

    s = sub.add_parser("preparar", help="guía de estilo, glosario y análisis del libro")
    s.add_argument("carpeta")
    s.add_argument("--solo", nargs="+", choices=["estilo", "glosario", "analisis", "terminos"])
    s.set_defaults(func=cmd_preparar)

    s = sub.add_parser("traducir", help="traduce el siguiente capítulo (o el indicado)")
    s.add_argument("carpeta")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--capitulo", type=int)
    g.add_argument("--todo", action="store_true", help="todos los capítulos pendientes, sin pausas")
    s.set_defaults(func=cmd_traducir)

    s = sub.add_parser("aprobar", help="marca un capítulo como revisado")
    s.add_argument("carpeta")
    s.add_argument("--capitulo", type=int, required=True)
    s.add_argument("--deshacer", action="store_true")
    s.set_defaults(func=cmd_aprobar)

    s = sub.add_parser("omitir", help="no traducir ciertos documentos (créditos, publicidad…)")
    s.add_argument("carpeta")
    s.add_argument("capitulos", type=int, nargs="+")
    s.add_argument("--deshacer", action="store_true")
    s.set_defaults(func=cmd_omitir)

    s = sub.add_parser("estado", help="progreso del proyecto")
    s.add_argument("carpeta")
    s.set_defaults(func=cmd_estado)

    s = sub.add_parser("exportar", help="genera el EPUB traducido")
    s.add_argument("carpeta")
    s.add_argument("--bilingue", action="store_true")
    s.add_argument("--salida")
    s.set_defaults(func=cmd_exportar)

    s = sub.add_parser("pdf2epub", help="convierte un PDF propio (con texto) en EPUB")
    s.add_argument("pdf")
    s.add_argument("--salida")
    s.add_argument("--titulo")
    s.add_argument("--autor")
    s.add_argument("--idioma")
    s.set_defaults(func=cmd_pdf2epub)

    s = sub.add_parser("prompt", help="modo manual: prompt del siguiente bloque para pegar en un chat")
    s.add_argument("carpeta")
    s.add_argument("--capitulo", type=int, required=True)
    s.add_argument("--salida")
    s.set_defaults(func=cmd_prompt)

    s = sub.add_parser("importar", help="modo manual: importa la respuesta del chat")
    s.add_argument("carpeta")
    s.add_argument("respuesta")
    s.add_argument("--capitulo", type=int, required=True)
    s.set_defaults(func=cmd_importar)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except (ProviderError, EpubError, PdfError, ValueError, FileNotFoundError, FileExistsError, KeyError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrumpido. El progreso guardado se conserva.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
