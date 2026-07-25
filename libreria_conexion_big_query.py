"""
libreria_conexion_big_query.py

Resumen:
    Utilidades para interactuar con Google BigQuery usando una cuenta de servicio.
    Funcionalidades principales:
      - Listar proyectos, datasets y tablas accesibles.
      - Leer tablas a pandas.DataFrame.
      - Escribir DataFrame a tablas de BigQuery.
      - Crear tablas a partir del esquema de un DataFrame.
      - Normalizar columnas datetime (timezone-unaware).

Requisitos:
    - python >= 3.8
    - pandas
    - google-cloud-bigquery
    - google-auth
    - logger_library (proveer setup_logger)

Uso básico:
    from libreria_conexion_big_query import read_bigquery_to_dataframe
    df = read_bigquery_to_dataframe(
        project_id="mi-proyecto",
        dataset_id="mi_dataset",
        table_id="mi_tabla",
        credentials_path="ruta/a/credenciales.json"
    )

Funciones públicas:
    - list_projects_datasets_and_tables(json_key_path)
    - read_bigquery_to_dataframe(project_id, dataset_id, table_id, credentials_path)
    - write_dataframe_to_bigquery(dataframe, project_id, dataset_id, table_id, credentials_path, if_exists='replace')
    - create_bigquery_table_from_dataframe(dataframe, dataframe_name, project_id, dataset_id, credentials_path, replace_if_exists=False)
    - make_datetime_timezone_unaware(df)

Manejo de errores y logging:
    - Se utiliza setup_logger para registrar actividad y errores.
    - Errores de la API de Google se registran; las funciones devuelven None o False cuando fallan.
    - Excepciones críticas y stack traces se registran con logger.debug/critical.

Notas:
    - Asegurar que el archivo JSON de la cuenta de servicio tenga permisos adecuados para las operaciones requeridas.
    - Para tablas grandes preferir cargas por lotes o particionado en lugar de leer todo en memoria.
    - Ajustar políticas de write_disposition según el comportamiento deseado ('replace', 'append', 'fail').

"""

# Import libraries
import pandas as pd
from google.cloud import bigquery
from google.oauth2 import service_account
from google.api_core.exceptions import GoogleAPIError
import traceback
from logger_library import setup_logger
from google.api_core.exceptions import NotFound



def list_projects_datasets_and_tables(json_key_path):
    """
    Lista todos los proyectos, datasets y tablas accesibles mediante una cuenta de servicio de Google Cloud.

    Parámetros:
        json_key_path (str): Ruta al archivo JSON que contiene las credenciales de la cuenta de servicio.

    Retorna:
        None: La función no retorna datos, pero registra la información de los proyectos, datasets y tablas en los logs.
    """
    logger = setup_logger("list_projects_datasets_and_tables")

    try:
        credentials = service_account.Credentials.from_service_account_file(json_key_path)
        logger.info("Credenciales cargadas correctamente desde el archivo JSON.")

        client = bigquery.Client(credentials=credentials)
        logger.info("Cliente de BigQuery inicializado correctamente.")

        try:
            projects = list(client.list_projects())
        except GoogleAPIError as e:
            logger.error("Error al listar proyectos: %s", e)
            return

        if not projects:
            logger.warning("No se encontraron proyectos accesibles con estas credenciales.")
            return

        logger.info("Iniciando listado de proyectos, datasets y tablas.")

        for project in projects:
            logger.info("Proyecto: %s", project.project_id)

            try:
                project_client = bigquery.Client(credentials=credentials, project=project.project_id)
                datasets = list(project_client.list_datasets())
            except GoogleAPIError as e:
                logger.error("Error al acceder a datasets del proyecto %s: %s", project.project_id, e)
                continue

            if not datasets:
                logger.info("  No se encontraron datasets en el proyecto %s.", project.project_id)
                continue

            for dataset in datasets:
                logger.info("  Dataset: %s", dataset.dataset_id)

                dataset_ref = bigquery.DatasetReference(project.project_id, dataset.dataset_id)
                try:
                    tables = list(project_client.list_tables(dataset_ref))
                except GoogleAPIError as e:
                    logger.error("  Error al listar tablas del dataset %s: %s", dataset.dataset_id, e)
                    continue

                if not tables:
                    logger.info("    No se encontraron tablas en el dataset %s.", dataset.dataset_id)
                    continue

                for table in tables:
                    logger.info("    Tabla: %s", table.table_id)

    except Exception as e:
        logger.critical("Error crítico en la función list_projects_datasets_and_tables: %s", e)
        logger.debug(traceback.format_exc())


def read_bigquery_to_dataframe(project_id, dataset_id, table_id, credentials_path):
    """
    Lee una tabla de BigQuery y la convierte en un DataFrame de pandas.

    Parámetros:
        project_id (str): ID del proyecto de Google Cloud.
        dataset_id (str): ID del dataset en BigQuery.
        table_id (str): ID de la tabla en BigQuery.
        credentials_path (str): Ruta al archivo JSON con las credenciales de la cuenta de servicio.

    Retorna:
        pd.DataFrame o None: DataFrame con los datos de la tabla si la consulta es exitosa, o None si ocurre un error.
    """
    logger = setup_logger("read_bigquery_to_dataframe")

    try:
        # Load credentials and initialize client
        credentials = service_account.Credentials.from_service_account_file(credentials_path)
        logger.info("Credenciales cargadas correctamente.")

        client = bigquery.Client(credentials=credentials, project=project_id)
        logger.info("Cliente de BigQuery inicializado para el proyecto '%s'.", project_id)

        # Construct full table reference and query
        table_ref = f"{project_id}.{dataset_id}.{table_id}"
        query = f"SELECT * FROM `{table_ref}`"
        logger.info("Ejecutando consulta: %s", query)

        # Execute query and convert to DataFrame
        dataframe = client.query(query).to_dataframe()
        logger.info("Consulta ejecutada exitosamente. Filas obtenidas: %d", len(dataframe))

        return dataframe

    except GoogleAPIError as api_error:
        logger.error("Error al acceder a BigQuery: %s", api_error)
    except Exception as e:
        logger.critical("Error inesperado al leer datos desde BigQuery: %s", e)
        logger.debug(traceback.format_exc())

    return None


def write_dataframe_to_bigquery(dataframe, project_id, dataset_id, table_id, credentials_path, if_exists='replace'):
    """
    Escribe un DataFrame de pandas en una tabla de BigQuery.

    Parámetros:
        dataframe (pd.DataFrame): DataFrame que se desea escribir en BigQuery.
        project_id (str): ID del proyecto de Google Cloud.
        dataset_id (str): ID del dataset en BigQuery.
        table_id (str): ID de la tabla de destino en BigQuery.
        credentials_path (str): Ruta al archivo JSON con las credenciales de la cuenta de servicio.
        if_exists (str): Acción si la tabla ya existe. Valores permitidos: 'replace', 'append', 'fail'.

    Retorna:
        bool: True si la escritura fue exitosa, False en caso de error.
    """
    logger = setup_logger("write_dataframe_to_bigquery")

    try:
        credentials = service_account.Credentials.from_service_account_file(credentials_path)
        logger.info("Credenciales cargadas correctamente.")

        client = bigquery.Client(credentials=credentials, project=project_id)
        logger.info("Cliente de BigQuery inicializado para el proyecto '%s'.", project_id)

        table_ref = f"{project_id}.{dataset_id}.{table_id}"
        logger.info("Escribiendo datos en la tabla: %s", table_ref)

        job_config = bigquery.LoadJobConfig(
            write_disposition={
                'replace': bigquery.WriteDisposition.WRITE_TRUNCATE,
                'append': bigquery.WriteDisposition.WRITE_APPEND,
                'fail': bigquery.WriteDisposition.WRITE_EMPTY
            }.get(if_exists, bigquery.WriteDisposition.WRITE_TRUNCATE)
        )

        load_job = client.load_table_from_dataframe(dataframe, table_ref, job_config=job_config)
        load_job.result()  # Espera a que termine el job

        logger.info("Escritura completada exitosamente. Filas escritas: %d", load_job.output_rows)
        return True

    except GoogleAPIError as api_error:
        logger.error("Error al escribir en BigQuery: %s", api_error)
    except Exception as e:
        logger.critical("Error inesperado al escribir datos en BigQuery: %s", e)
        logger.debug(traceback.format_exc())

    return False


def create_bigquery_table_from_dataframe(dataframe, dataframe_name, project_id, dataset_id, credentials_path, replace_if_exists=False):
    """
    Crea una tabla en BigQuery a partir del esquema de un DataFrame de pandas.

    El nombre de la tabla será igual al nombre proporcionado del DataFrame.

    Parámetros:
        dataframe (pd.DataFrame): DataFrame con la estructura que se usará para crear la tabla.
        dataframe_name (str): Nombre que se utilizará como nombre de la tabla en BigQuery.
        project_id (str): ID del proyecto de Google Cloud.
        dataset_id (str): ID del dataset donde se creará la tabla.
        credentials_path (str): Ruta al archivo JSON con las credenciales de la cuenta de servicio.
        replace_if_exists (bool): Si True y la tabla existe, se eliminará y se creará de nuevo. Por defecto False.

    Retorna:
        str: Nombre completo de la tabla creada si fue exitosa.

    Lanza:
        ValueError: Si la tabla ya existe en BigQuery y replace_if_exists es False.
    """
    logger = setup_logger("create_bigquery_table_from_dataframe")

    try:
        credentials = service_account.Credentials.from_service_account_file(credentials_path)
        logger.info("Credenciales cargadas correctamente.")

        client = bigquery.Client(credentials=credentials, project=project_id)
        logger.info("Cliente de BigQuery inicializado para el proyecto '%s'.", project_id)

        table_id = dataframe_name
        table_ref = f"{project_id}.{dataset_id}.{table_id}"

        # Verifica si la tabla ya existe
        try:
            client.get_table(table_ref)
            # Si se llega aquí la tabla existe
            if replace_if_exists:
                logger.info("La tabla '%s' ya existe y replace_if_exists=True. Eliminando la tabla...", table_ref)
                try:
                    client.delete_table(table_ref)
                    logger.info("Tabla '%s' eliminada correctamente.", table_ref)
                except Exception as del_exc:
                    logger.error("Error eliminando la tabla '%s': %s", table_ref, del_exc)
                    raise
            else:
                raise ValueError(f"La tabla '{table_ref}' ya existe en BigQuery.")
        except NotFound:
            logger.info("La tabla '%s' no existe. Procediendo a crearla.", table_ref)

        # Generar el esquema a partir del DataFrame
        schema = []
        for name, dtype in dataframe.dtypes.items():
            if pd.api.types.is_string_dtype(dtype) or pd.api.types.is_object_dtype(dtype):
                field_type = "STRING"
            elif pd.api.types.is_integer_dtype(dtype):
                field_type = "INTEGER"
            elif pd.api.types.is_float_dtype(dtype):
                field_type = "FLOAT"
            elif pd.api.types.is_bool_dtype(dtype):
                field_type = "BOOLEAN"
            elif pd.api.types.is_datetime64_any_dtype(dtype):
                field_type = "TIMESTAMP"
            else:
                field_type = "STRING"  # Fallback

            schema.append(bigquery.SchemaField(name, field_type))

        table = bigquery.Table(table_ref, schema=schema)
        client.create_table(table)
        logger.info("Tabla creada exitosamente: %s", table_ref)

        return table_ref

    except ValueError as ve:
        logger.error(str(ve))
        raise
    except GoogleAPIError as api_error:
        logger.error("Error al crear la tabla en BigQuery: %s", api_error)
    except Exception as e:
        logger.critical("Error inesperado al crear la tabla en BigQuery: %s", e)
        logger.debug(traceback.format_exc())

    return None

def make_datetime_timezone_unaware(df):
    """
    Convierte todas las columnas de tipo datetime en el DataFrame a objetos sin zona horaria (timezone unaware).

    Parámetros:
        df (pd.DataFrame): DataFrame que contiene columnas de tipo datetime.

    Retorna:
        pd.DataFrame: DataFrame con las columnas datetime sin información de zona horaria.
    """
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].dt.tz_localize(None)
    return df

