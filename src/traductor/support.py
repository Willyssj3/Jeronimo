"""Apoyar el proyecto: los enlaces para donar, voluntario y sin presiones.

Los completa quien publica Jerónimo. Mientras un enlace esté vacío, la app no lo muestra (y si
están todos vacíos, no muestra nada). La app solo abre estos enlaces, nunca uno que llegue de
afuera, y solo si apuntan a las páginas de cada servicio."""

from __future__ import annotations

from .i18n import N_, tr

LINKS: dict[str, dict] = {
    # GitHub Sponsors: pendiente de aprobación (20/10/2026); al aprobarse, poner
    # "https://github.com/sponsors/Willyssj3" y sumar «github: [Willyssj3]» a .github/FUNDING.yml.
    "github": {"nombre": "GitHub Sponsors", "detalle": N_("desde cualquier país"), "url": ""},
    "cafecito": {"nombre": "Cafecito", "detalle": N_("en pesos, con Mercado Pago"), "url": "https://cafecito.app/willyssj3"},
    "paypal": {"nombre": "PayPal", "detalle": N_("con tarjeta o cuenta de PayPal"), "url": "https://paypal.me/Willyssj3"},
}

ALLOWED = {
    "github": ("https://github.com/sponsors/",),
    "cafecito": ("https://cafecito.app/",),
    "paypal": ("https://paypal.me/", "https://www.paypal.me/", "https://www.paypal.com/"),
}


def url(kind: str) -> str:
    """El enlace de un servicio, comprobado; vacío si no está configurado."""
    link = (LINKS.get(kind) or {}).get("url", "").strip()
    if link and not link.startswith(ALLOWED.get(kind, ())):
        raise ValueError(tr("Ese enlace de apoyo no es válido."))
    return link


def links() -> list[dict]:
    """Las formas de apoyar que están configuradas, para mostrar en la app."""
    return [{"id": kind, "nombre": info["nombre"], "detalle": tr(info["detalle"])}
            for kind, info in LINKS.items() if url(kind)]
