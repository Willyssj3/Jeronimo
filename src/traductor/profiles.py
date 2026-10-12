"""Perfiles de autor: lo aprendido de un autor (referencias emparejadas, guía
de estilo, glosario y los capítulos ya aprobados) para reutilizarlo en su
próximo libro sin volver a prepararlo.

Se guardan en la carpeta de ajustes del usuario (``autores/``); solo contienen
los pares de párrafos ya emparejados, no los EPUB."""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from .inline import strip_marks
from .project import Project, read_json, slugify, write_json
from .providers import config_dir


def profiles_dir() -> Path:
    path = config_dir() / "autores"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _folder(profile_id: str) -> Path:
    folder = profiles_dir() / slugify(profile_id)
    if not (folder / "perfil.json").exists():
        raise ValueError("No encuentro ese perfil de autor.")
    return folder


def list_profiles() -> list[dict]:
    out = []
    for folder in sorted(profiles_dir().glob("*")):
        info = read_json(folder / "perfil.json")
        if info:
            out.append(dict(info, id=folder.name))
    return out


def _approved_pairs(project: Project) -> list[dict]:
    pairs = []
    for i in project.chapter_indices():
        data = project.load_chapter(i)
        if data.get("aprobado") and not data.get("omitir"):
            pairs += [{"src": strip_marks(s["src"]), "tgt": strip_marks(s["tgt"]), "cost": 0.0, "kind": "1-1"}
                      for s in data["segmentos"] if s.get("tgt") and s["src"].strip()]
    return pairs


def _merge_glossary(base: list[dict], extra: list[dict]) -> list[dict]:
    """Une glosarios; ante el mismo término manda ``base``."""
    seen = {e["termino"]: e for e in base if e.get("termino")}
    for e in extra:
        if e.get("termino") and e["termino"] not in seen:
            seen[e["termino"]] = e
    return sorted(seen.values(), key=lambda e: e["termino"].lower())


def save_profile(project: Project, name: str = "") -> dict:
    """Crea o actualiza el perfil del autor con lo de este proyecto."""
    cfg = project.config
    name = (name or "").strip() or (cfg.get("autor") or "").strip() or "Autor sin nombre"
    folder = profiles_dir() / slugify(name)
    refs = folder / "referencias"
    refs.mkdir(parents=True, exist_ok=True)

    for ref in sorted((project.root / "referencias").glob("*")):
        if (ref / "pares.jsonl").exists():
            dest = refs / ref.name
            shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(ref, dest)

    title = cfg.get("titulo") or cfg.get("nombre") or "libro"
    approved = _approved_pairs(project)
    if approved:
        dest = refs / slugify(f"{title}-aprobado")
        dest.mkdir(parents=True, exist_ok=True)
        write_json(dest / "info.json", {
            "nombre": f"{title} (traducción aprobada)", "titulo_original": title,
            "titulo_traduccion": cfg.get("titulo_traducido") or title, "autor": cfg.get("autor", ""),
            "idioma_traduccion": project.target_lang, "origen": "perfil",
            "estadisticas": {"pairs": len(approved), "src_paragraphs": len(approved),
                             "tgt_paragraphs": len(approved), "one_to_one": 1.0, "coverage": 1.0,
                             "quality": "buena"}})
        with open(dest / "pares.jsonl", "w", encoding="utf-8") as f:
            for p in approved:
                f.write(json.dumps(p, ensure_ascii=False) + "\n")

    style = project.style_guide()
    if style.strip():
        (folder / "estilo.md").write_text(style, encoding="utf-8")
    # Las propuestas de la IA sin revisar son de este libro: no se llevan al perfil.
    mine = [e for e in project.glossary() if e.get("origen") != "propuesta"]
    glossary = _merge_glossary(mine, read_json(folder / "glosario.json", []) or [])
    write_json(folder / "glosario.json", glossary)

    info = read_json(folder / "perfil.json", {}) or {}
    books = info.get("libros", [])
    if title not in books:
        books.append(title)
    info.update({
        "nombre": name, "idioma_origen": project.source_lang, "idioma_destino": project.target_lang,
        "variante": cfg.get("variante", "auto"), "libros": books,
        "referencias": sum(1 for r in refs.glob("*") if (r / "pares.jsonl").exists()),
        "glosario": len(glossary), "estilo": bool(style.strip()),
        "actualizado": datetime.now().isoformat(timespec="minutes"),
    })
    write_json(folder / "perfil.json", info)
    project.config["perfil_autor"] = name
    project.save_config()
    return dict(info, id=folder.name)


def apply_profile(project: Project, profile_id: str, *, replace_style: bool = False) -> dict:
    """Añade al proyecto las referencias, la guía y el glosario del perfil."""
    folder = _folder(profile_id)
    added = 0
    present = {r.name for r in (project.root / "referencias").glob("*")}
    for ref in sorted((folder / "referencias").glob("*")):
        if ref.name not in present and (ref / "pares.jsonl").exists():
            shutil.copytree(ref, project.root / "referencias" / ref.name)
            added += 1
    style_path = folder / "estilo.md"
    style_set = False
    if style_path.exists() and (replace_style or not project.style_guide().strip()):
        project.set_style_guide(style_path.read_text(encoding="utf-8"))
        style_set = True
    before = len(project.glossary())
    project.set_glossary(_merge_glossary(project.glossary(), read_json(folder / "glosario.json", []) or []))
    info = read_json(folder / "perfil.json", {}) or {}
    project.config["perfil_autor"] = info.get("nombre", profile_id)
    project.save_config()
    return {"referencias": added, "estilo": style_set, "terminos": len(project.glossary()) - before}


def delete_profile(profile_id: str) -> None:
    shutil.rmtree(_folder(profile_id))
