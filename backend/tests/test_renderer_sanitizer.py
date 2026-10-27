"""Pruebas del renderizador, sanitizador y plantillas."""
from __future__ import annotations

import pytest

from app.services.renderer import (
    blocks_to_html,
    html_to_plain_text,
    render_campaign_email,
    render_template_string,
    validate_template_source,
    build_context,
)
from app.services.sanitizer import (
    sanitize_email_html,
    sanitize_editor_blocks,
    validate_url,
)


class TestSanitizador:
    def test_elimina_script(self):
        assert "<script>" not in sanitize_email_html("<p>a</p><script>alert(1)</script>")

    def test_elimina_onclick(self):
        out = sanitize_email_html('<p onclick="x()">hola</p>')
        assert "onclick" not in out
        assert "hola" in out

    def test_elimina_javascript_url(self):
        out = sanitize_email_html('<a href="javascript:alert(1)">c</a>')
        assert "javascript" not in out.lower()

    def test_elimina_iframe(self):
        assert "<iframe" not in sanitize_email_html('<iframe src="http://x"></iframe>')

    def test_conserva_html_seguro(self):
        out = sanitize_email_html('<p style="color:red;">Hola <b>mundo</b></p>')
        assert "<b>mundo</b>" in out

    def test_permitidos_mailto(self):
        out = sanitize_email_html('<a href="mailto:a@b.com">m</a>')
        assert "mailto:a@b.com" in out


class TestURLs:
    def test_http_ok(self):
        assert validate_url("https://ejemplo.com/pagina")

    def test_mailto_ok(self):
        assert validate_url("mailto:x@y.com")

    def test_javascript_no(self):
        assert not validate_url("javascript:alert(1)")

    def test_data_no(self):
        assert not validate_url("data:text/html;base64,xxx")

    def test_vacia_no(self):
        assert not validate_url("")

    def test_ssrf_localhost_bloqueado(self):
        assert not validate_url("http://localhost:8080/admin")

    def test_ssrf_ips_internas_bloqueadas(self):
        # Metadatos cloud, loopback por IP, RFC 1918 y CGNAT quedan bloqueados
        for url in (
            "http://169.254.169.254/latest/meta-data/",
            "http://127.0.0.1:8086/api",
            "http://10.1.2.3/interno",
            "http://192.168.1.10/cam",
            "http://172.16.0.5/x",
            "http://100.64.0.1/cgnat",
        ):
            assert not validate_url(url), url

    def test_ips_publicas_permitidas(self):
        assert validate_url("https://8.8.8.8/ok")
        assert validate_url("https://leisureparts4u.store/imagen.png")

    def test_css_behavior_eliminado(self):
        # behavior:url(...) (IE) y expresiones CSS se eliminan de <style> y style=
        html = '<style>a{behavior:url(#default#time2)}</style><p style="behavior:url(x)">h</p>'
        out = sanitize_email_html(html)
        assert "behavior" not in out.lower()


class TestBloques:
    def test_bloques_saneados(self):
        blocks = [{"type": "texto", "props": {"content": "<p>ok</p><script>bad()</script>"}}]
        out = sanitize_editor_blocks(blocks)
        assert out[0]["props"]["content"] == "<p>ok</p>"

    def test_url_insegura_descartada(self):
        blocks = [{"type": "imagen", "props": {"src": "javascript:x"}}]
        out = sanitize_editor_blocks(blocks)
        assert out[0]["props"]["src"] == ""

    def test_bloques_a_html_estructura(self):
        html = blocks_to_html([
            {"type": "titulo", "props": {"text": "Título", "level": 2}},
            {"type": "texto", "props": {"content": "<p>Cuerpo</p>"}},
            {"type": "boton", "props": {"text": "Click", "href": "https://x.com"}},
            {"type": "pie", "props": {"text": "Pie"}},
        ])
        assert "<table" in html and "Título" in html and "Click" in html
        # El pie incluye el enlace de baja con la variable
        assert "{{unsubscribe_url}}" in html


class TestRender:
    def test_variables_sustituidas(self):
        ctx = build_context(
            {"email": "a@b.com", "first_name": "Ana"},
            {"name": "Campaña X", "subject": "Hola"},
            "https://p/baja?token=abc",
        )
        out = render_template_string("Hola {{contact.first_name}} <b>{{contact.email}}</b>", ctx)
        assert "Ana" in out and "a@b.com" in out

    def test_variable_ausente_no_rompe(self):
        out = render_template_string("Hola {{contact.campo_inexistente}}", {"contact": {}})
        assert "Hola" in out

    def test_autoescape_previene_xss(self):
        out = render_template_string("{{contact.first_name}}", {"contact": {"first_name": "<script>x</script>"}})
        assert "<script>" not in out

    def test_sintaxis_invalida_detectada(self):
        assert validate_template_source("{{ sin_cerrar ") is not None
        assert validate_template_source("Hola {{contact.first_name}}") is None

    def test_render_completo_genera_texto_plano(self):
        ctx = build_context(
            {"email": "a@b.com", "first_name": "Ana"},
            {"name": "C", "subject": "Asunto"},
            "https://p/baja",
        )
        subj, html, plain = render_campaign_email(
            mode="bloques",
            blocks=[{"type": "titulo", "props": {"text": "Hola {{contact.first_name}}", "level": 1}}],
            html_source=None,
            subject="Hola {{contact.first_name}}",
            context=ctx,
        )
        assert subj == "Hola Ana"
        assert "Hola Ana" in html
        assert "Hola Ana" in plain
        # El texto plano incluye el enlace de baja
        assert "https://p/baja" in plain

    def test_no_script_en_salida_final(self):
        ctx = build_context({"email": "a@b.com"}, {"name": "C", "subject": "S"}, "https://p/baja")
        _, html, _ = render_campaign_email(
            mode="html", blocks=None,
            html_source='<p>Hi</p><script>alert(1)</script><img src="https://x/i.png" onerror="p()">',
            subject="S", context=ctx,
        )
        assert "<script>" not in html and "onerror" not in html


class TestTextoPlano:
    def test_convierte_links(self):
        text = html_to_plain_text('<p>Visita <a href="https://x.com">esto</a></p>')
        assert "https://x.com" in text
