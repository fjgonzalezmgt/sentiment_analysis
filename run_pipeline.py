# -*- coding: utf-8 -*-
"""run_pipeline.py — Orquestador del pipeline completo.

Este script ejecuta el pipeline end-to-end:

1) Lee un listado de empresas desde un Excel (por defecto ``Empresas.xlsx``).
2) Ejecuta scraping de reseñas con ``web_scrapping.py``.
3) Ejecuta ``procesado_resenas``.
4) Ejecuta ``llm_parse``.
5) Persiste y vectoriza los resultados en Chroma.

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
- ``OPENAI_API_KEY`` para clasificación y embeddings.
- Chroma local persistente por defecto o servidor mediante ``CHROMA_HOST``.

Author
------
Francisco Gonzalez
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

import chroma_store
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
    ap = argparse.ArgumentParser(
        description="Pipeline completo (scrape -> procesado -> OpenAI -> Chroma)."
    )
    ap.add_argument("--empresas-xlsx", default="Empresas.xlsx", help="Ruta al Excel con el listado de empresas.")
    ap.add_argument("--sheet", default=None, help="Nombre de hoja (sheet) a leer. Si se omite, usa la primera.")
    ap.add_argument("--company-col", default=None, help="Nombre de columna para empresa/company (opcional).")
    ap.add_argument("--url-col", default=None, help="Nombre de columna para URL Trustpilot (opcional).")
    ap.add_argument(
        "--from-combined-csv",
        default=None,
        metavar="PATH",
        help=(
            "Inicia en un resenas_combinadas.csv existente y omite Excel, "
            "scraping y consolidación."
        ),
    )

    ap.add_argument("--timeout", type=int, default=25, help="Timeout (s) por request en scraping.")
    ap.add_argument("--pause", type=float, default=2.0, help="Pausa (s) entre páginas en scraping.")
    ap.add_argument("--max-pages", type=int, default=500, help="Máximo de páginas por empresa.")
    ap.add_argument(
        "--test-mode",
        action="store_true",
        help="Modo pruebas: procesa 3 reseñas por empresa de forma síncrona.",
    )
    ap.add_argument(
        "--llm-mode",
        choices=("batch", "sync"),
        default="batch",
        help="Batch API (producción, por defecto) o Responses API síncrona.",
    )
    ap.add_argument(
        "--finalize-batch",
        action="store_true",
        help="Omite scraping, descarga el Batch terminado y carga el resultado a Chroma.",
    )
    ap.add_argument(
        "--batch-id",
        default=None,
        help="ID opcional del Batch; si se omite se usa output/openai_batch_state.json.",
    )
    ap.add_argument("--playwright", action="store_true", help="Usa Playwright como fallback si requests no extrae.")
    ap.add_argument("--conservative", action="store_true", help="Aborta si robots.txt no es legible (modo conservador).")
    return ap.parse_args()


def run_classification_stage(
    combined_csv: str,
    output_dir: str,
    llm_mode: str,
    *,
    test_mode: bool = False,
) -> None:
    """Ejecutar clasificación y persistencia desde un CSV consolidado.

    Parameters
    ----------
    combined_csv : str
        Archivo ``resenas_combinadas.csv`` que contiene la columna ``body``.
    output_dir : str
        Directorio para resultados clasificados y estado Batch.
    llm_mode : {"batch", "sync"}
        Modo de ejecución de OpenAI.
    test_mode : bool, default=False
        Limita la clasificación a tres filas y fuerza modo síncrono.

    Returns
    -------
    None
        El modo síncrono carga Chroma; el modo Batch guarda el estado para
        finalizarlo posteriormente.

    Raises
    ------
    FileNotFoundError
        Si ``combined_csv`` no existe.
    """
    logger = setup_logger("run_pipeline.classification")
    if not os.path.isfile(combined_csv):
        raise FileNotFoundError(f"No se encontró el CSV consolidado: {combined_csv}")

    llm_out_xlsx = os.path.join(output_dir, "resenas_clasificadas.xlsx")
    llm_out_csv = os.path.join(output_dir, "resenas_clasificadas.csv")
    batch_state_path = os.path.join(output_dir, "openai_batch_state.json")

    if test_mode or llm_mode == "sync":
        logger.info("Clasificando con Responses API en modo síncrono...")
        llm_parse.classify_sync(
            input_csv=combined_csv,
            output_xlsx=llm_out_xlsx,
            output_csv=llm_out_csv,
            max_rows=3 if test_mode else None,
        )
        stored = chroma_store.upsert_reviews(llm_out_xlsx)
        logger.info("Pipeline completo: %s reseñas almacenadas en Chroma.", stored)
        return

    logger.info("Preparando y enviando clasificación mediante OpenAI Batch API...")
    state = llm_parse.submit_batch(
        input_csv=combined_csv,
        batch_input_path=os.path.join(output_dir, "openai_batch_requests.jsonl"),
        manifest_path=os.path.join(output_dir, "openai_batch_manifest.json"),
        state_path=batch_state_path,
    )
    logger.info(
        "Batch %s enviado. Cuando termine, ejecuta: "
        "python run_pipeline.py --finalize-batch",
        state["batch_id"],
    )


def main() -> None:
    """Ejecuta el pipeline completo (scrape -> procesado -> LLM -> Chroma).

    Returns
    -------
    None

    Raises
    ------
    FileNotFoundError
        Si el CSV consolidado indicado no existe.
    RuntimeError
        Si el Excel no contiene empresas válidas.
    """
    logger = setup_logger("run_pipeline")
    args = parse_args()

    max_reviews = 3 if args.test_mode else None
    review_data_dir = "review_data_test" if args.test_mode else "review_data"
    output_dir = "output_test" if args.test_mode else "output"
    llm_out_xlsx = os.path.join(output_dir, "resenas_clasificadas.xlsx")
    llm_out_csv = os.path.join(output_dir, "resenas_clasificadas.csv")
    batch_state_path = os.path.join(output_dir, "openai_batch_state.json")

    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(review_data_dir, exist_ok=True)

    if args.finalize_batch:
        result = llm_parse.finalize_batch(
            state_path=batch_state_path,
            batch_id=args.batch_id,
            output_xlsx=llm_out_xlsx,
            output_csv=llm_out_csv,
        )
        if result is None:
            logger.info("El Batch aún no terminó; no se modifica Chroma.")
            return
        stored = chroma_store.upsert_reviews(llm_out_xlsx)
        logger.info("Pipeline finalizado: %s reseñas almacenadas en Chroma.", stored)
        return

    if args.from_combined_csv:
        logger.info(
            "Inicio desde CSV consolidado: se omiten scraping y procesamiento."
        )
        run_classification_stage(
            combined_csv=args.from_combined_csv,
            output_dir=output_dir,
            llm_mode=args.llm_mode,
            test_mode=args.test_mode,
        )
        return

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

    run_classification_stage(
        combined_csv=combined_csv,
        output_dir=output_dir,
        llm_mode=args.llm_mode,
        test_mode=args.test_mode,
    )


if __name__ == "__main__":
    main()
