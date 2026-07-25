# -*- coding: utf-8 -*-
"""Persistencia y búsqueda semántica de reseñas con Chroma.

Author
------
Francisco Gonzalez
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Iterable

import chromadb
import pandas as pd
from chromadb.api.models.Collection import Collection
from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction
from dotenv import load_dotenv

from logger_library import setup_logger

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env.local")
load_dotenv(BASE_DIR / ".env")

DEFAULT_INPUT = "output/resenas_clasificadas.xlsx"
DEFAULT_CHROMA_PATH = os.getenv("CHROMA_PATH", "chroma_data")
DEFAULT_COLLECTION = os.getenv("CHROMA_COLLECTION", "customer_reviews")
DEFAULT_EMBEDDING_MODEL = os.getenv(
    "OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"
)
DEFAULT_UPSERT_BATCH_SIZE = int(os.getenv("CHROMA_UPSERT_BATCH_SIZE", "100"))
SCHEMA_VERSION = "2"


def get_chroma_client() -> chromadb.ClientAPI:
    """Construir el cliente de Chroma configurado para el entorno.

    Returns
    -------
    chromadb.ClientAPI
        Cliente HTTP cuando ``CHROMA_HOST`` está definido; en caso contrario,
        cliente local persistente.
    """
    host = os.getenv("CHROMA_HOST")
    if host:
        return chromadb.HttpClient(
            host=host,
            port=int(os.getenv("CHROMA_PORT", "8000")),
            ssl=os.getenv("CHROMA_SSL", "false").lower() == "true",
        )
    Path(DEFAULT_CHROMA_PATH).mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=DEFAULT_CHROMA_PATH)


def get_embedding_function() -> OpenAIEmbeddingFunction:
    """Construir la función de embeddings de OpenAI usada por Chroma.

    Returns
    -------
    OpenAIEmbeddingFunction
        Función configurada con ``OPENAI_EMBEDDING_MODEL``.

    Raises
    ------
    RuntimeError
        Si ``OPENAI_API_KEY`` no está disponible.
    """
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("Falta OPENAI_API_KEY para generar embeddings.")
    return OpenAIEmbeddingFunction(model_name=DEFAULT_EMBEDDING_MODEL)


def get_collection(client: chromadb.ClientAPI | None = None) -> Collection:
    """Obtener o crear la colección de reseñas.

    Parameters
    ----------
    client : chromadb.ClientAPI or None, default=None
        Cliente de Chroma. Si se omite, se crea a partir del entorno.

    Returns
    -------
    Collection
        Colección configurada con la función de embeddings del proyecto.

    Raises
    ------
    RuntimeError
        Si la colección existente fue creada con otro modelo de embeddings.
    """
    client = client or get_chroma_client()
    collection = client.get_or_create_collection(
        name=DEFAULT_COLLECTION,
        embedding_function=get_embedding_function(),
        metadata={
            "description": "Reseñas clasificadas y consultables semánticamente",
            "schema_version": SCHEMA_VERSION,
            "embedding_model": DEFAULT_EMBEDDING_MODEL,
        },
    )
    stored_model = (collection.metadata or {}).get("embedding_model")
    if stored_model and stored_model != DEFAULT_EMBEDDING_MODEL:
        raise RuntimeError(
            f"La colección usa '{stored_model}', pero la configuración solicita "
            f"'{DEFAULT_EMBEDDING_MODEL}'. Define otro CHROMA_COLLECTION o reindexa."
        )
    return collection


def _is_missing(value: Any) -> bool:
    """Determinar si un valor debe tratarse como ausente.

    Parameters
    ----------
    value : Any
        Valor que se desea evaluar.

    Returns
    -------
    bool
        ``True`` para ``None`` o un número flotante NaN.
    """
    if value is None:
        return True
    if isinstance(value, float):
        return math.isnan(value)
    return False


def sanitize_metadata(row: dict[str, Any]) -> dict[str, str | int | float | bool]:
    """Convertir una fila a metadatos admitidos por Chroma.

    Parameters
    ----------
    row : dict[str, Any]
        Registro tabular original.

    Returns
    -------
    dict[str, str or int or float or bool]
        Metadatos sin texto duplicado ni valores ausentes.
    """
    metadata: dict[str, str | int | float | bool] = {}
    for key, value in row.items():
        if key in {"review", "body"} or _is_missing(value):
            continue
        if hasattr(value, "isoformat"):
            value = value.isoformat()
        if isinstance(value, (str, int, float, bool)):
            metadata[str(key)] = value
        else:
            metadata[str(key)] = json.dumps(value, ensure_ascii=False, default=str)
    metadata["schema_version"] = SCHEMA_VERSION
    return metadata


def stable_review_id(row: dict[str, Any]) -> str:
    """Generar un identificador estable para una reseña.

    Parameters
    ----------
    row : dict[str, Any]
        Registro que contiene la identidad y el texto de la reseña.

    Returns
    -------
    str
        Identificador SHA-256 con prefijo ``review-``.
    """
    natural_key = "|".join(
        str(row.get(key, ""))
        for key in ("review_id", "company", "source_url", "review", "body")
    )
    return "review-" + hashlib.sha256(natural_key.encode("utf-8")).hexdigest()


def _read_dataframe(path_value: str) -> pd.DataFrame:
    """Leer un archivo tabular compatible.

    Parameters
    ----------
    path_value : str
        Ruta del archivo CSV o Excel.

    Returns
    -------
    pandas.DataFrame
        Datos leídos desde el archivo.

    Raises
    ------
    FileNotFoundError
        Si la ruta no existe.
    ValueError
        Si la extensión no corresponde a CSV o Excel.
    """
    path = Path(path_value)
    if not path.exists():
        raise FileNotFoundError(f"No se encontró el archivo: {path}")
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    if path.suffix.lower() in {".xlsx", ".xls"}:
        return pd.read_excel(path)
    raise ValueError("La entrada debe ser CSV o Excel.")


def _chunks(values: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    """Dividir registros en grupos consecutivos.

    Parameters
    ----------
    values : list[dict[str, Any]]
        Registros que se desean agrupar.
    size : int
        Cantidad máxima de registros por grupo.

    Yields
    ------
    list[dict[str, Any]]
        Siguiente grupo de registros.
    """
    for start in range(0, len(values), size):
        yield values[start : start + size]


def upsert_reviews(
    input_path: str = DEFAULT_INPUT,
    *,
    batch_size: int = DEFAULT_UPSERT_BATCH_SIZE,
    collection: Collection | None = None,
) -> int:
    """Insertar o actualizar reseñas en Chroma por lotes.

    Parameters
    ----------
    input_path : str, default=DEFAULT_INPUT
        Archivo CSV o Excel con las reseñas clasificadas.
    batch_size : int, default=DEFAULT_UPSERT_BATCH_SIZE
        Número máximo de documentos por operación de upsert y embeddings.
    collection : Collection or None, default=None
        Colección de destino. Se crea o recupera cuando se omite.

    Returns
    -------
    int
        Número de reseñas almacenadas.

    Raises
    ------
    ValueError
        Si ``batch_size`` es menor que uno.
    KeyError
        Si la entrada no contiene ``review`` ni ``body``.
    """
    if batch_size < 1:
        raise ValueError("batch_size debe ser >= 1")
    logger = setup_logger("chroma_store")
    df = _read_dataframe(input_path)
    review_column = "review" if "review" in df.columns else "body"
    if review_column not in df.columns:
        raise KeyError("La entrada debe contener una columna 'review' o 'body'.")
    rows = [
        {key: value for key, value in row.items()}
        for row in df.to_dict(orient="records")
        if str(row.get(review_column, "")).strip()
    ]
    target = collection or get_collection()
    processed = 0
    for chunk in _chunks(rows, batch_size):
        target.upsert(
            ids=[stable_review_id(row) for row in chunk],
            documents=[str(row[review_column]).strip() for row in chunk],
            metadatas=[sanitize_metadata(row) for row in chunk],
        )
        processed += len(chunk)
        logger.info("Chroma: %s/%s reseñas almacenadas.", processed, len(rows))
    return processed


def semantic_search(
    query: str,
    *,
    n_results: int = 10,
    where: dict[str, Any] | None = None,
    collection: Collection | None = None,
) -> list[dict[str, Any]]:
    """Buscar reseñas por similitud semántica.

    Parameters
    ----------
    query : str
        Consulta de texto libre.
    n_results : int, default=10
        Número máximo de resultados.
    where : dict[str, Any] or None, default=None
        Filtro de metadatos compatible con Chroma.
    collection : Collection or None, default=None
        Colección que se desea consultar.

    Returns
    -------
    list[dict[str, Any]]
        Resultados ordenados con distancia, reseña y metadatos.
    """
    target = collection or get_collection()
    available = target.count()
    if available == 0:
        return []
    response = target.query(
        query_texts=[query],
        n_results=min(n_results, available),
        where=where,
        include=["documents", "metadatas", "distances"],
    )
    return [
        {
            "id": response["ids"][0][index],
            "distance": response["distances"][0][index],
            "review": response["documents"][0][index],
            **(response["metadatas"][0][index] or {}),
        }
        for index in range(len(response["ids"][0]))
    ]


def export_collection(
    output_path: str,
    *,
    collection: Collection | None = None,
    page_size: int = 500,
) -> int:
    """Exportar la colección para Power BI u otras herramientas tabulares.

    Parameters
    ----------
    output_path : str
        Ruta CSV o Excel de destino.
    collection : Collection or None, default=None
        Colección que se desea exportar.
    page_size : int, default=500
        Registros solicitados por página.

    Returns
    -------
    int
        Número de registros exportados.
    """
    target = collection or get_collection()
    records: list[dict[str, Any]] = []
    for offset in range(0, target.count(), page_size):
        page = target.get(
            limit=page_size,
            offset=offset,
            include=["documents", "metadatas"],
        )
        for index, record_id in enumerate(page["ids"]):
            records.append(
                {
                    "chroma_id": record_id,
                    "review": page["documents"][index],
                    **(page["metadatas"][index] or {}),
                }
            )
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(records)
    if output.suffix.lower() == ".csv":
        df.to_csv(output, index=False, encoding="utf-8-sig")
    else:
        df.to_excel(output, index=False)
    return len(records)


def _where_filter(expression: str | None) -> dict[str, Any] | None:
    """Interpretar un filtro JSON recibido por línea de comandos.

    Parameters
    ----------
    expression : str or None
        Objeto JSON serializado.

    Returns
    -------
    dict[str, Any] or None
        Filtro listo para Chroma o ``None``.

    Raises
    ------
    json.JSONDecodeError
        Si el texto no es JSON válido.
    ValueError
        Si el JSON no representa un objeto.
    """
    if not expression:
        return None
    parsed = json.loads(expression)
    if not isinstance(parsed, dict):
        raise ValueError("--where debe ser un objeto JSON.")
    return parsed


def parse_args() -> argparse.Namespace:
    """Analizar los argumentos de la interfaz de línea de comandos.

    Returns
    -------
    argparse.Namespace
        Comando y opciones validadas.
    """
    parser = argparse.ArgumentParser(description="Administra las reseñas en Chroma.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    load_parser = subparsers.add_parser("load")
    load_parser.add_argument("--input", default=DEFAULT_INPUT)
    load_parser.add_argument("--batch-size", type=int, default=DEFAULT_UPSERT_BATCH_SIZE)

    query_parser = subparsers.add_parser("query")
    query_parser.add_argument("query")
    query_parser.add_argument("--limit", type=int, default=10)
    query_parser.add_argument("--where", default=None, help="Filtro Chroma como JSON.")

    subparsers.add_parser("stats")

    export_parser = subparsers.add_parser("export")
    export_parser.add_argument("--output", default="output/chroma_export.xlsx")
    return parser.parse_args()


def main() -> None:
    """Ejecutar la operación de Chroma seleccionada en la CLI.

    Returns
    -------
    None
        La función comunica el resultado por la salida estándar.
    """
    args = parse_args()
    if args.command == "load":
        count = upsert_reviews(args.input, batch_size=args.batch_size)
        print(f"{count} reseñas almacenadas en Chroma.")
    elif args.command == "query":
        print(
            json.dumps(
                semantic_search(
                    args.query,
                    n_results=args.limit,
                    where=_where_filter(args.where),
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.command == "stats":
        collection = get_collection()
        print(
            json.dumps(
                {"collection": collection.name, "records": collection.count()},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        count = export_collection(args.output)
        print(f"{count} reseñas exportadas a {args.output}.")


if __name__ == "__main__":
    main()
