"""Renderizador de correos: bloques del editor -> HTML, Jinja2 con escape,
generación de texto plano y sustitución de variables personalizadas.

Variables disponibles en plantillas:
  {{contact.first_name}} {{contact.last_name}} {{contact.email}}
  {{campaign.name}} {{campaign.subject}}
  {{unsubscribe_url}} {{web_view_url}}
Motor: Jinja2 SandboxedEnvironment con autoescape.
"""
from __future__ import annotations

import html as html_module
import re
from typing import Any, Dict, List, Optional

import html2text
from jinja2 import Undefined
from jinja2.sandbox import SandboxedEnvironment

from app.services.sanitizer import inline_email_css, sanitize_email_html, validate_url

# --------------------------------------------------------------------------- #
# Motor de plantillas
# --------------------------------------------------------------------------- #


class LenientUndefined(Undefined):
    """Variable ausente -> cadena vacía (evita fallos por campos opcionales)."""
    def __str__(self):
        return ""

    def __iter__(self):
        return iter(())

    def __bool__(self):
        return False


def _make_env() -> SandboxedEnvironment:
    env = SandboxedEnvironment(
        autoescape=True,
        undefined=LenientUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    # Sin filtros peligrosos ni acceso global
    env.globals = {}
    return env


_env = _make_env()

VAR_PATTERN = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_.]*)\s*\}\}")


def known_variables() -> List[Dict[str, str]]:
    """Catálogo de variables documentadas para la interfaz."""
    return [
        {"key": "{{contact.first_name}}", "desc": "Nombre del contacto"},
        {"key": "{{contact.last_name}}", "desc": "Apellido del contacto"},
        {"key": "{{contact.email}}", "desc": "Correo del contacto"},
        {"key": "{{campaign.name}}", "desc": "Nombre interno de la campaña"},
        {"key": "{{campaign.subject}}", "desc": "Asunto de la campaña"},
        {"key": "{{unsubscribe_url}}", "desc": "Enlace funcional de baja"},
        {"key": "{{web_view_url}}", "desc": "Vista en navegador"},
    ]


def render_template_string(source: str, context: Dict[str, Any]) -> str:
    """Renderiza una cadena con Jinja2 sandboxed y autoescape."""
    if not source:
        return ""
    try:
        template = _env.from_string(source)
        return template.render(**context)
    except Exception:
        # Ante error de sintaxis Jinja, se entrega el texto sin sustituir
        return source


def validate_template_source(source: str) -> Optional[str]:
    """Devuelve un mensaje de error si la sintaxis Jinja2 es inválida."""
    try:
        _env.parse(source)
        return None
    except Exception as exc:
        return str(exc)[:300]


# --------------------------------------------------------------------------- #
# Bloques del editor -> HTML
# --------------------------------------------------------------------------- #


def _esc(text: Optional[str]) -> str:
    return html_module.escape(text or "", quote=True)


_BLOCK_STYLES = {
    "h1": "font-size:28px;line-height:1.3;margin:0 0 12px;",
    "h2": "font-size:22px;line-height:1.3;margin:0 0 10px;",
    "h3": "font-size:18px;line-height:1.4;margin:0 0 8px;",
    "p": "margin:0 0 12px;line-height:1.6;",
}


def _render_inline_html(text: str) -> str:
    """Convierte el texto enriquecido del editor (HTML simple) a HTML seguro."""
    return sanitize_email_html(text or "")


def _block_to_html(block: Dict[str, Any], click_signer=None) -> str:
    t = block.get("type", "")
    p = block.get("props", {}) or {}

    def signed_link(url: str, text: str, style: str = "") -> str:
        if not url:
            return _esc(text)
        href = url
        if click_signer:
            href = click_signer(url)
        target = "_blank"
        return f'<a href="{_esc(href)}" target="{target}" rel="noopener noreferrer" style="{style}">{_render_inline_html(text)}</a>'

    if t == "encabezado":
        return (
            '<tr><td style="padding:24px 24px 8px;text-align:center;">'
            f'<span style="{_esc(p.get("style", ""))}">{_render_inline_html(p.get("text", ""))}</span>'
            "</td></tr>"
        )
    if t == "texto":
        return (
            '<tr><td style="padding:8px 24px;font-family:Arial,Helvetica,sans-serif;color:#374151;">'
            + _render_inline_html(p.get("content", "")) +
            "</td></tr>"
        )
    if t == "titulo":
        level = p.get("level", 2)
        level = level if level in (1, 2, 3) else 2
        style = _BLOCK_STYLES[f"h{level}"]
        return (
            '<tr><td style="padding:8px 24px;font-family:Arial,Helvetica,sans-serif;color:#111827;">'
            f'<h{level} style="{style}">{_render_inline_html(p.get("text", ""))}</h{level}>'
            "</td></tr>"
        )
    if t == "imagen":
        src = p.get("src", "")
        if not src or not validate_url(src):
            return ""
        alt = _esc(p.get("alt", ""))
        return (
            '<tr><td style="padding:8px 24px;text-align:center;">'
            f'<img src="{_esc(src)}" alt="{alt}" style="max-width:100%;height:auto;border-radius:8px;display:inline-block;" />'
            "</td></tr>"
        )
    if t == "boton":
        href = p.get("href", "")
        label = p.get("text", "Ver más") or "Ver más"
        bg = _esc(p.get("bg_color", "#10b981"))
        color = _esc(p.get("text_color", "#ffffff"))
        radius = _esc(p.get("radius", "8px"))
        align = _esc(p.get("align", "center"))
        inner = (
            f'<span style="display:inline-block;padding:13px 28px;background:{bg};color:{color};'
            f'border-radius:{radius};font-weight:bold;font-family:Arial,Helvetica,sans-serif;font-size:15px;">'
            f"{_render_inline_html(label)}</span>"
        )
        if href and validate_url(href):
            inner = signed_link(href, inner)
        return f'<tr><td style="padding:12px 24px;text-align:{align};">{inner}</td></tr>'
    if t == "separador":
        color = _esc(p.get("color", "#e5e7eb"))
        return f'<tr><td style="padding:12px 24px;"><hr style="border:none;border-top:1px solid {color};margin:0;" /></td></tr>'
    if t == "espaciador":
        h = int(p.get("height", 24) or 24)
        return f'<tr><td style="height:{h}px;line-height:{h}px;font-size:0;">&nbsp;</td></tr>'
    if t == "columnas":
        cols = p.get("columns", []) or []
        cells = []
        for col in cols[:3]:
            inner_rows = "".join(_block_to_html(b, click_signer) for b in (col.get("blocks", []) or []))
            if not inner_rows:
                inner_rows = "<tr><td></td></tr>"
            cells.append(f'<td style="vertical-align:top;width:{100 // max(len(cols[:3]), 1)}%;" data-col="1">{inner_rows}</td>')
        return f'<tr><td style="padding:8px 12px;"><table width="100%" cellpadding="0" cellspacing="0" border="0" role="presentation"><tr>{"".join(cells)}</tr></table></td></tr>'
    if t == "pie":
        text = p.get("text", "") or ""
        company = _esc(p.get("company", "")) if p.get("company") else ""
        return (
            '<tr><td style="padding:16px 24px;text-align:center;color:#6b7280;font-size:12px;'
            'font-family:Arial,Helvetica,sans-serif;">'
            f"{_render_inline_html(text)}<br/>"
            f'<a href="{{{{unsubscribe_url}}}}" style="color:#9ca3af;">Cancelar suscripción</a>'
            f"{'<br/>' + company if company else ''}"
            "</td></tr>"
        )
    return ""


def blocks_to_html(blocks: Optional[List[Dict[str, Any]]], *, click_signer=None) -> str:
    """Genera el documento HTML completo (tabla, compatible con clientes de correo)."""
    preheader = ""
    body_rows: List[str] = []
    for block in blocks or []:
        if block.get("type") == "preheader":
            preheader_text = (block.get("props", {}) or {}).get("text", "")
            preheader = (
                f'<div style="display:none;max-height:0;overflow:hidden;">{_esc(preheader_text)}</div>'
            )
            continue
        body_rows.append(_block_to_html(block, click_signer))

    bg = "#f3f4f6"
    card = "#ffffff"
    return f"""<!DOCTYPE html>
<html lang="es" xmlns="http://www.w3.org/1999/xhtml">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<meta name="color-scheme" content="light" />
<title>{{{{campaign.subject}}}}</title>
</head>
<body style="margin:0;padding:0;background:{bg};">
{preheader}
<div style="display:none;font-size:1px;color:{bg};">{'&#8203;' * 8}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{bg};padding:24px 0;">
<tr><td align="center">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;width:100%;background:{card};border-radius:12px;overflow:hidden;">
{chr(10).join(body_rows)}
</table>
</td></tr>
</table>
</body>
</html>"""


def html_to_plain_text(html: str) -> str:
    """Genera la versión de texto plano a partir del HTML."""
    h = html2text.HTML2Text()
    h.ignore_links = False
    h.ignore_images = True
    h.body_width = 72
    h.unicode_snob = True
    text = h.handle(html or "")
    # Colapsa líneas vacías múltiples
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def wrap_plain_with_unsubscribe(text: str) -> str:
    footer = "\n\n-------------------------------------------\nPara cancelar tu suscripción visita: {{unsubscribe_url}}\n"
    return (text or "") + footer


# --------------------------------------------------------------------------- #
# Render completo de campaña
# --------------------------------------------------------------------------- #


def build_context(contact: Dict[str, Any], campaign: Dict[str, Any], unsubscribe_url: str,
                  web_view_url: str = "") -> Dict[str, Any]:
    return {
        "contact": {
            "email": contact.get("email", ""),
            "first_name": contact.get("first_name") or "",
            "last_name": contact.get("last_name") or "",
            "attributes": contact.get("attributes") or {},
        },
        "campaign": {
            "name": campaign.get("name", ""),
            "subject": campaign.get("subject", ""),
        },
        "unsubscribe_url": unsubscribe_url,
        "web_view_url": web_view_url,
    }


def render_campaign_email(
    *,
    mode: str,
    blocks: Optional[List[Dict[str, Any]]],
    html_source: Optional[str],
    subject: str,
    context: Dict[str, Any],
    click_signer=None,
) -> tuple[str, str, str]:
    """Devuelve (asunto_renderizado, html_final_sanitizado, texto_plano)."""
    rendered_subject = render_template_string(subject, context).strip() or subject

    if mode == "html":
        raw_html = html_source or ""
    else:
        raw_html = blocks_to_html(blocks, click_signer=click_signer)

    # 1) Sustitución de variables ({{unsubscribe_url}} etc.)
    html_rendered = render_template_string(raw_html, context)
    # 2) Sanitización final (sin JavaScript, URLs seguras, conserva <style>)
    html_sanitized = sanitize_email_html(html_rendered)
    # 3) CSS de <style> convertido a estilos inline (Premailer) para que el
    #    diseño sobreviva en clientes de correo que ignoran <style>
    html_final = inline_email_css(html_sanitized)
    # 4) Texto plano
    plain = html_to_plain_text(html_final)
    plain = wrap_plain_with_unsubscribe(plain)
    plain = render_template_string(plain, context)
    return rendered_subject, html_final, plain
