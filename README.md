# Análisis profesional de reseñas con OpenAI GPT‑5.6 y Chroma

Pipeline reproducible para extraer reseñas, normalizarlas, clasificarlas con
salidas estructuradas y almacenarlas en Chroma para búsqueda semántica. Los
resultados también se conservan en CSV/Excel para Power BI.

## Arquitectura

```mermaid
flowchart LR
    A[Trustpilot] --> B[Scraping]
    B --> C[Pandas: limpieza]
    C --> D[OpenAI Batch API]
    D --> E[GPT-5.6 Luna + Structured Outputs]
    E --> F[CSV / Excel]
    F --> G[Chroma + embeddings]
    F --> H[Power BI]
    G --> I[Búsqueda semántica]
```

Decisiones principales:

- `gpt-5.6-luna` para clasificación de alto volumen; puede cambiarse a
  `gpt-5.6-terra` o `gpt-5.6-sol` mediante configuración.
- Batch API como modo de producción y Responses API síncrona para pruebas.
- Structured Outputs con Pydantic: la API aplica el esquema y el código valida
  además la coherencia entre polaridad y taxonomía.
- `store=False` para no conservar respuestas del clasificador en OpenAI.
- Chroma persistente local para desarrollo; `HttpClient` para un servidor de
  producción.
- `text-embedding-3-small` por defecto: es un modelo de embeddings, no un LLM,
  y es más apropiado que GPT‑5.6 para indexación vectorial.
- Upserts idempotentes y embeddings enviados por grupos.

## Instalación con Conda

```powershell
conda env create -f environment.yml
conda activate sentiment-analysis
playwright install chromium
```

La instalación de Chromium solo hace falta si se usa `--playwright`.

## Panel de análisis

El panel Streamlit combina las clasificaciones tabulares con la búsqueda
semántica persistida en Chroma. Usa por defecto
`output/resenas_clasificadas.xlsx` y permite seleccionar otro CSV o Excel
clasificado desde la barra lateral.

```powershell
conda run -n sentiment-analysis streamlit run dashboard.py
```

Incluye filtros por empresa, sentimiento, categoría, valoración y periodo;
comparativas Plotly; detalle de reseñas; y una consulta semántica a Chroma
restringida por los filtros activos. La búsqueda necesita la misma
configuración de Chroma y `OPENAI_API_KEY` que el pipeline.

Copiar la configuración de ejemplo y conservar la clave existente:

```powershell
Copy-Item .env.example .env.local
```

No sobrescribas tu `.env` actual. Tanto `.env` como `.env.local` están
ignorados por Git.

## Uso recomendado: Batch API

El flujo de producción tiene dos fases porque OpenAI procesa los lotes de forma
asíncrona.

### Empezar desde reseñas ya consolidadas

Si ya existe `output/resenas_combinadas.csv`, se puede omitir la lectura de
empresas, el scraping y la consolidación:

```powershell
conda run -n sentiment-analysis python run_pipeline.py --from-combined-csv "output\resenas_combinadas.csv"
```

El comando usa Batch API de forma predeterminada. Cuando termine:

```powershell
conda run -n sentiment-analysis python run_pipeline.py --finalize-batch
```

Para procesar inmediatamente sin Batch:

```powershell
conda run -n sentiment-analysis python run_pipeline.py --from-combined-csv output/resenas_combinadas.csv --llm-mode sync
```

### 1. Extraer, procesar y enviar el lote

```powershell
conda run -n sentiment-analysis python run_pipeline.py --empresas-xlsx Empresas.xlsx --playwright
```

El ID y el estado quedan en `output/openai_batch_state.json`. Cada solicitud
del JSONL tiene un `custom_id` estable y el lote usa `/v1/responses`.

### 2. Consultar el estado

```powershell
conda run -n sentiment-analysis python llm_parse.py --mode batch-status
```

### 3. Descargar resultados y cargarlos a Chroma

```powershell
conda run -n sentiment-analysis python run_pipeline.py --finalize-batch
```

Si el lote aún no terminó, el comando informa el estado y no modifica Chroma.
Al finalizar se generan:

- `output/resenas_clasificadas.csv`
- `output/resenas_clasificadas.xlsx`
- `chroma_data/` con la colección vectorial local

## Prueba rápida síncrona

Procesa tres reseñas por empresa mediante Responses API y las inserta en Chroma:

```powershell
conda run -n sentiment-analysis python run_pipeline.py --test-mode
```

Para ejecutar todo el volumen de forma síncrona:

```powershell
conda run -n sentiment-analysis python run_pipeline.py --llm-mode sync
```

## Operaciones con Chroma

Cargar o actualizar un archivo clasificado:

```powershell
conda run -n sentiment-analysis python chroma_store.py load --input output/resenas_clasificadas.xlsx
```

Búsqueda semántica:

```powershell
conda run -n sentiment-analysis python chroma_store.py query "paquetes que nunca llegaron" --limit 10
```

Filtrar por metadatos:

```powershell
conda run -n sentiment-analysis python chroma_store.py query "mala comunicación" --where '{"sentiment_label":"Negativa"}'
```

Estadísticas y exportación para BI:

```powershell
conda run -n sentiment-analysis python chroma_store.py stats
conda run -n sentiment-analysis python chroma_store.py export --output output/chroma_export.xlsx
```

Para producción, iniciar Chroma como servicio y definir `CHROMA_HOST`,
`CHROMA_PORT` y `CHROMA_SSL`. La aplicación cambia automáticamente de
`PersistentClient` a `HttpClient`.

## Configuración

Consulta [.env.example](.env.example). Las variables más importantes son:

| Variable | Predeterminado | Uso |
|---|---|---|
| `OPENAI_CLASSIFICATION_MODEL` | `gpt-5.6-luna` | Modelo del clasificador |
| `OPENAI_REASONING_EFFORT` | `none` | Esfuerzo para clasificación |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | Vectores de Chroma |
| `CHROMA_PATH` | `chroma_data` | Persistencia local |
| `CHROMA_COLLECTION` | `customer_reviews` | Colección |
| `CHROMA_UPSERT_BATCH_SIZE` | `100` | Documentos por upsert |

Usa Sol selectivamente cuando una evaluación demuestre que la calidad adicional
compensa el coste y la latencia:

```powershell
$env:OPENAI_CLASSIFICATION_MODEL = "gpt-5.6-sol"
$env:OPENAI_REASONING_EFFORT = "low"
```

## Estructura relevante

```text
run_pipeline.py              Orquestación end-to-end y ciclo Batch
llm_parse.py                 Responses API, Batch API y esquema Pydantic
llm_parse_system_prompt.md   Taxonomía e instrucciones de clasificación
chroma_store.py              Upsert, consulta y exportación de Chroma
procesado_resenas.py         Consolidación y limpieza
web_scrapping.py             Extracción de reseñas
tests/                       Pruebas sin llamadas externas
environment.yml              Entorno Conda mínimo y reproducible
pyproject.toml               Configuración de pytest y Ruff
```

BigQuery y sus dependencias se retiraron del código. Chroma cumple la función de
almacén semántico; CSV/Excel sigue siendo la interfaz tabular para Power BI.

## Calidad y seguridad

```powershell
conda run -n sentiment-analysis pytest
conda run -n sentiment-analysis ruff check .
```

- Nunca versiones `.env`, credenciales JSON, `chroma_data/` ni salidas.
- El antiguo archivo de credenciales de Google está ignorado, pero conviene
  revocar esa clave en Google Cloud si alguna vez se publicó.
- Respeta `robots.txt`, los términos de Trustpilot y la normativa aplicable.
- Antes de cambiar modelo, esfuerzo o taxonomía, evalúa una muestra etiquetada y
  compara precisión, coste, latencia y tasa de errores.

## Autor

Francisco Gonzalez.

Consulta `LICENSE` para los términos del proyecto.
