# -*- coding: utf-8 -*-
"""run_pipeline.py — Orquestador del pipeline completo.

Este script ejecuta el pipeline end-to-end:

1) Lee un listado de empresas desde un Excel (por defecto ``Empresas.xlsx``).
2) Ejecuta scraping de reseñas con ``web_scrapping.py``.
3) Ejecuta ``procesado_resenas``.
4) Ejecuta ``llm_parse``.
5) Ejecuta ``load_to_bigquery``.

El Excel puede contener:
- Una columna de empresa/dominio (por ejemplo ``Empresa``) con valores como
    ``sending.es``.
- Opcionalmente, una columna de URL; si está presente, se usa tal cual.

Importante
----------
La construcción/normalización de la URL de Trustpilot se delega a
``web_scrapping.main(company=...)``: si se recibe un dominio, ``web_scrapping``
construye ``https://es.trustpilot.com/review/<dominio>``; si se recibe una URL
completa, la respeta.

Uso
---
Ejemplo:
        python run_pipeline.py

Opcional:
        python run_pipeline.py --empresas-xlsx Empresas.xlsx --sheet Hoja1 --playwright

Requisitos
----------
- Variables/credenciales según los módulos invocados:
    - ``OPENAI_API_KEY`` (para ``llm_parse``)
    - Credenciales GCP JSON según ``load_to_bigquery.py``
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterable, Optional

import pandas as pd

import load_to_bigquery
import llm_parse
import procesado_resenas
import web_scrapping
from logger_library import setup_logger


@dataclass(frozen=True)
class EmpresaSpec:
    """Especificación de una empresa a procesar.

    Parameters
    ----------
    company : str
        Nombre legible de la empresa (se usa para logs y para nombre de archivo).
    company_or_url : str
        Identificador de entrada que se le pasa a ``web_scrapping``.
        Puede ser un dominio (p. ej. ``sending.es``) o una URL completa.
    """

    company: str
    company_or_url: str


@contextmanager
def patched_argv(new_argv: list[str]):
    """Reemplaza temporalmente ``sys.argv`` para invocar módulos con argparse.

    Parameters
    ----------
    new_argv : list[str]
        Lista completa de argumentos (incluyendo el nombre del script como
        primer elemento) que será asignada a ``sys.argv`` durante el contexto.

    Yields
    ------
    None
        No retorna valores; únicamente administra el estado de ``sys.argv``.
    """
    old_argv = sys.argv[:]
    sys.argv = new_argv
    try:
        yield
    finally:
        sys.argv = old_argv


def slugify_company(value: str) -> str:
    """Convierte un nombre de empresa a un slug seguro para archivos.

    Parameters
    ----------
    value : str
        Texto de entrada (nombre de empresa/dominio).

    Returns
    -------
    str
        Slug en minúsculas que contiene solo caracteres seguros.
    """
    s = str(value or "").strip()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s)
    s = s.strip("._-")
    return s.lower() or "empresa"


def _pick_column(df: pd.DataFrame, candidates: Iterable[str]) -> Optional[str]:
    """Encuentra una columna del DataFrame por lista de candidatos (case-insensitive).

    Parameters
    ----------
    df : pandas.DataFrame
        DataFrame con columnas leídas desde Excel.
    candidates : Iterable[str]
        Nombres de columnas candidatos (se comparan en minúsculas y recortados).

    Returns
    -------
    str | None
        El nombre real de la columna existente en ``df`` o ``None`` si no se
        encuentra coincidencia.
    """
    lowered = {str(c).strip().lower(): str(c) for c in df.columns}
    for c in candidates:
        if c.lower() in lowered:
            return lowered[c.lower()]
    return None


def read_empresas_from_excel(
    xlsx_path: str,
    sheet: Optional[str] = None,
    company_col: Optional[str] = None,
    url_col: Optional[str] = None,
) -> list[EmpresaSpec]:
    """Lee el listado de empresas desde un archivo Excel.

    El Excel puede tener una o dos columnas relevantes:
    - Empresa/dominio (p. ej. ``Empresa``): valores como ``sending.es``.
    - URL (p. ej. ``url``/``enlace``): una URL completa a Trustpilot.

    La URL final se delega a ``web_scrapping``: este lector solo conserva el
    valor de entrada como ``company_or_url``.

    Parameters
    ----------
    xlsx_path : str
        Ruta del archivo Excel.
    sheet : str | None, default=None
        Nombre de la hoja. Si es ``None``, usa la primera hoja.
    company_col : str | None, default=None
        Nombre exacto de la columna de empresa/dominio. Si es ``None``, se
        intenta detectar automáticamente.
    url_col : str | None, default=None
        Nombre exacto de la columna de URL. Si es ``None``, se intenta detectar
        automáticamente.

    Returns
    -------
    list[EmpresaSpec]
        Lista de empresas deduplicadas por ``company_or_url``.

    Raises
    ------
    FileNotFoundError
        Si ``xlsx_path`` no existe.
    """
    if not os.path.exists(xlsx_path):
        raise FileNotFoundError(f"No se encontró el archivo Excel: {xlsx_path}")

    df = pd.read_excel(xlsx_path, sheet_name=sheet if sheet is not None else 0)
    if df.empty:
        return []

    # Normaliza nombres de columnas
    df.columns = [str(c).strip() for c in df.columns]

    detected_url_col = url_col or _pick_column(
        df,
        [
            "trustpilot_url",
            "url",
            "link",
            "enlace",
            "pagina",
            "página",
        ],
    )

    detected_company_col = company_col or _pick_column(
        df,
        [
            "company",
            "empresa",
            "nombre",
            "name",
            "dominio",
            "domain",
        ],
    )

    # Si no hay columnas reconocibles, usa la primera columna
    if not detected_url_col and not detected_company_col:
        detected_company_col = str(df.columns[0])

    empresas: list[EmpresaSpec] = []
    for _, row in df.iterrows():
        raw_company = row.get(detected_company_col) if detected_company_col else None
        raw_url = row.get(detected_url_col) if detected_url_col else None

        # Delegamos la construcción/normalización de la URL a web_scrapping.py.
        # - Si hay una URL explícita en el Excel, la usamos tal cual.
        # - Si no, usamos el valor de la empresa/dominio (p. ej. sending.es) y
        #   web_scrapping construirá https://es.trustpilot.com/review/<empresa>.
        if raw_url is not None and str(raw_url).strip():
            company_or_url = str(raw_url).strip()
        elif raw_company is not None and str(raw_company).strip():
            company_or_url = str(raw_company).strip()
        else:
            continue

        # Company para nombre de archivo/log (si no hay, reusar company_or_url)
        company = str(raw_company).strip() if raw_company is not None and str(raw_company).strip() else company_or_url

        empresas.append(EmpresaSpec(company=company, company_or_url=company_or_url))

    # Dedup por entrada (URL o empresa/dominio)
    unique: dict[str, EmpresaSpec] = {}
    for e in empresas:
        unique[e.company_or_url.strip().lower()] = e
    return list(unique.values())


def run_web_scraping_for_empresas(
    empresas: list[EmpresaSpec],
    review_data_dir: str,
    timeout: int,
    pause: float,
    max_pages: int,
    max_reviews: int | None,
    playwright: bool,
    conservative: bool,
) -> None:
    """Ejecuta ``web_scrapping`` para una lista de empresas.

    Parameters
    ----------
    empresas : list[EmpresaSpec]
        Empresas a scrapear.
    review_data_dir : str
        Directorio donde se guardarán los CSV de reseñas.
    timeout : int
        Timeout (s) por request.
    pause : float
        Pausa (s) entre páginas.
    max_pages : int
        Número máximo de páginas a recorrer por empresa.
    max_reviews : int | None
        Límite de reseñas (deduplicadas) a recolectar por empresa. Si es
        ``None``, no se aplica límite.
    playwright : bool
        Si True, activa el modo Playwright en ``web_scrapping``.
    conservative : bool
        Si True, activa modo conservador respecto a robots.txt.

    Returns
    -------
    None
    """
    logger = setup_logger("run_pipeline.web_scraping")
    os.makedirs(review_data_dir, exist_ok=True)

    for e in empresas:
        slug = slugify_company(e.company)
        out_csv = os.path.join(review_data_dir, f"trustpilot_reviews_{slug}.csv")

        argv = [
            "web_scrapping.py",
            "--out",
            out_csv,
            "--timeout",
            str(timeout),
            "--pause",
            str(pause),
            "--max-pages",
            str(max_pages),
        ]
        if max_reviews is not None:
            argv.extend(["--max-reviews", str(max_reviews)])
        if playwright:
            argv.append("--playwright")
        if conservative:
            argv.append("--conservative")

        logger.info(f"Scrape: {e.company} -> {e.company_or_url}")
        try:
            with patched_argv(argv):
                web_scrapping.main(company=e.company_or_url)
        except Exception as ex:
            logger.exception(f"Fallo scraping para '{e.company}' ({e.company_or_url}): {ex}")


def parse_args() -> argparse.Namespace:
    """Parsea argumentos de línea de comandos.

    Returns
    -------
    argparse.Namespace
        Argumentos parseados para controlar lectura del Excel y parámetros de
        scraping.
    """
    ap = argparse.ArgumentParser(description="Ejecuta el pipeline completo (scrape -> procesado -> LLM -> BigQuery).")
    ap.add_argument("--empresas-xlsx", default="Empresas.xlsx", help="Ruta al Excel con el listado de empresas.")
    ap.add_argument("--sheet", default=None, help="Nombre de hoja (sheet) a leer. Si se omite, usa la primera.")
    ap.add_argument("--company-col", default=None, help="Nombre de columna para empresa/company (opcional).")
    ap.add_argument("--url-col", default=None, help="Nombre de columna para URL Trustpilot (opcional).")

    ap.add_argument("--timeout", type=int, default=25, help="Timeout (s) por request en scraping.")
    ap.add_argument("--pause", type=float, default=2.0, help="Pausa (s) entre páginas en scraping.")
    ap.add_argument("--max-pages", type=int, default=500, help="Máximo de páginas por empresa.")
    ap.add_argument(
        "--test-mode",
        action="store_true",
        help="Modo pruebas: scrapea solo 3 reseñas por empresa y NO carga a BigQuery.",
    )
    ap.add_argument("--playwright", action="store_true", help="Usa Playwright como fallback si requests no extrae.")
    ap.add_argument("--conservative", action="store_true", help="Aborta si robots.txt no es legible (modo conservador).")
    return ap.parse_args()


def main() -> None:
    """Ejecuta el pipeline completo (scrape -> procesado -> LLM -> BigQuery).

    Returns
    -------
    None

    Raises
    ------
    RuntimeError
        Si el Excel no contiene empresas válidas.
    """
    logger = setup_logger("run_pipeline")
    args = parse_args()

    max_reviews = 3 if args.test_mode else None
    review_data_dir = "review_data_test" if args.test_mode else "review_data"
    output_dir = "output_test" if args.test_mode else "output"

    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(review_data_dir, exist_ok=True)

    # En modo pruebas, evita mezclar con datos de ejecuciones anteriores.
    if args.test_mode:
        for name in os.listdir(review_data_dir):
            if name.lower().endswith(".csv") and name.lower().startswith("trustpilot_reviews_"):
                try:
                    os.remove(os.path.join(review_data_dir, name))
                except OSError:
                    pass

        for name in os.listdir(output_dir):
            if name.lower().startswith("resenas_") and (name.lower().endswith(".csv") or name.lower().endswith(".xlsx")):
                try:
                    os.remove(os.path.join(output_dir, name))
                except OSError:
                    pass

    logger.info("Leyendo Empresas.xlsx...")
    empresas = read_empresas_from_excel(
        xlsx_path=args.empresas_xlsx,
        sheet=args.sheet,
        company_col=args.company_col,
        url_col=args.url_col,
    )
    if not empresas:
        raise RuntimeError("No se encontraron empresas válidas en el Excel.")

    logger.info(f"Empresas a scrapear: {len(empresas)}")
    run_web_scraping_for_empresas(
        empresas=empresas,
        review_data_dir=review_data_dir,
        timeout=args.timeout,
        pause=args.pause,
        max_pages=args.max_pages,
        max_reviews=max_reviews,
        playwright=args.playwright,
        conservative=args.conservative,
    )

    logger.info("Ejecutando procesado_resenas...")
    combined_xlsx = os.path.join(output_dir, "resenas_combinadas.xlsx")
    combined_csv = os.path.join(output_dir, "resenas_combinadas.csv")
    procesado_resenas.main(
        carpeta_entrada=review_data_dir,
        output_xlsx=combined_xlsx,
        output_csv=combined_csv,
    )

    logger.info("Ejecutando llm_parse...")
    llm_out_xlsx = os.path.join(output_dir, "resenas_clasificadas.xlsx")
    llm_parse.main(
        input_csv=combined_csv,
        output_xlsx=llm_out_xlsx,
        max_rows=3 if args.test_mode else None,
    )

    if args.test_mode:
        logger.info("Modo pruebas activo: se omite load_to_bigquery.")
    else:
        logger.info("Ejecutando load_to_bigquery...")
        load_to_bigquery.main()

    logger.info("Pipeline completo finalizado.")


if __name__ == "__main__":
    main()
