# -*- coding: utf-8 -*-
"""
Módulo: load_to_bigquery.py

Autores:
    - Francisco Gonzalez
    - Vincent Martinez

Fecha de creación: 2025
Universidad: Universidad Galileo
Trimestre: 8

Descripción:
    Script para crear una tabla en Google BigQuery a partir de un DataFrame de pandas
    y para cargar los datos de reseñas preprocesadas a dicha tabla. El DataFrame se
    lee desde un archivo Excel local (por defecto: output/resenas_clasificadas.xlsx).
    
    Este módulo forma parte del pipeline de procesamiento de reseñas de clientes,
    permitiendo la persistencia de los datos clasificados en la nube de Google
    para análisis posterior y visualización en herramientas de BI.

Funciones principales:
    - create_bigquery_table_from_dataframe(...):
        Crea la tabla en BigQuery inferiendo el esquema a partir del DataFrame.
        (Delegada al módulo libreria_conexion_big_query).
    - write_dataframe_to_bigquery(...):
        Inserta los registros del DataFrame en la tabla BigQuery especificada.
        (Delegada al módulo libreria_conexion_big_query).
    - main():
        Orquesta la lectura del Excel y llama a las funciones anteriores para crear
        la tabla y cargar los datos.

Requisitos:
    - Python 3.8+ (recomendado)
    - pandas
    - google-cloud-bigquery (si la librería de conexión lo utiliza)
    - libreria_conexion_big_query.py accesible en el mismo directorio o en PYTHONPATH
    - logger_library.py para el registro de eventos
    - Archivo de credenciales JSON de Google Cloud con permisos de BigQuery:
        - roles/bigquery.dataEditor (o superior) para escribir datos
        - roles/bigquery.admin (si se requiere crear datasets/tablas)

Parámetros de configuración (variables del módulo):
    - GCP_PROJECT_ID: ID del proyecto GCP donde se alojará la tabla.
    - GCP_DATASET_ID: ID del dataset BigQuery.
    - GCP_TABLE_ID: Nombre de la tabla a crear/escribir.
    - GOOGLE_JSON_CREDENTIALS_PATH: Ruta local al archivo JSON de credenciales.

Archivo de entrada por defecto:
    - output/resenas_clasificadas.xlsx
      (Debe contener las columnas esperadas por su pipeline; el esquema será inferido.)

Cómo ejecutar:
    1) Colocar el archivo de credenciales JSON en una ruta accesible y
       actualizar GOOGLE_JSON_CREDENTIALS_PATH o establecer la variable de entorno
       GOOGLE_APPLICATION_CREDENTIALS con la ruta al JSON.
    2) Ejecutar desde la terminal (Windows PowerShell o CMD):
       python load_to_bigquery.py

Ejemplo de uso:
    >>> python load_to_bigquery.py
    # Cargará el archivo output/resenas_clasificadas.xlsx y lo subirá a BigQuery

Buenas prácticas y notas de seguridad:
    - No subir el archivo de credenciales JSON a repositorios públicos.
    - Preferir el uso de cuentas de servicio con permisos mínimos necesarios.
    - Para cargas grandes, preferir métodos de carga por archivos (GCS -> BigQuery)
      en lugar de insert_rows si la librería lo soporta.
    - Validar tamaño y tipos de datos del DataFrame antes de la carga para evitar
      fallos por incompatibilidades de esquema.

Manejo de errores recomendado:
    - Añadir manejo de excepciones alrededor de las llamadas a la API de BigQuery
      para capturar errores de autenticación, permisos o límites de cuota.
    - Validar que el archivo Excel exista y que pandas pueda leerlo correctamente.

Referencias:
    - https://cloud.google.com/bigquery/docs
    - Documentación de google-cloud-bigquery: https://googleapis.dev/python/bigquery/latest/index.html
    - Google Cloud Authentication: https://cloud.google.com/docs/authentication/getting-started

Notas de versión:
    - v1.0: Implementación inicial con carga desde Excel a BigQuery
"""

import pandas as pd
from libreria_conexion_big_query import create_bigquery_table_from_dataframe, write_dataframe_to_bigquery
from logger_library import setup_logger

# ============================================================================
# CONFIGURACIÓN DE GOOGLE CLOUD PLATFORM
# ============================================================================

# ID del proyecto en Google Cloud Platform
GCP_PROJECT_ID = "alpine-realm-352216"  # Cambia por tu proyecto GCP

# ID del dataset en BigQuery donde se almacenarán las tablas
GCP_DATASET_ID = "galileo"       # Cambia por tu dataset

# Nombre de la tabla que contendrá las reseñas clasificadas
GCP_TABLE_ID = "resenas_clasificadas"  # Cambia por tu tabla

# Ruta al archivo JSON de credenciales de la cuenta de servicio de GCP
# Ruta al archivo JSON de credenciales de la cuenta de servicio de GCP
GOOGLE_JSON_CREDENTIALS_PATH = "alpine-realm-352216-66fb3ad8f36b.json"  # Cambia por la ruta a tus credenciales


# ============================================================================
# FUNCIÓN PRINCIPAL
# ============================================================================

def main():
    """
    Función principal que orquesta el proceso de carga de datos a BigQuery.
    
    Flujo de trabajo:
        1. Inicializa el logger para registro de eventos
        2. Lee el archivo Excel con las reseñas clasificadas
        3. Crea la tabla en BigQuery con el esquema inferido del DataFrame
        4. Carga los datos del DataFrame a la tabla de BigQuery
        5. Registra la finalización exitosa del proceso
    
    Args:
        None
    
    Returns:
        None
    
    Raises:
        FileNotFoundError: Si el archivo Excel no existe en la ruta especificada
        google.cloud.exceptions.GoogleCloudError: Si hay errores en la comunicación con BigQuery
        pandas.errors.EmptyDataError: Si el archivo Excel está vacío
        Exception: Otros errores durante el proceso de carga
    
    Example:
        >>> main()
        INFO - Iniciando carga de datos a BigQuery...
        INFO - Datos escritos en BigQuery: alpine-realm-352216.galileo.resenas_clasificadas
    
    Notes:
        - El archivo de entrada debe existir en output/resenas_clasificadas.xlsx
        - Si la tabla ya existe, será reemplazada (replace_if_exists=True)
        - Los datos se cargan mediante el método write_dataframe_to_bigquery
        - Se recomienda validar el DataFrame antes de la carga para grandes volúmenes
    """
    # Configurar logger para seguimiento del proceso
    logger = setup_logger("load_to_bigquery")
    logger.info("Iniciando carga de datos a BigQuery...")
    
    # Leer archivo Excel con las reseñas clasificadas
    df_resenas = pd.read_excel("output/resenas_clasificadas.xlsx")
    
    # Crear tabla en BigQuery con esquema inferido del DataFrame
    # Si la tabla existe, será reemplazada debido a replace_if_exists=True
    create_bigquery_table_from_dataframe(
        dataframe=df_resenas, 
        dataframe_name=GCP_TABLE_ID,
        project_id=GCP_PROJECT_ID, 
        dataset_id=GCP_DATASET_ID,
        credentials_path=GOOGLE_JSON_CREDENTIALS_PATH,
        replace_if_exists=True
    )
    
    # Cargar datos del DataFrame a la tabla de BigQuery
    write_dataframe_to_bigquery(
        dataframe=df_resenas, 
        project_id=GCP_PROJECT_ID, 
        dataset_id=GCP_DATASET_ID,
        table_id=GCP_TABLE_ID,
        credentials_path=GOOGLE_JSON_CREDENTIALS_PATH
    )
    
    # Registrar finalización exitosa
    # Registrar finalización exitosa
    logger.info(f"Datos escritos en BigQuery: {GCP_PROJECT_ID}.{GCP_DATASET_ID}.{GCP_TABLE_ID}")


# ============================================================================
# PUNTO DE ENTRADA DEL SCRIPT
# ============================================================================

if __name__ == "__main__":
    """
    Punto de entrada cuando el script se ejecuta directamente.
    
    Permite ejecutar el módulo como script independiente desde la línea de comandos:
        python load_to_bigquery.py
    
    Si el módulo es importado desde otro script, este bloque no se ejecutará.
    """
    main()