"""La API que usa la interfaz, con la IA simulada."""

import time

import pytest
from helpers import make_epub
from test_pipeline import BODY, REF_SRC, REF_TGT, FakeAI

from traductor.app.api import Api, Bridge


def wait(api: Api, timeout: float = 20.0) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        st = api.task_status()
        if not st["activa"]:
            return st
        time.sleep(0.05)
    raise AssertionError("la tarea no terminó")


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    make_epub(tmp_path / "book.epub", [("The Harbor", BODY), ("Credits", "<p>Published by Nobody Press.</p>")])
    make_epub(tmp_path / "ref_en.epub", REF_SRC, title="Nets")
    make_epub(tmp_path / "ref_es.epub", REF_TGT, lang="es", title="Redes")
    a = Api(mode="navegador", provider_factory=lambda cfg: FakeAI())
    a.create_project(str(tmp_path / "book.epub"), str(tmp_path / "proyecto"))
    return a


def test_project_flow(api, tmp_path):
    info = api.app_info()
    assert info["recientes"][0]["carpeta"] == str(tmp_path / "proyecto")

    api.add_reference(str(tmp_path / "ref_en.epub"), str(tmp_path / "ref_es.epub"), "Nets")
    st = wait(api)
    assert st["error"] is None and st["resultado"]["pairs"] == 2

    api.set_provider({"tipo": "ollama", "modelo": "x"})
    api.prepare(["estilo", "glosario", "analisis", "terminos"])
    assert wait(api)["error"] is None
    k = api.knowledge()
    assert "raya" in k["estilo"] and any(g["termino"] == "Harrowgate" for g in k["glosario"])

    api.set_skipped(1, True)
    api.translate("siguiente")
    assert wait(api)["error"] is None
    ch = api.chapter(0)
    assert ch["hechos"] == ch["total"]
    assert ch["segmentos"][1]["marcas"] == {"1": "i"}
    assert ch["segmentos"][2]["marcas"] == {"1": "a"}

    # Edición manual: las marcas rotas se reparan.
    r = api.save_segment(0, 2, "—Los barcos llegan tarde —dijo el farero.")
    assert r["tgt"].endswith("<x1/>") and r["avisos"]

    api.set_approved(0, True)
    summary = api.project_summary()
    assert summary["progreso"]["caps_aprobados"] == 1
    assert summary["estimacion"]["coste_usd"] == 0.0  # modelo local

    out = api.export(False)
    assert out["ruta"].endswith(".es.epub")


def test_manual_mode(api):
    api.set_provider({"tipo": "manual"})
    assert api.project_summary()["bloque_manual"] == 15000
    prompt = api.manual_prompt(0)
    assert prompt["ids"] == [0, 1, 2, 3] and "<seg id=\"0\">" in prompt["texto"]
    assert prompt["bloques_restantes"] == 1
    r = api.manual_import(0, '<seg id="0">El puerto</seg>')
    assert r == {"guardados": 1, "faltan": [], "bloques_restantes": 1, "parrafos_pendientes": 3}
    api.set_settings({"bloque_manual": 6000})
    assert api.manual_status(0)["bloque"] == 6000
    with pytest.raises(ValueError):
        api.set_settings({"bloque_manual": 10})
    with pytest.raises(ValueError):
        api.manual_import(0, "respuesta sin marcas")
    with pytest.raises(ValueError, match="modo manual"):
        api.translate("siguiente")  # en modo manual no hay IA conectada


def test_bridge_reports_errors():
    bridge = Bridge(Api(mode="navegador"))
    assert bridge.call("project_summary")["ok"] is False
    assert bridge.call("_provider")["ok"] is False
    assert bridge.call("no_existe")["ok"] is False
    assert bridge.call("app_info")["ok"] is True


def test_chat_presets_and_manual_prep(api, tmp_path):
    api.set_provider({"tipo": "manual"})
    chats = {c["id"]: c for c in api.app_info()["chats"]}
    assert {"claude", "chatgpt", "gemini", "copilot", "deepseek", "lechat", "otro"} <= set(chats)
    summary = api.set_settings({"chat_manual": "copilot"})
    assert summary["chat_manual"] == "copilot" and summary["bloque_manual"] == chats["copilot"]["bloque"]
    assert api.manual_prep_status() is None
    st = api.manual_prep_start("analisis")
    assert st["paso"] == "analisis" and st["total"] == 1
    assert "[[CHAPTER 0" in api.manual_prep_prompt()["texto"]
    api.manual_prep_cancel()
    assert api.manual_prep_status() is None


def test_clear_and_restore_chapter_translation(api):
    api.translate("capitulo", 0)
    assert wait(api)["error"] is None
    assert api.chapter(0)["hechos"] == 4
    api.set_approved(0, True)

    r = api.clear_translation(0)
    assert r["borrados"] == 4
    ch = api.chapter(0)
    assert ch["hechos"] == 0 and not ch["aprobado"] and ch["copia_borrada"]
    assert all(s["tgt"] is None and not s["nota"] and not s["avisos"] for s in ch["segmentos"])
    assert api.clear_translation(0)["borrados"] == 0  # nada más que borrar

    # Si después se traduce un párrafo a mano, recuperar no lo pisa.
    api.save_segment(0, 3, "Mi versión.")
    r = api.restore_translation(0)
    assert r["recuperados"] == 3
    ch = api.chapter(0)
    assert ch["hechos"] == 4 and not ch["copia_borrada"]
    assert ch["segmentos"][3]["tgt"] == "Mi versión." and ch["segmentos"][1]["tgt"].startswith("ES MIRELA")
    with pytest.raises(ValueError):
        api.restore_translation(0)


def test_review_pass_through_the_api(api):
    api.translate("capitulo", 0)
    assert wait(api)["error"] is None
    assert api.review_status(0) == {"por_revisar": 4, "revisados": 0, "sugerencias": 0}
    api.review_start(0)
    st = wait(api)
    assert st["error"] is None and st["resultado"] == {"bloques": 1, "sugerencias": 1}
    segs = api.chapter(0)["segmentos"]
    assert segs[1]["sugerencia"]["texto"].startswith("ES MIRELA WALKED TO PUERTOARADO") and segs[1]["revisado_ia"]
    r = api.review_resolve(0, None, True)
    assert r["resueltas"] == 1 and r["sugerencias"] == 0
    assert "PUERTOARADO" in api.chapter(0)["segmentos"][1]["tgt"]
    assert api.review_reset(0)["por_revisar"] == 4
