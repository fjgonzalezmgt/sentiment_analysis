# -*- coding: utf-8 -*-
"""Clasificación estructurada de reseñas con OpenAI Responses y Batch APIs.

El modo síncrono es útil para pruebas y volúmenes pequeños. Para producción,
``batch-submit`` crea un Batch de OpenAI y ``batch-finalize`` descarga y combina
los resultados cuando el lote termina.

Author
------
Francisco Gonzalez
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, ConfigDict

from logger_library import setup_logger

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env.local")
load_dotenv(BASE_DIR / ".env")

INPUT_CSV = "output/resenas_combinadas.csv"
OUTPUT_XLSX = "output/resenas_clasificadas.xlsx"
OUTPUT_CSV = "output/resenas_clasificadas.csv"
TEXT_COLUMN = "body"
ID_COLUMN = "review_id"
SYSTEM_PROMPT_FILENAME = "llm_parse_system_prompt.md"

# Luna es la variante GPT-5.6 adecuada para clasificación repetitiva de alto
# volumen. El valor se puede cambiar por gpt-5.6-terra o gpt-5.6-sol mediante .env.
MODEL_NAME = os.getenv("OPENAI_CLASSIFICATION_MODEL", "gpt-5.6-luna")
REASONING_EFFORT = os.getenv("OPENAI_REASONING_EFFORT", "none")
MAX_OUTPUT_TOKENS = int(os.getenv("OPENAI_MAX_OUTPUT_TOKENS", "256"))

BATCH_STATE_PATH = "output/openai_batch_state.json"
BATCH_INPUT_PATH = "output/openai_batch_requests.jsonl"
BATCH_MANIFEST_PATH = "output/openai_batch_manifest.json"

GENERAL_CATEGORIES = (
    "Entrega",
    "Recogida y logística inversa",
    "Seguimiento y comunicación",
    "Servicio al cliente",
    "Compensación y reembolso",
    "Calidad del producto entregado",
    "Repartidor",
    "Experiencia general",
    "Valor percibido",
    "Fidelización",
    "Responsabilidad y recuperación",
)

NEGATIVE_CATEGORIES = (
    "Falta de entrega",
    "Retraso en la entrega",
    "Entrega en dirección incorrecta",
    "Entrega sin aviso o contacto",
    "Entrega dañada",
    "Entrega fuera de horario o zona",
    "No se presentó a recoger",
    "Retraso en recogida",
    "Problemas con punto de recogida",
    "Seguimiento incorrecto o sin actualizar",
    "Comunicación inexistente o deficiente",
    "Información confusa o contradictoria",
    "Falta de respuesta a reclamaciones",
    "Atención poco profesional o grosera",
    "Derivación o evasión de responsabilidad",
    "No reembolsan producto o envío",
    "Procesos de reclamo ineficaces",
    "Daño físico al producto",
    "Contenido incompleto o perdido",
    "Repartidor poco profesional",
    "Proceso ineficiente o burocrático",
    "Costo excesivo frente a servicio",
    "Empresa no confiable",
    "Otros",
)

POSITIVE_CATEGORIES = (
    "Entrega puntual",
    "Entrega rápida",
    "Entrega correcta",
    "Entrega en buenas condiciones",
    "Entrega flexible o conveniente",
    "Buen seguimiento",
    "Comunicación efectiva",
    "Aviso previo o confirmación",
    "Atención rápida y resolutiva",
    "Atención amable o profesional",
    "Buena gestión de reclamaciones",
    "Repartidor amable o educado",
    "Repartidor puntual o responsable",
    "Repartidor proactivo",
    "Servicio confiable",
    "Satisfacción general",
    "Profesionalismo",
    "Rapidez de respuesta",
    "Buena relación calidad-precio",
    "Expectativas superadas",
    "Recomendación a otros",
    "Repetición de compra o uso",
    "Resolución satisfactoria de errores",
    "Compromiso con el cliente",
    "Otros",
)


class ReviewClassification(BaseModel):
    """Contrato estricto entre OpenAI y el pipeline."""

    model_config = ConfigDict(extra="forbid")

    sentiment_label: Literal["Positiva", "Negativa"]
    general_category: Literal[
        "Entrega",
        "Recogida y logística inversa",
        "Seguimiento y comunicación",
        "Servicio al cliente",
        "Compensación y reembolso",
        "Calidad del producto entregado",
        "Repartidor",
        "Experiencia general",
        "Valor percibido",
        "Fidelización",
        "Responsabilidad y recuperación",
    ]
    specific_category: Literal[
        "Falta de entrega",
        "Retraso en la entrega",
        "Entrega en dirección incorrecta",
        "Entrega sin aviso o contacto",
        "Entrega dañada",
        "Entrega fuera de horario o zona",
        "No se presentó a recoger",
        "Retraso en recogida",
        "Problemas con punto de recogida",
        "Seguimiento incorrecto o sin actualizar",
        "Comunicación inexistente o deficiente",
        "Información confusa o contradictoria",
        "Falta de respuesta a reclamaciones",
        "Atención poco profesional o grosera",
        "Derivación o evasión de responsabilidad",
        "No reembolsan producto o envío",
        "Procesos de reclamo ineficaces",
        "Daño físico al producto",
        "Contenido incompleto o perdido",
        "Repartidor poco profesional",
        "Proceso ineficiente o burocrático",
        "Costo excesivo frente a servicio",
        "Empresa no confiable",
        "Entrega puntual",
        "Entrega rápida",
        "Entrega correcta",
        "Entrega en buenas condiciones",
        "Entrega flexible o conveniente",
        "Buen seguimiento",
        "Comunicación efectiva",
        "Aviso previo o confirmación",
        "Atención rápida y resolutiva",
        "Atención amable o profesional",
        "Buena gestión de reclamaciones",
        "Repartidor amable o educado",
        "Repartidor puntual o responsable",
        "Repartidor proactivo",
        "Servicio confiable",
        "Satisfacción general",
        "Profesionalismo",
        "Rapidez de respuesta",
        "Buena relación calidad-precio",
        "Expectativas superadas",
        "Recomendación a otros",
        "Repetición de compra o uso",
        "Resolución satisfactoria de errores",
        "Compromiso con el cliente",
        "Otros",
    ]

    def validate_taxonomy(self) -> "ReviewClassification":
        """Validar la coherencia entre sentimiento y categoría específica.

        Returns
        -------
        ReviewClassification
            La misma instancia cuando la taxonomía es coherente.

        Raises
        ------
        ValueError
            Si la categoría específica no pertenece a la polaridad indicada.
        """
        allowed = (
            POSITIVE_CATEGORIES
            if self.sentiment_label == "Positiva"
            else NEGATIVE_CATEGORIES
        )
        if self.specific_category not in allowed:
            raise ValueError(
                f"'{self.specific_category}' no corresponde a "
                f"{self.sentiment_label.lower()}."
            )
        return self


def load_system_prompt(prompt_path: str | None = None) -> str:
    """Cargar el prompt del sistema versionado junto al código.

    Parameters
    ----------
    prompt_path : str or None, default=None
        Ruta explícita del prompt.

    Returns
    -------
    str
        Contenido del prompt sin espacios exteriores.

    Raises
    ------
    FileNotFoundError
        Si el archivo configurado no existe.
    """
    chosen = prompt_path or os.getenv("LLM_PARSE_SYSTEM_PROMPT_PATH")
    path = Path(chosen) if chosen else BASE_DIR / SYSTEM_PROMPT_FILENAME
    if not path.is_absolute():
        path = (BASE_DIR / path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"No se encontró el system prompt: {path}")
    return path.read_text(encoding="utf-8").strip()


SYSTEM_PROMPT = load_system_prompt()


def get_openai_client() -> OpenAI:
    """Crear el cliente de OpenAI con resiliencia centralizada.

    Returns
    -------
    OpenAI
        Cliente configurado con timeout y reintentos.

    Raises
    ------
    RuntimeError
        Si ``OPENAI_API_KEY`` no está disponible.
    """
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError(
            "Falta OPENAI_API_KEY. Configúrala en el entorno o en un .env ignorado."
        )
    return OpenAI(
        max_retries=int(os.getenv("OPENAI_MAX_RETRIES", "5")),
        timeout=float(os.getenv("OPENAI_TIMEOUT_SECONDS", "60")),
    )


def _extract_parsed(response: Any) -> ReviewClassification:
    """Extraer y validar una salida estructurada de Responses API.

    Parameters
    ----------
    response : Any
        Respuesta devuelta por el SDK de OpenAI.

    Returns
    -------
    ReviewClassification
        Clasificación validada.

    Raises
    ------
    RuntimeError
        Si el modelo rechaza la entrada o no devuelve contenido estructurado.
    ValueError
        Si la taxonomía es incoherente.
    """
    parsed = getattr(response, "output_parsed", None)
    if parsed is not None:
        return ReviewClassification.model_validate(parsed).validate_taxonomy()

    for output in getattr(response, "output", []):
        if getattr(output, "type", None) != "message":
            continue
        for content in getattr(output, "content", []):
            refusal = getattr(content, "refusal", None)
            if refusal:
                raise RuntimeError(f"OpenAI rechazó la clasificación: {refusal}")
            item_parsed = getattr(content, "parsed", None)
            if item_parsed is not None:
                return ReviewClassification.model_validate(
                    item_parsed
                ).validate_taxonomy()
    raise RuntimeError("La respuesta no contiene una clasificación estructurada.")


def call_openai_json(
    review_text: str,
    client: OpenAI,
    *,
    model: str = MODEL_NAME,
    reasoning_effort: str = REASONING_EFFORT,
) -> dict[str, str]:
    """Clasificar una reseña mediante Responses API y Structured Outputs.

    Parameters
    ----------
    review_text : str
        Texto de la reseña.
    client : OpenAI
        Cliente autenticado.
    model : str, default=MODEL_NAME
        Modelo GPT-5.6 de la solicitud.
    reasoning_effort : str, default=REASONING_EFFORT
        Nivel de razonamiento.

    Returns
    -------
    dict[str, str]
        Clasificación validada y serializable.
    """
    response = client.responses.parse(
        model=model,
        reasoning={"effort": reasoning_effort},
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": review_text},
        ],
        text_format=ReviewClassification,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        store=False,
        prompt_cache_key="sentiment-classifier-v2",
    )
    return _extract_parsed(response).model_dump()


def validate_fields(data: dict[str, Any]) -> None:
    """Validar el esquema y la coherencia taxonómica de un resultado.

    Parameters
    ----------
    data : dict[str, Any]
        Clasificación que se desea validar.

    Returns
    -------
    None
        La función no retorna datos.

    Raises
    ------
    ValueError
        Si los valores no satisfacen el esquema o la taxonomía.
    """
    ReviewClassification.model_validate(data).validate_taxonomy()


def _json_safe(value: Any) -> Any:
    """Convertir un valor de pandas a una representación JSON segura.

    Parameters
    ----------
    value : Any
        Valor original.

    Returns
    -------
    Any
        Valor nativo, texto ISO, ``None`` o representación de cadena.
    """
    if value is None or (not isinstance(value, (list, dict)) and pd.isna(value)):
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool, list, dict)):
        return value
    return str(value)


def _load_reviews(input_csv: str, max_rows: int | None = None) -> pd.DataFrame:
    """Leer y validar las reseñas que deben clasificarse.

    Parameters
    ----------
    input_csv : str
        Archivo CSV de entrada.
    max_rows : int or None, default=None
        Límite opcional de filas.

    Returns
    -------
    pandas.DataFrame
        Reseñas no vacías.

    Raises
    ------
    FileNotFoundError
        Si el archivo no existe.
    KeyError
        Si falta la columna de texto.
    ValueError
        Si ``max_rows`` es menor que uno.
    """
    path = Path(input_csv)
    if not path.exists():
        raise FileNotFoundError(f"No se encontró el CSV de entrada: {path}")
    df = pd.read_csv(path)
    if TEXT_COLUMN not in df.columns:
        raise KeyError(f"El CSV debe contener la columna '{TEXT_COLUMN}'.")
    if max_rows is not None:
        if max_rows < 1:
            raise ValueError("max_rows debe ser >= 1")
        df = df.head(max_rows)
    return df[df[TEXT_COLUMN].fillna("").astype(str).str.strip().ne("")].copy()


def _enrich_row(row: pd.Series, classification: dict[str, Any]) -> dict[str, Any]:
    """Combinar una fila original con su clasificación.

    Parameters
    ----------
    row : pandas.Series
        Registro original.
    classification : dict[str, Any]
        Resultado del modelo o información de error.

    Returns
    -------
    dict[str, Any]
        Registro enriquecido y serializable.
    """
    result = {column: _json_safe(value) for column, value in row.items()}
    result["review"] = str(row.get(TEXT_COLUMN, "")).strip()
    result.update(classification)
    return result


def _save_results(
    records: list[dict[str, Any]],
    output_xlsx: str,
    output_csv: str,
) -> pd.DataFrame:
    """Guardar resultados en Excel y CSV.

    Parameters
    ----------
    records : list[dict[str, Any]]
        Registros clasificados.
    output_xlsx : str
        Ruta del archivo Excel.
    output_csv : str
        Ruta del archivo CSV.

    Returns
    -------
    pandas.DataFrame
        DataFrame escrito en ambos destinos.
    """
    out_df = pd.DataFrame(records)
    for path_value in (output_xlsx, output_csv):
        Path(path_value).parent.mkdir(parents=True, exist_ok=True)
    out_df.to_excel(output_xlsx, index=False)
    out_df.to_csv(output_csv, index=False, encoding="utf-8-sig")
    return out_df


def classify_sync(
    input_csv: str = INPUT_CSV,
    output_xlsx: str = OUTPUT_XLSX,
    output_csv: str = OUTPUT_CSV,
    max_rows: int | None = None,
    *,
    model: str = MODEL_NAME,
) -> pd.DataFrame:
    """Clasificar reseñas inmediatamente mediante Responses API.

    Parameters
    ----------
    input_csv : str, default=INPUT_CSV
        Archivo CSV de entrada.
    output_xlsx : str, default=OUTPUT_XLSX
        Archivo Excel de salida.
    output_csv : str, default=OUTPUT_CSV
        Archivo CSV de salida.
    max_rows : int or None, default=None
        Límite opcional de reseñas.
    model : str, default=MODEL_NAME
        Modelo GPT-5.6 para la clasificación.

    Returns
    -------
    pandas.DataFrame
        Resultados y errores por fila.
    """
    logger = setup_logger("llm_parse.sync")
    df = _load_reviews(input_csv, max_rows)
    client = get_openai_client()
    records: list[dict[str, Any]] = []

    for index, row in df.iterrows():
        try:
            classification = call_openai_json(str(row[TEXT_COLUMN]), client, model=model)
            logger.info("Fila %s clasificada correctamente.", index)
        except Exception as exc:
            logger.exception("Fallo al clasificar la fila %s.", index)
            classification = {
                "sentiment_label": None,
                "general_category": None,
                "specific_category": None,
                "classification_error": str(exc),
            }
        records.append(_enrich_row(row, classification))

    result = _save_results(records, output_xlsx, output_csv)
    logger.info("Resultados guardados: %s filas.", len(result))
    return result


def _custom_id(index: Any, row: pd.Series) -> str:
    """Generar el identificador de correlación de una solicitud Batch.

    Parameters
    ----------
    index : Any
        Índice original de la fila.
    row : pandas.Series
        Registro que se desea identificar.

    Returns
    -------
    str
        Identificador determinista de hasta 64 caracteres.
    """
    natural_key = "|".join(
        str(row.get(name, ""))
        for name in (ID_COLUMN, "company", "source_url", TEXT_COLUMN)
    )
    digest = hashlib.sha256(natural_key.encode("utf-8")).hexdigest()[:16]
    return f"review-{index}-{digest}"[:64]


def _batch_request_body(review_text: str, model: str) -> dict[str, Any]:
    """Construir el cuerpo de una solicitud individual del Batch.

    Parameters
    ----------
    review_text : str
        Texto de la reseña.
    model : str
        Modelo GPT-5.6 elegido.

    Returns
    -------
    dict[str, Any]
        Cuerpo compatible con ``POST /v1/responses``.
    """
    return {
        "model": model,
        "reasoning": {"effort": REASONING_EFFORT},
        "input": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": review_text},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "review_classification",
                "strict": True,
                "schema": ReviewClassification.model_json_schema(),
            }
        },
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "store": False,
        "prompt_cache_key": "sentiment-classifier-v2",
    }


def prepare_batch_files(
    input_csv: str = INPUT_CSV,
    batch_input_path: str = BATCH_INPUT_PATH,
    manifest_path: str = BATCH_MANIFEST_PATH,
    max_rows: int | None = None,
    *,
    model: str = MODEL_NAME,
) -> tuple[Path, Path, int]:
    """Generar el JSONL y manifiesto de un Batch sin llamar a la API.

    Parameters
    ----------
    input_csv : str, default=INPUT_CSV
        Archivo CSV con reseñas.
    batch_input_path : str, default=BATCH_INPUT_PATH
        Destino del JSONL.
    manifest_path : str, default=BATCH_MANIFEST_PATH
        Destino del mapa entre solicitudes y filas.
    max_rows : int or None, default=None
        Límite opcional de filas.
    model : str, default=MODEL_NAME
        Modelo incluido en cada solicitud.

    Returns
    -------
    tuple[pathlib.Path, pathlib.Path, int]
        Ruta JSONL, ruta del manifiesto y número de solicitudes.
    """
    df = _load_reviews(input_csv, max_rows)
    request_path = Path(batch_input_path)
    manifest_file = Path(manifest_path)
    request_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_file.parent.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, dict[str, Any]] = {}
    with request_path.open("w", encoding="utf-8", newline="\n") as stream:
        for index, row in df.iterrows():
            custom_id = _custom_id(index, row)
            manifest[custom_id] = {
                column: _json_safe(value) for column, value in row.items()
            }
            request = {
                "custom_id": custom_id,
                "method": "POST",
                "url": "/v1/responses",
                "body": _batch_request_body(str(row[TEXT_COLUMN]), model),
            }
            stream.write(json.dumps(request, ensure_ascii=False) + "\n")

    manifest_file.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return request_path, manifest_file, len(manifest)


def submit_batch(
    input_csv: str = INPUT_CSV,
    batch_input_path: str = BATCH_INPUT_PATH,
    manifest_path: str = BATCH_MANIFEST_PATH,
    state_path: str = BATCH_STATE_PATH,
    max_rows: int | None = None,
    *,
    model: str = MODEL_NAME,
) -> dict[str, Any]:
    """Subir solicitudes y crear un Batch de OpenAI.

    Parameters
    ----------
    input_csv : str, default=INPUT_CSV
        Archivo CSV con reseñas.
    batch_input_path : str, default=BATCH_INPUT_PATH
        JSONL que se creará y subirá.
    manifest_path : str, default=BATCH_MANIFEST_PATH
        Archivo local para reconstruir las filas.
    state_path : str, default=BATCH_STATE_PATH
        Archivo de estado del lote.
    max_rows : int or None, default=None
        Límite opcional de reseñas.
    model : str, default=MODEL_NAME
        Modelo GPT-5.6 del lote.

    Returns
    -------
    dict[str, Any]
        Estado inicial, identificadores y rutas del Batch.
    """
    logger = setup_logger("llm_parse.batch")
    request_path, manifest_file, row_count = prepare_batch_files(
        input_csv,
        batch_input_path,
        manifest_path,
        max_rows,
        model=model,
    )
    client = get_openai_client()
    with request_path.open("rb") as stream:
        uploaded = client.files.create(file=stream, purpose="batch")
    batch = client.batches.create(
        input_file_id=uploaded.id,
        endpoint="/v1/responses",
        completion_window="24h",
        metadata={"pipeline": "sentiment-analysis", "schema_version": "2"},
    )
    state = {
        "batch_id": batch.id,
        "status": batch.status,
        "input_file_id": uploaded.id,
        "input_csv": str(input_csv),
        "batch_input_path": str(request_path),
        "manifest_path": str(manifest_file),
        "model": model,
        "row_count": row_count,
    }
    state_file = Path(state_path)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("Batch %s enviado con %s reseñas.", batch.id, row_count)
    return state


def _load_state(state_path: str, batch_id: str | None = None) -> dict[str, Any]:
    """Cargar el estado local y resolver el ID del Batch.

    Parameters
    ----------
    state_path : str
        Ruta del archivo JSON de estado.
    batch_id : str or None, default=None
        ID que sustituye al guardado cuando se proporciona.

    Returns
    -------
    dict[str, Any]
        Estado con un ``batch_id`` válido.

    Raises
    ------
    ValueError
        Si no existe ningún ID disponible.
    """
    path = Path(state_path)
    state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if batch_id:
        state["batch_id"] = batch_id
    if not state.get("batch_id"):
        raise ValueError("Indica --batch-id o conserva el archivo de estado del envío.")
    return state


def batch_status(
    state_path: str = BATCH_STATE_PATH,
    batch_id: str | None = None,
) -> dict[str, Any]:
    """Consultar el estado actual de un Batch.

    Parameters
    ----------
    state_path : str, default=BATCH_STATE_PATH
        Archivo de estado local.
    batch_id : str or None, default=None
        ID explícito del Batch.

    Returns
    -------
    dict[str, Any]
        Estado, contadores y archivos producidos.
    """
    state = _load_state(state_path, batch_id)
    batch = get_openai_client().batches.retrieve(state["batch_id"])
    return {
        "batch_id": batch.id,
        "status": batch.status,
        "request_counts": getattr(batch, "request_counts", None),
        "output_file_id": getattr(batch, "output_file_id", None),
        "error_file_id": getattr(batch, "error_file_id", None),
    }


def _response_output_text(body: dict[str, Any]) -> str:
    """Extraer el texto de salida desde un objeto Response serializado.

    Parameters
    ----------
    body : dict[str, Any]
        Cuerpo de la respuesta incluido en el archivo Batch.

    Returns
    -------
    str
        Texto concatenado de los elementos ``output_text``.

    Raises
    ------
    RuntimeError
        Si el modelo rechazó la entrada.
    ValueError
        Si no existe texto de salida.
    """
    if body.get("output_text"):
        return str(body["output_text"])
    chunks: list[str] = []
    for output in body.get("output", []):
        if output.get("type") != "message":
            continue
        for content in output.get("content", []):
            if content.get("type") == "refusal":
                raise RuntimeError(f"OpenAI rechazó la entrada: {content.get('refusal')}")
            if content.get("type") == "output_text" and content.get("text"):
                chunks.append(str(content["text"]))
    if not chunks:
        raise ValueError("El resultado Batch no contiene output_text.")
    return "".join(chunks)


def _download_text(client: OpenAI, file_id: str) -> str:
    """Descargar un archivo de OpenAI como texto UTF-8.

    Parameters
    ----------
    client : OpenAI
        Cliente autenticado.
    file_id : str
        Identificador del archivo.

    Returns
    -------
    str
        Contenido textual.
    """
    content = client.files.content(file_id)
    text = getattr(content, "text", None)
    if isinstance(text, str):
        return text
    raw = content.read()
    return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)


def finalize_batch(
    state_path: str = BATCH_STATE_PATH,
    batch_id: str | None = None,
    output_xlsx: str = OUTPUT_XLSX,
    output_csv: str = OUTPUT_CSV,
) -> pd.DataFrame | None:
    """Descargar un Batch terminado y reconstruir el dataset tabular.

    Parameters
    ----------
    state_path : str, default=BATCH_STATE_PATH
        Archivo local con el estado y manifiesto.
    batch_id : str or None, default=None
        ID explícito del Batch.
    output_xlsx : str, default=OUTPUT_XLSX
        Archivo Excel de salida.
    output_csv : str, default=OUTPUT_CSV
        Archivo CSV de salida.

    Returns
    -------
    pandas.DataFrame or None
        Resultados reconstruidos o ``None`` mientras el lote sigue activo.

    Raises
    ------
    RuntimeError
        Si el Batch falla o termina sin archivos procesables.
    """
    logger = setup_logger("llm_parse.batch")
    state = _load_state(state_path, batch_id)
    client = get_openai_client()
    batch = client.batches.retrieve(state["batch_id"])
    state["status"] = batch.status
    Path(state_path).write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if batch.status != "completed":
        if batch.status in {"failed", "expired", "cancelled"}:
            raise RuntimeError(
                f"El Batch {batch.id} terminó en estado '{batch.status}': "
                f"{getattr(batch, 'errors', None)}"
            )
        logger.info("Batch %s todavía está en estado '%s'.", batch.id, batch.status)
        return None
    if not batch.output_file_id and not batch.error_file_id:
        raise RuntimeError("El Batch terminó sin archivos de salida ni de errores.")

    manifest_path = Path(state.get("manifest_path", BATCH_MANIFEST_PATH))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    output_by_id: dict[str, dict[str, Any]] = {}
    if batch.output_file_id:
        for line in _download_text(client, batch.output_file_id).splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            custom_id = item["custom_id"]
            error = item.get("error")
            response = item.get("response") or {}
            if error or int(response.get("status_code", 500)) >= 400:
                output_by_id[custom_id] = {
                    "sentiment_label": None,
                    "general_category": None,
                    "specific_category": None,
                    "classification_error": json.dumps(
                        error or response.get("body"), ensure_ascii=False
                    ),
                }
                continue
            try:
                raw = _response_output_text(response["body"])
                classification = ReviewClassification.model_validate_json(
                    raw
                ).validate_taxonomy()
                output_by_id[custom_id] = classification.model_dump()
            except Exception as exc:
                output_by_id[custom_id] = {
                    "sentiment_label": None,
                    "general_category": None,
                    "specific_category": None,
                    "classification_error": str(exc),
                }

    if batch.error_file_id:
        for line in _download_text(client, batch.error_file_id).splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            custom_id = item.get("custom_id")
            if custom_id and custom_id not in output_by_id:
                output_by_id[custom_id] = {
                    "sentiment_label": None,
                    "general_category": None,
                    "specific_category": None,
                    "classification_error": json.dumps(
                        item.get("error", item), ensure_ascii=False
                    ),
                }

    records = []
    for custom_id, raw_row in manifest.items():
        row = pd.Series(raw_row)
        classification = output_by_id.get(
            custom_id,
            {
                "sentiment_label": None,
                "general_category": None,
                "specific_category": None,
                "classification_error": "No se recibió resultado para esta reseña.",
            },
        )
        records.append(_enrich_row(row, classification))
    result = _save_results(records, output_xlsx, output_csv)
    logger.info("Batch %s finalizado: %s filas guardadas.", batch.id, len(result))
    return result


def main(
    input_csv: str = INPUT_CSV,
    output_xlsx: str = OUTPUT_XLSX,
    max_rows: int | None = None,
) -> pd.DataFrame:
    """Ejecutar la clasificación síncrona para compatibilidad.

    Parameters
    ----------
    input_csv : str, default=INPUT_CSV
        Archivo CSV de entrada.
    output_xlsx : str, default=OUTPUT_XLSX
        Archivo Excel de salida.
    max_rows : int or None, default=None
        Límite opcional de filas.

    Returns
    -------
    pandas.DataFrame
        Resultados clasificados.
    """
    output_csv = str(Path(output_xlsx).with_suffix(".csv"))
    return classify_sync(input_csv, output_xlsx, output_csv, max_rows)


def parse_args() -> argparse.Namespace:
    """Analizar argumentos de la interfaz de clasificación.

    Returns
    -------
    argparse.Namespace
        Modo y opciones validadas.
    """
    parser = argparse.ArgumentParser(
        description="Clasifica reseñas con Responses API o Batch API."
    )
    parser.add_argument(
        "--mode",
        choices=("sync", "batch-submit", "batch-status", "batch-finalize"),
        default="batch-submit",
    )
    parser.add_argument("--input-csv", default=INPUT_CSV)
    parser.add_argument("--output-xlsx", default=OUTPUT_XLSX)
    parser.add_argument("--output-csv", default=OUTPUT_CSV)
    parser.add_argument("--batch-id", default=None)
    parser.add_argument("--state-path", default=BATCH_STATE_PATH)
    parser.add_argument("--max-rows", type=int, default=None)
    parser.add_argument("--model", default=MODEL_NAME)
    return parser.parse_args()


def cli() -> None:
    """Ejecutar el modo solicitado desde la línea de comandos.

    Returns
    -------
    None
        La función escribe estados y resultados en la salida estándar.
    """
    args = parse_args()
    if args.mode == "sync":
        classify_sync(
            args.input_csv,
            args.output_xlsx,
            args.output_csv,
            args.max_rows,
            model=args.model,
        )
    elif args.mode == "batch-submit":
        state = submit_batch(
            args.input_csv,
            state_path=args.state_path,
            max_rows=args.max_rows,
            model=args.model,
        )
        print(json.dumps(state, ensure_ascii=False, indent=2))
    elif args.mode == "batch-status":
        print(
            json.dumps(
                batch_status(args.state_path, args.batch_id),
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
    else:
        result = finalize_batch(
            args.state_path,
            args.batch_id,
            args.output_xlsx,
            args.output_csv,
        )
        if result is None:
            print("El Batch aún no ha terminado; inténtalo de nuevo más tarde.")


if __name__ == "__main__":
    cli()
