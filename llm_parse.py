# -*- coding: utf-8 -*-
"""
llm_parse.py — Clasificación de reseñas usando la Responses API de OpenAI (JSON -> DataFrame -> Excel)

Autores
-------
Francisco Gonzalez
Vincent Martinez

Fecha de creación
----------------
2025

Resumen
-------
Este módulo procesa un CSV de reseñas (columna por defecto 'body'), envía cada reseña a la Responses API
de OpenAI usando un system prompt controlado, y guarda los resultados en un archivo Excel. El modelo
debe devolver exclusivamente un objeto JSON con tres campos obligatorios:
  - sentiment_label: "Positiva" | "Negativa"
  - general_category: una etiqueta de la lista GENERAL
  - specific_category: una etiqueta válida de la lista ESPECÍFICAS (según polaridad)

Características principales
---------------------------
- Lector CSV con pandas.
- Cliente OpenAI (openai.OpenAI) configurado mediante la variable de entorno OPENAI_API_KEY.
- Envío de prompt de sistema + prompt de usuario (reseña).
- Parsing robusto del texto de respuesta a JSON (con varios fallbacks).
- Validación de campos obligatorios.
- Reintentos con backoff exponencial configurable.
- Exportación final a Excel (openpyxl).

Requisitos
----------
- Python 3.8+
- Paquetes: openai (OpenAI Python client), pandas, openpyxl, python-dotenv (opcional)
  Instalación: pip install openai pandas openpyxl python-dotenv

Variables / Rutas configurables (constantes del módulo)
------------------------------------------------------
- INPUT_CSV: ruta del CSV de entrada (por defecto "output/resenas_combinadas.csv")
  - El CSV debe contener, como mínimo, la columna TEXT_COLUMN (por defecto "body").
- TEXT_COLUMN: nombre de la columna que contiene el texto de la reseña.
- ID_COLUMN: columna opcional con identificadores de reseñas (ej. "review_id").
- OUTPUT_XLSX: ruta del archivo Excel de salida.
- MODEL_NAME: nombre del modelo a usar (p. ej. "gpt-5").
- MAX_RETRIES / INITIAL_BACKOFF: controlan los reintentos y el backoff exponencial.

Seguridad y configuración de la API
-----------------------------------
- La clave de API debe estar en la variable de entorno OPENAI_API_KEY.
  - Windows (ej.): setx OPENAI_API_KEY "sk-..."
  - macOS/Linux (ej.): export OPENAI_API_KEY="sk-..."
- Se puede usar un archivo .env en el directorio de trabajo; python-dotenv carga automáticamente las variables.

Formato esperado de la respuesta del modelo
------------------------------------------
- El modelo debe responder únicamente con JSON válido, sin texto adicional.
- Estructura obligatoria:
  {
    "sentiment_label": "Positiva" | "Negativa",
    "general_category": "<etiqueta GENERAL>",
    "specific_category": "<etiqueta específica válida para la polaridad>"
  }
- Si el modelo devuelve texto extra (comentarios, Markdown), el módulo intentará extraer el primer bloque JSON
  o fallará con una excepción descriptiva.

Manejo de errores y fallbacks
-----------------------------
- El cliente implementa reintentos con backoff exponencial (controlados por MAX_RETRIES e INITIAL_BACKOFF).
- Si la respuesta no puede parsearse a JSON o faltan campos, se captura el error y la fila se registra con
  campos nulos y una clave "error" con el mensaje, para mantener trazabilidad en el Excel de salida.
- Errores fatales (como ausencia de OPENAI_API_KEY o inexistencia del CSV de entrada) lanzan excepciones
  para que el proceso termine con diagnóstico claro.

Salida / columnas en el Excel resultante
----------------------------------------
Cada fila de salida contendrá, como mínimo:
- review_id (si INPUT CSV tiene ID_COLUMN)
- review (texto original)
- sentiment_label
- general_category
- specific_category
Además, si existen en el CSV original, se copian columnas comunes: author, rating, date_published, location,
source_url, title, company. Se recomienda revisar el Excel resultante para validar la calidad del parsing.

Cómo usar
--------
1. Preparar el CSV de entrada en INPUT_CSV con una columna que contenga la reseña (TEXT_COLUMN).
2. Exportar/definir OPENAI_API_KEY en el entorno.
3. Ejecutar: python llm_parse.py
   - Para tests locales o depuración, se puede reducir MAX_RETRIES o cambiar MODEL_NAME.
4. Revisar el archivo OUTPUT_XLSX generado.

Consideraciones prácticas
-------------------------
- Coste y latencia: llamar a la API por cada reseña tiene coste y latencia. Para lotes grandes, considerar
  batching o límites de tasa (throttling).
- Auditoría: se conserva la reseña en el resultado para trazabilidad. Guardar también la respuesta cruda si
  se necesita auditar decisiones del modelo.
- Consistencia: el system prompt fuerza formato JSON estricto; aun así, conviene revisar muestras para validar
  que las etiquetas devueltas respetan las listas permitidas.

Extendiendo el script
---------------------
- Añadir caching local de respuestas para evitar reconsultas.
- Paralelizar llamadas respetando límites de la API.
- Añadir logging a fichero en lugar de prints.
- Añadir validación más estricta de las etiquetas devueltas frente a las listas permitidas.

Licencia y privacidad
---------------------
- No incluir datos sensibles ni PII en las reseñas si la política de privacidad lo prohíbe.
- Este script es una plantilla; adaptar su uso a la normativa de protección de datos aplicable.

"""

import os
import re
import time
import json
import pandas as pd
from pathlib import Path
from typing import Dict, Any, Optional
from dotenv import load_dotenv
from openai import OpenAI
from logger_library import setup_logger

load_dotenv()
# -----------------------------
# Configuración
# -----------------------------
INPUT_CSV = "output/resenas_combinadas.csv"      # CSV generado por tu scraper (columna 'body')
TEXT_COLUMN = "body"                   # Columna con el texto de la reseña
ID_COLUMN = "review_id"               # Opcional, si existe en tu CSV
OUTPUT_XLSX = "output/resenas_clasificadas.xlsx"


MODEL_NAME = "gpt-5"                # Ajusta si usas otra variante (p.ej. "gpt-5.1")
MAX_RETRIES = 4
INITIAL_BACKOFF = 2.0

# -----------------------------
# System Prompt (cargado desde archivo .md para evitar hardcode)
# -----------------------------
SYSTEM_PROMPT_FILENAME = "llm_parse_system_prompt.md"


def load_system_prompt(prompt_path: Optional[str] = None) -> str:
    """Carga el system prompt desde un archivo markdown.

    Por defecto, carga `llm_parse_system_prompt.md` que vive junto a este script.
    Se puede sobreescribir con la variable de entorno `LLM_PARSE_SYSTEM_PROMPT_PATH`
    o pasando `prompt_path` explícitamente.
    """
    env_path = os.getenv("LLM_PARSE_SYSTEM_PROMPT_PATH")
    chosen = prompt_path or env_path

    base_dir = Path(__file__).resolve().parent
    path = Path(chosen) if chosen else (base_dir / SYSTEM_PROMPT_FILENAME)
    if not path.is_absolute():
        path = (base_dir / path).resolve()

    if not path.exists():
        raise FileNotFoundError(
            f"No se encontró el system prompt en: {path}. "
            f"Crea el archivo '{SYSTEM_PROMPT_FILENAME}' o define LLM_PARSE_SYSTEM_PROMPT_PATH."
        )

    return path.read_text(encoding="utf-8").strip()


SYSTEM_PROMPT = load_system_prompt()


# -----------------------------
# Cliente OpenAI
# -----------------------------
def get_openai_client() -> OpenAI:
    """
    Inicializa y devuelve un cliente de OpenAI configurado.
    
    La función busca la clave API en la variable de entorno OPENAI_API_KEY.
    Si existe un archivo .env en el directorio de trabajo, las variables se
    cargan automáticamente mediante load_dotenv().
    
    Returns
    -------
    OpenAI
        Cliente de OpenAI configurado y listo para usar.
    
    Raises
    ------
    RuntimeError
        Si la variable de entorno OPENAI_API_KEY no está definida.
    
    Examples
    --------
    >>> client = get_openai_client()
    >>> # El cliente está listo para hacer llamadas a la API
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Falta la variable de entorno OPENAI_API_KEY. Añádela en .env o exporta la variable.")
    return OpenAI(api_key=api_key)


def call_openai_json(review_text: str, client: OpenAI) -> Dict[str, Any]:
    """
    Llama a la Responses API de OpenAI para clasificar una reseña.
    
    Envía el texto de la reseña al modelo especificado en MODEL_NAME junto con
    el SYSTEM_PROMPT que define el formato de clasificación esperado. Implementa
    un mecanismo de reintentos con backoff exponencial para manejar errores
    transitorios de la API.
    
    Parameters
    ----------
    review_text : str
        Texto de la reseña a clasificar.
    client : OpenAI
        Cliente de OpenAI previamente configurado.
    
    Returns
    -------
    Dict[str, Any]
        Diccionario con las claves:
        - sentiment_label: "Positiva" o "Negativa"
        - general_category: categoría general de la reseña
        - specific_category: categoría específica según la polaridad
    
    Raises
    ------
    RuntimeError
        Si después de MAX_RETRIES intentos no se obtiene una respuesta válida.
    ValueError
        Si la respuesta del modelo no puede parsearse a JSON válido.
    
    Notes
    -----
    - El backoff inicial es INITIAL_BACKOFF segundos y se duplica en cada
      reintento hasta un máximo de 30 segundos.
    - La función intenta parsear la respuesta de forma robusta, extrayendo
      JSON incluso si viene acompañado de texto adicional.
    
    Examples
    --------
    >>> client = get_openai_client()
    >>> result = call_openai_json("Excelente servicio, muy rápido", client)
    >>> print(result['sentiment_label'])
    'Positiva'
    """
    last_err = None
    backoff = INITIAL_BACKOFF

    for _ in range(MAX_RETRIES):
        try:
            resp = client.responses.create(
                model=MODEL_NAME,
                reasoning={"effort": "low"},
                input=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    # Si quieres añadir un developer prompt (ej. "Talk like a pirate."),
                    # añade aquí otro dict con role="developer".
                    {"role": "user", "content": review_text},
                ],
            )

            # En tu ejemplo: response.output_text
            raw = getattr(resp, "output_text", None)
            if raw is None:
                # Algunas versiones devuelven en resp.output[0].content[0].text
                # Fallback genérico:
                raw = str(resp)

            data = coerce_to_json(raw)
            validate_fields(data)
            return data

        except Exception as e:
            last_err = e
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)

    raise RuntimeError(f"Fallo clasificando reseña tras reintentos: {last_err}")


def coerce_to_json(s: str) -> Dict[str, Any]:
    """
    Convierte una cadena de texto a un diccionario JSON de forma robusta.
    
    Intenta parsear el texto directamente como JSON. Si falla, busca el primer
    bloque {...} en el texto (útil cuando el modelo devuelve JSON envuelto en
    texto adicional o marcadores Markdown) y lo parsea.
    
    Parameters
    ----------
    s : str
        Cadena de texto que debería contener JSON válido.
    
    Returns
    -------
    Dict[str, Any]
        Diccionario parseado desde el JSON.
    
    Raises
    ------
    ValueError
        Si no se puede extraer ni parsear JSON válido del texto.
    
    Notes
    -----
    La función implementa múltiples estrategias de parsing:
    1. Parse directo con json.loads()
    2. Extracción de primer bloque {...} usando regex
    3. Si ambos fallan, lanza ValueError con los primeros 300 caracteres
    
    Examples
    --------
    >>> coerce_to_json('{"sentiment_label": "Positiva"}')
    {'sentiment_label': 'Positiva'}
    >>> coerce_to_json('Aquí está el resultado: {"sentiment_label": "Negativa"}')
    {'sentiment_label': 'Negativa'}
    """
    s = s.strip()

    # Intento directo
    try:
        return json.loads(s)
    except Exception:
        pass

    # Extraer el primer bloque {...} más externo
    # Captura greedy para incluir anidados, luego valida
    m = re.search(r"\{.*\}", s, flags=re.DOTALL)
    if m:
        candidate = m.group(0)
        try:
            return json.loads(candidate)
        except Exception:
            pass

    # Último recurso: componer un dict mínimo si el modelo devolvió claves como líneas
    # (no debería ocurrir con el prompt, pero dejamos este fallback)
    raise ValueError(f"No se pudo parsear a JSON: {s[:300]}...")


def validate_fields(d: Dict[str, Any]) -> None:
    """
    Valida que un diccionario contenga todos los campos obligatorios.
    
    Verifica que el diccionario parseado desde la respuesta del modelo contenga
    las tres claves requeridas: sentiment_label, general_category y specific_category.
    
    Parameters
    ----------
    d : Dict[str, Any]
        Diccionario a validar.
    
    Raises
    ------
    ValueError
        Si faltan uno o más campos obligatorios. El mensaje incluye
        la lista de campos faltantes.
    
    Notes
    -----
    Esta función no valida los valores de los campos, solo su presencia.
    
    Examples
    --------
    >>> validate_fields({"sentiment_label": "Positiva", 
    ...                  "general_category": "Entrega",
    ...                  "specific_category": "Entrega puntual"})
    # No lanza excepción
    
    >>> validate_fields({"sentiment_label": "Positiva"})
    ValueError: Faltan campos obligatorios: {'general_category', 'specific_category'}
    """
    required = {"sentiment_label", "general_category", "specific_category"}
    missing = required - set(d.keys())
    if missing:
        raise ValueError(f"Faltan campos obligatorios: {missing}")


# -----------------------------
# Carga CSV, clasificación y guardado
# -----------------------------
def main(
    input_csv: str = INPUT_CSV,
    output_xlsx: str = OUTPUT_XLSX,
    max_rows: int | None = None,
):
    """
    Función principal que ejecuta el pipeline completo de clasificación.
    
    Ejecuta el proceso completo:
    1. Carga el CSV de reseñas desde INPUT_CSV
    2. Valida que exista la columna TEXT_COLUMN
    3. Inicializa el cliente de OpenAI
    4. Para cada reseña:
       - Llama a la API de OpenAI para clasificarla
       - Maneja errores y registra el resultado
       - Preserva columnas originales del CSV
    5. Genera un DataFrame con todos los resultados
    6. Exporta a Excel en OUTPUT_XLSX
    
    Raises
    ------
    FileNotFoundError
        Si el archivo INPUT_CSV no existe.
    KeyError
        Si el CSV no contiene la columna TEXT_COLUMN.
    RuntimeError
        Si falta la configuración de OPENAI_API_KEY.
    
    Notes
    -----
    - Las reseñas vacías se omiten del procesamiento.
    - Si una clasificación falla después de MAX_RETRIES, se registra el error
      en el resultado pero el proceso continúa con las demás reseñas.
    - Se preservan columnas adicionales del CSV original: author, rating,
      date_published, location, source_url, title, company.
    - El logger registra el progreso y errores durante la ejecución.
    
    Examples
    --------
    Para ejecutar el script:
    
    >>> if __name__ == "__main__":
    ...     main()
    """
    # Cargar reseñas
    logger = setup_logger("llm_parse")
    if not os.path.exists(input_csv):
        raise FileNotFoundError(f"No se encontró el CSV de entrada: {input_csv}")

    df = pd.read_csv(input_csv)
    if max_rows is not None:
        if max_rows < 1:
            raise ValueError("max_rows debe ser >= 1")
        df = df.head(max_rows)
    if TEXT_COLUMN not in df.columns:
        raise KeyError(f"El CSV debe contener la columna '{TEXT_COLUMN}' con el texto de la reseña.")

    client = get_openai_client()

    results = []
    for idx, row in df.iterrows():
        review_text = str(row.get(TEXT_COLUMN, "")).strip()
        if not review_text:
            continue

        try:
            data = call_openai_json(review_text, client)
            logger.info(f"Fila {idx} (ID: {row.get(ID_COLUMN, 'N/A')}): Clasificada correctamente.")
        except Exception as e:
            logger.info(f"Error en fila {idx} (ID: {row.get(ID_COLUMN, 'N/A')}): {e}")
            # Si algo falla, registramos un resultado con error para que no se pierda la fila
            data = {
                "sentiment_label": None,
                "general_category": None,
                "specific_category": None,
                "error": str(e),
            }

        # Enlazar con campos originales útiles (id/fecha/origen si están)
        enriched = {
            "review_id": row.get(ID_COLUMN) if ID_COLUMN in df.columns else None,
            "review": review_text,
            "sentiment_label": data.get("sentiment_label"),
            "general_category": data.get("general_category"),
            "specific_category": data.get("specific_category"),
            # Copiamos la reseña devuelta por el modelo por trazabilidad (puede coincidir con la original)
        }

        # Copia extra de columnas originales si quieres mantenerlas (fecha, rating, author, etc.)
        for col in ["author", "rating", "date_published", "location", "source_url", "title","company"]:
            if col in df.columns:
                enriched[col] = row.get(col)

        results.append(enriched)

    out_df = pd.DataFrame(results)

    # Guardar a Excel (openpyxl)
    out_dir = os.path.dirname(output_xlsx)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    out_df.to_excel(output_xlsx, index=False)
    logger.info(f"Listo. Guardado en: {output_xlsx}\nFilas: {len(out_df)}")
    
   

if __name__ == "__main__":
    main()
