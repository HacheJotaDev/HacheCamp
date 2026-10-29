"""Importación y exportación CSV de contactos con validación estricta.

- Límite de tamaño configurable.
- Detección de codificación (UTF-8 con/sin BOM, Latin-1 de reserva).
- Detección de duplicados internos.
- Prevalidación para la importación asíncrona (build_import_plan).
- Normalización de direcciones.
"""
from __future__ import annotations

import csv
import io
from typing import Any, Dict, List, Tuple

from app.core.config import settings
from app.services.contacts import is_valid_email, normalize_email

EXPECTED_COLUMNS = ["email", "first_name", "last_name"]
COLUMN_ALIASES = {
    "email": {"email", "correo", "correo_electronico", "e-mail", "mail", "correo electrónico"},
    "first_name": {"first_name", "nombre", "firstname", "name", "nombre_contacto"},
    "last_name": {"last_name", "apellido", "lastname", "apellidos"},
}


class CSVImportError(ValueError):
    pass


def _decode(raw: bytes) -> Tuple[str, str]:
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig"), "utf-8-sig"
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("latin-1"), "latin-1"


def _sniff_dialect(sample: str) -> csv.Dialect:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        class _Comma(csv.excel):
            delimiter = ","
        return _Comma  # type: ignore[return-value]


def _map_headers(header: List[str]) -> Dict[str, int]:
    mapping: Dict[str, int] = {}
    for idx, raw_name in enumerate(header):
        name = (raw_name or "").strip().lower()
        for canonical, aliases in COLUMN_ALIASES.items():
            if name in aliases and canonical not in mapping:
                mapping[canonical] = idx
    return mapping


def _parece_email(texto: str) -> bool:
    from app.services.contacts import is_valid_email
    return bool(texto) and "@" in texto and is_valid_email(texto.strip().lower())


def _parse_txt(text: str, encoding: str) -> Tuple[List[str], List[Dict[str, Any]], int, str]:
    """Procesa un TXT con un correo por línea (sin encabezados)."""
    rows: List[Dict[str, Any]] = []
    invalid = 0
    for line_no, line in enumerate(text.splitlines(), start=1):
        email = normalize_email(line)
        if not email or not is_valid_email(email):
            if email or line.strip():  # solo cuenta líneas no vacías como inválidas
                invalid += 1
            continue
        rows.append({"email": email, "line": line_no})
        if len(rows) > settings.IMPORT_MAX_ROWS:
            raise CSVImportError(f"El archivo contiene más de {settings.IMPORT_MAX_ROWS:,} filas válidas".replace(",", "."))
    return ["email"], rows, invalid, encoding


def parse_csv_bytes(raw: bytes) -> Tuple[List[str], List[Dict[str, Any]], int, str]:
    """Devuelve (encabezados normalizados, filas como dicts, inválidas, encoding).

    Acepta dos formatos:
    - CSV con encabezados (columna email obligatoria; aliases reconocidos).
    - TXT plano: un correo por línea, sin encabezados.
    """
    max_bytes = settings.CSV_MAX_MB * 1024 * 1024
    if len(raw) > max_bytes:
        raise CSVImportError(f"El archivo excede el límite de {settings.CSV_MAX_MB} MB")
    if not raw:
        raise CSVImportError("El archivo está vacío")

    text, encoding = _decode(raw)
    reader_lines = text.splitlines()
    if not reader_lines:
        raise CSVImportError("El archivo está vacío")

    dialect = _sniff_dialect(reader_lines[0] + "\n" + (reader_lines[1] if len(reader_lines) > 1 else ""))
    reader = csv.reader(io.StringIO(text), dialect)

    try:
        header = next(reader)
    except StopIteration:
        raise CSVImportError("No se encontró la fila de encabezados")

    mapping = _map_headers(header)
    if "email" not in mapping:
        # Formato TXT: primera celda con aspecto de correo => un correo por línea
        primera_celda = (header[0] if header else "").strip()
        if _parece_email(primera_celda):
            return _parse_txt(text, encoding)
        raise CSVImportError(
            "Falta la columna 'email' (acepta: email, correo, mail, e-mail) "
            "o el archivo no es un TXT de correos (un correo por línea)"
        )

    extra_columns: Dict[int, str] = {
        idx: (name or "").strip().lower()
        for idx, name in enumerate(header)
        if idx not in mapping.values() and idx != mapping.get("email") and idx != mapping.get("first_name") and idx != mapping.get("last_name") and (name or "").strip()
    }

    rows: List[Dict[str, Any]] = []
    invalid_rows = 0
    for line_no, row in enumerate(reader, start=2):
        if not row or all(not c.strip() for c in row):
            continue
        email = normalize_email(row[mapping["email"]]) if mapping["email"] < len(row) else ""
        if not email or not is_valid_email(email):
            invalid_rows += 1
            continue
        entry: Dict[str, Any] = {"email": email, "line": line_no}
        if "first_name" in mapping and mapping["first_name"] < len(row):
            entry["first_name"] = row[mapping["first_name"]].strip()[:120] or None
        if "last_name" in mapping and mapping["last_name"] < len(row):
            entry["last_name"] = row[mapping["last_name"]].strip()[:120] or None
        attributes = {}
        for idx, col_name in extra_columns.items():
            if idx < len(row) and row[idx].strip():
                attributes[col_name] = row[idx].strip()[:500]
        if attributes:
            entry["attributes"] = attributes
        rows.append(entry)
        if len(rows) > settings.IMPORT_MAX_ROWS:
            raise CSVImportError(f"El archivo contiene más de {settings.IMPORT_MAX_ROWS:,} filas válidas".replace(",", "."))

    headers_normalized = [h for h in ["email", "first_name", "last_name"] if h in mapping]
    return headers_normalized, rows, invalid_rows, encoding  # type: ignore[return-value]


def build_import_plan(raw: bytes) -> Dict[str, Any]:
    """Prevalida un archivo CSV/TXT y produce el PLAN de importación.

    Se ejecuta en la subida (fuera del event loop, en un hilo): parsea,
    normaliza, valida y deduplica en memoria. Devuelve un dict serializable a
    JSON que el worker procesará por lotes:

        {
          "rows": [ {"email", "first_name", "last_name", "attributes", "line"}, ... ],
          "filas_validas", "filas_invalidas", "duplicados_en_archivo",
          "encoding", "headers",
        }

    Sin acceso a base de datos: la deduplicación contra la BD la hace el
    worker por lote (upsert idempotente).
    """
    _, rows, invalid_rows, encoding = parse_csv_bytes(raw)

    seen: set[str] = set()
    plan_rows: List[Dict[str, Any]] = []
    duplicates = 0
    for row in rows:
        email = row["email"]
        if email in seen:
            duplicates += 1
            continue
        seen.add(email)
        plan_rows.append(row)

    headers = sorted({k for r in plan_rows for k in r.keys() if k not in ("line",)} - {"email"})
    return {
        "rows": plan_rows,
        "filas_validas": len(plan_rows),
        "filas_invalidas": invalid_rows,
        "duplicados_en_archivo": duplicates,
        "encoding": encoding,
        "headers": ["email"] + headers,
    }
