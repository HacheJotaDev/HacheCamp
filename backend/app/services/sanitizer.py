"""Sanitización de HTML para correo electrónico.

- Elimina JavaScript, iframes, scripts, controladores de eventos, etc.
- Restringe esquemas de URL a http/https/mailto (protección XSS y SSRF).
- CONSERVA los bloques <style> con CSS personalizado: se incluyen en las
  etiquetas permitidas y se excluyen de clean_content_tags (nh3 los trataría
  como contenido peligroso y destruiría los estilos).
- Canalización de conversión de CSS a estilos inline mediante Premailer
  (compatibilidad con clientes de correo que no leen <style>), conservando
  las media queries para diseño responsive.
- Se usa tanto para contenido del editor como para HTML importado.
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse

import nh3

# Etiquetas seguras y estándar en correo electrónico
# "style" está incluida a propósito: los bloques <style> con CSS son legítimos.
EMAIL_TAGS = {
    "a", "abbr", "address", "b", "bdi", "blockquote", "br", "caption", "center",
    "cite", "code", "col", "colgroup", "dd", "del", "div", "dl", "dt", "em",
    "figcaption", "figure", "font", "h1", "h2", "h3", "h4", "h5", "h6", "head",
    "hr", "html", "body", "i", "img", "ins", "kbd", "li", "mark", "meta", "ol",
    "p", "picture", "pre", "q", "s", "small", "span", "strong", "style", "sub",
    "sup", "table", "tbody", "td", "tfoot", "th", "thead", "title", "tr", "u", "ul",
}

# Contenidos que se eliminan completos junto con su etiqueta.
# IMPORTANTE: "style" NO puede estar aquí (ni por defecto) si se permite la
# etiqueta <style>: nh3/ammonia lanza un pánico si aparece en ambos conjuntos.
# Se mantiene "script" para que su contenido no se vuelva texto visible.
CLEAN_CONTENT_TAGS = {"script"}

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

# Patrones de CSS peligroso (defensa en profundidad; los clientes de correo
# modernos no los ejecutan, pero se eliminan por higiene)
_DANGEROUS_CSS = re.compile(
    r"expression\s*\(|javascript\s*:|-moz-binding|behavior\s*:@|@import",
    re.IGNORECASE,
)


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
    """Sanitiza HTML de correo: sin JS, sin iframes, esquemas restringidos.

    Conserva los bloques <style> (CSS personalizado) y los atributos style.
    """
    if not html:
        return ""
    if len(html) > MAX_HTML_LENGTH:
        raise SanitizationError("El HTML excede el tamaño máximo permitido")
    cleaned = nh3.clean(
        html,
        tags=EMAIL_TAGS,
        attributes=EMAIL_ATTRIBUTES,
        url_schemes=ALLOWED_SCHEMES,
        link_rel="noopener noreferrer",
        strip_comments=False,
        clean_content_tags=CLEAN_CONTENT_TAGS,
    )
    return _harden_css(cleaned)


def _harden_css(html: str) -> str:
    """Neutraliza construcciones CSS peligrosas en estilos inline y bloques."""
    if not html or "style" not in html.lower():
        return html

    def _limpiar(match: re.Match) -> str:
        value = match.group(0)
        return _DANGEROUS_CSS.sub("", value)

    # Bloques <style>...</style> y atributos style="..."
    html = re.sub(r"<style[^>]*>[\s\S]*?</style>", _limpiar, html, flags=re.IGNORECASE)
    html = re.sub(r'style=\"[^\"]*\"', _limpiar, html, flags=re.IGNORECASE)
    html = re.sub(r"style='[^']*'", _limpiar, html, flags=re.IGNORECASE)
    return html


def inline_email_css(html: str) -> str:
    """Convierte el CSS de los bloques <style> en estilos inline (Premailer).

    Mantiene los bloques <style> (media queries y reglas no inlineables) para
    los clientes que sí los soportan. Si Premailer falla con un HTML mal
    formado, se devuelve el HTML sanitizado sin inlinear (no bloquea el envío).
    """
    if not html or "<style" not in html.lower():
        return html
    try:
        import premailer

        result = premailer.Premailer(
            html,
            remove_classes=False,
            keep_style_tags=True,
            disable_validation=True,
        ).transform()
        return _harden_css(result)
    except Exception:  # pragma: no cover - HTML degenerado
        return html


def prepare_email_html(html: str) -> str:
    """Canalización completa: sanitización + conversión de CSS a inline.

    Orden: primero nh3 (elimina scripts/URLs peligrosas y conserva <style>),
    después Premailer (inlinea el CSS ya seguro). El resultado conserva el
    diseño en clientes de correo y mantiene la protección contra HTML activo.
    """
    sanitized = sanitize_email_html(html)
    return inline_email_css(sanitized)


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
