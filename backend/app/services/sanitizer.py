"""Sanitización de HTML para correo electrónico.

- Elimina JavaScript, iframes, scripts, controladores de eventos, etc.
- Restringe esquemas de URL a http/https/mailto (protección XSS y SSRF).
- Se usa tanto para contenido del editor como para HTML importado.
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse

import nh3

# Etiquetas seguras y estándar en correo electrónico
EMAIL_TAGS = {
    "a", "abbr", "address", "b", "bdi", "blockquote", "br", "caption", "center",
    "cite", "code", "col", "colgroup", "dd", "del", "div", "dl", "dt", "em",
    "figcaption", "figure", "font", "h1", "h2", "h3", "h4", "h5", "h6", "hr",
    "i", "img", "ins", "kbd", "li", "mark", "ol", "p", "picture", "pre", "q",
    "s", "small", "span", "strong", "sub", "sup", "table", "tbody",
    "td", "tfoot", "th", "thead", "tr", "u", "ul",
}

EMAIL_ATTRIBUTES = {
    "*": {"class", "style", "title", "dir", "lang", "align", "valign", "width", "height"},
    "a": {"href", "target"},  # nh3 añade rel="noopener noreferrer" vía link_rel
    "img": {"src", "srcset", "alt", "width", "height", "loading"},
    "table": {"role", "cellpadding", "cellspacing", "border", "bgcolor", "summary"},
    "td": {"bgcolor", "colspan", "rowspan"},
    "th": {"bgcolor", "colspan", "rowspan", "scope"},
    "font": {"color", "face", "size"},
}

ALLOWED_SCHEMES = {"http", "https", "mailto"}

# Longitud máxima de HTML aceptado (protección ante cargas enormes)
MAX_HTML_LENGTH = 500_000


class SanitizationError(ValueError):
    pass


def validate_url(url: str, *, allow_mailto: bool = True) -> bool:
    """Valida que una URL sea segura (http/https[/mailto])."""
    if not url or not isinstance(url, str):
        return False
    url = url.strip()
    if len(url) > 2000:
        return False
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme == "mailto":
        return allow_mailto and "@" in parsed.path
    if scheme not in ("http", "https"):
        return False
    # Requiere host; bloquea metadatos internos (SSRF)
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    if host in ("localhost", "0.0.0.0") or host.endswith(".local"):
        return False
    return True


def sanitize_email_html(html: str) -> str:
    """Sanitiza HTML de correo: sin JS, sin iframes, esquemas restringidos."""
    if not html:
        return ""
    if len(html) > MAX_HTML_LENGTH:
        raise SanitizationError("El HTML excede el tamaño máximo permitido")
    return nh3.clean(
        html,
        tags=EMAIL_TAGS,
        attributes=EMAIL_ATTRIBUTES,
        url_schemes=ALLOWED_SCHEMES,
        link_rel="noopener noreferrer",
        strip_comments=False,
    )


def sanitize_editor_blocks(blocks: Optional[list]) -> list:
    """Sanitiza el contenido de los bloques del editor (defensa en profundidad)."""
    if not blocks:
        return []
    if len(blocks) > 200:
        raise SanitizationError("Demasiados bloques")
    # Claves que contienen HTML enriquecido: se sanitizan aquí y también al renderizar
    HTML_KEYS = {"content", "html", "footer_text", "header_text"}
    out = []
    for block in blocks:
        if not isinstance(block, dict) or "type" not in block:
            continue
        props = block.get("props") or {}
        clean_props = {}
        for key, value in props.items():
            if isinstance(value, str):
                if key in ("href", "src", "url"):
                    if value and not validate_url(value):
                        value = ""  # descarta URLs inseguras
                elif key in HTML_KEYS:
                    value = sanitize_email_html(value[:50_000])
                elif len(value) > 50_000:
                    value = value[:50_000]
            clean_props[key] = value
        out.append({"type": str(block["type"])[:40], "props": clean_props})
    return out
