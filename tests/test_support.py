"""Apoyar el proyecto: los enlaces se muestran solo si están cargados y se abren solo si son de cada servicio."""

import pytest

from traductor import support
from traductor.app import api as api_module
from traductor.app.api import Api


def set_links(monkeypatch, **urls):
    links = {kind: dict(info, url=urls.get(kind, "")) for kind, info in support.LINKS.items()}
    monkeypatch.setattr(support, "LINKS", links)


def test_nothing_is_shown_without_links(monkeypatch):
    set_links(monkeypatch)
    assert support.links() == []


def test_configured_links_in_order(monkeypatch):
    set_links(monkeypatch, github="https://github.com/sponsors/ejemplo", cafecito="https://cafecito.app/ejemplo")
    assert [(s["id"], s["nombre"]) for s in support.links()] == [("github", "GitHub Sponsors"), ("cafecito", "Cafecito")]
    assert support.links()[1]["detalle"] == "en pesos, con Mercado Pago"
    assert support.url("paypal") == ""


def test_only_each_services_own_pages(monkeypatch):
    set_links(monkeypatch, paypal="https://paypal.example.com/robo")
    with pytest.raises(ValueError):
        support.url("paypal")


def test_api_opens_only_configured_links(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    opened = []
    monkeypatch.setattr(api_module.webbrowser, "open", lambda link: opened.append(link) or True)
    set_links(monkeypatch, paypal="https://paypal.me/ejemplo")
    api = Api(mode="navegador")
    assert [s["id"] for s in api.app_info()["apoyo"]] == ["paypal"]
    assert api.open_support("paypal") and opened == ["https://paypal.me/ejemplo"]
    with pytest.raises(ValueError):
        api.open_support("cafecito")  # no está cargado
    with pytest.raises(ValueError):
        api.open_support("https://otra-pagina.example")
