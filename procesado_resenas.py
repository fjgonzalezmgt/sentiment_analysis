"""
Módulo: procesado_resenas.py

Autores:
    Francisco Gonzalez
    Vincent Martinez

Fecha de creación: 2025
Universidad: Universidad Galileo

Resumen:
    Herramientas para cargar múltiples archivos CSV de reseñas (exportados de Trustpilot),
    combinar los datos en un único DataFrame, normalizar el nombre de la compañía e
    exportar las reseñas combinadas a Excel y CSV.

Propósito:
    - Leer todos los archivos .csv en una carpeta de entrada.
    - Añadir una columna 'company' derivada del nombre de archivo (se elimina el prefijo
      "trustpilot_reviews_" y se convierte a mayúsculas).
    - Concatenar todos los DataFrames leídos en un único DataFrame.
    - Filtrar (por ejemplo eliminar registros de la compañía "SAMPLE").
    - Eliminar duplicados basados en 'review_id'.
    - Eliminar filas sin 'review_id'.
    - Guardar el resultado en formatos .xlsx y .csv.

Requisitos:
    - Python 3.8+.
    - pandas instalado (pip install pandas openpyxl).
    - logger_library disponible en el proyecto.
    - Permisos de lectura en la carpeta de entrada y de escritura en la carpeta de salida.

Estructura de archivos esperada:
    carpeta_entrada (por defecto "review_data")
        ├─ trustpilot_reviews_COMPANY1.csv
        ├─ trustpilot_reviews_COMPANY2.csv
        └─ ...
    salida esperada:
        output/resenas_combinadas.xlsx
        output/resenas_combinadas.csv

Funciones públicas:
    - cargar_resenas_csv(carpeta: str) -> pandas.DataFrame
        Lee todos los .csv de la carpeta indicada, añade la columna 'company' y
        devuelve un DataFrame con todas las reseñas concatenadas.
        Excepciones:
            - FileNotFoundError: si no se encuentran archivos CSV en la carpeta.

    - guardar_resenas_excel(df: pandas.DataFrame, ruta_salida: str) -> None
        Guarda el DataFrame dado en formato Excel (.xlsx). No crea automáticamente
        el directorio padre; debe existir o provocará un error de escritura.

    - guardar_resenas_csv(df: pandas.DataFrame, ruta_salida: str) -> None
        Guarda el DataFrame dado en formato CSV. Igual consideración de directorio.

Uso (línea de comandos):
    python procesado_resenas.py
    - Ajustar la variable `carpeta_entrada` dentro de `main()` o modificar las rutas
      si se desea usar ubicaciones diferentes.

Notas de implementación:
    - El nombre de la compañía se obtiene desde el nombre de archivo removiendo el
      prefijo literal "trustpilot_reviews_". Si los nombres de archivo no siguen este
      patrón, la etiqueta 'company' puede quedar con el nombre completo del archivo.
    - Pandas usa la codificación por defecto para leer CSV; para CSV con codificaciones
      distintas (p. ej. 'utf-8-sig' o 'latin-1') puede ser necesario pasar `encoding=...`
      a pd.read_csv.
    - Se añade la columna 'company' en mayúsculas: df['company'] = nombre_sin_extension.upper()
    - El módulo actualmente filtra explícitamente filas con company == 'SAMPLE' en main().
    - Se implementa limpieza de datos eliminando duplicados y valores nulos en 'review_id'.
    - Sistema de logging integrado para seguimiento de operaciones.
    - Mejoras recomendadas: crear automáticamente directorios de salida si no existen 
      (os.makedirs(..., exist_ok=True)).

Ejemplo mínimo de personalización:
    - Cambiar `carpeta_entrada` por la ruta absoluta de la carpeta con CSVs.
    - Crear la carpeta `output/` antes de ejecutar o adaptar las funciones para crearla.

Workflow típico:
    1. Colocar archivos CSV de Trustpilot en la carpeta 'review_data/'.
    2. Ejecutar el script: python procesado_resenas.py
    3. Revisar los archivos de salida en 'output/resenas_combinadas.xlsx' y 
       'output/resenas_combinadas.csv'.
    4. Verificar logs para confirmar que todas las operaciones se completaron exitosamente.
"""

import pandas as pd
import os
from logger_library import setup_logger

def cargar_resenas_csv(carpeta: str) -> pd.DataFrame:
    """
    Carga todos los archivos CSV de reseñas desde una carpeta y los combina en un único DataFrame.
    
    Esta función busca todos los archivos .csv en la carpeta especificada, los lee uno por uno,
    extrae el nombre de la compañía del nombre del archivo (removiendo el prefijo 
    'trustpilot_reviews_'), añade una columna 'company' con ese nombre en mayúsculas, y 
    finalmente concatena todos los DataFrames en uno solo.
    
    Args:
        carpeta (str): Ruta de la carpeta que contiene los archivos CSV de reseñas.
                      Se espera que los archivos sigan el patrón de nombre 
                      'trustpilot_reviews_<COMPANY>.csv'.
    
    Returns:
        pd.DataFrame: DataFrame concatenado con todas las reseñas de todos los archivos CSV.
                     Incluye todas las columnas originales más la columna 'company' que 
                     identifica la empresa de cada reseña.
    
    Raises:
        FileNotFoundError: Si no se encuentran archivos CSV en la carpeta especificada.
    
    Ejemplo:
        >>> df = cargar_resenas_csv("review_data")
        >>> print(df.columns)
        Index(['review_id', 'rating', 'text', 'date', 'company', ...])
        >>> print(df['company'].unique())
        ['ALIEXPRESS', 'DHL', 'GENEI.ES', ...]
    
    Notas:
        - Los nombres de archivo deben seguir el patrón 'trustpilot_reviews_<COMPANY>.csv'
        - El nombre de la compañía se convierte a mayúsculas automáticamente
        - Se utiliza logging para registrar el proceso de carga
        - Todos los DataFrames se concatenan con ignore_index=True para crear un índice continuo
    """
    logger = setup_logger("cargar_resenas_csv")
    logger.info(f"Cargando reseñas desde la carpeta: {carpeta}")
    dataframes = {}
    for archivo in os.listdir(carpeta):
        if archivo.endswith(".csv"):
            ruta_completa = os.path.join(carpeta, archivo)
            nombre_sin_extension = os.path.splitext(archivo)[0]
            #dejar el nombre de la comania como nombre del dataframe, removiendo el texto trustpilot_reviews_
            nombre_sin_extension = nombre_sin_extension.replace("trustpilot_reviews_", "")
            df = pd.read_csv(ruta_completa)
            df['company'] = nombre_sin_extension.upper()
            dataframes[nombre_sin_extension] = df
    if not dataframes:
        logger.error(f"No se encontraron archivos CSV en la carpeta: {carpeta}")
        raise FileNotFoundError(f"No se encontraron archivos CSV en la carpeta: {carpeta}")
    else:
        logger.info(f"Archivos CSV cargados: {list(dataframes.keys())}")
        data = pd.concat(dataframes.values(), ignore_index=True)
    return data

def guardar_resenas_excel(df: pd.DataFrame, ruta_salida: str) -> None:
    """
    Guarda un DataFrame de reseñas en formato Excel (.xlsx).
    
    Exporta el DataFrame proporcionado a un archivo Excel sin incluir el índice.
    El directorio de destino debe existir previamente; la función no crea 
    directorios automáticamente.
    
    Args:
        df (pd.DataFrame): DataFrame que contiene las reseñas a guardar.
        ruta_salida (str): Ruta completa del archivo Excel de destino, incluyendo 
                          el nombre del archivo y la extensión .xlsx.
    
    Returns:
        None
    
    Raises:
        PermissionError: Si no hay permisos de escritura en la ruta de destino.
        FileNotFoundError: Si el directorio padre no existe.
    
    Ejemplo:
        >>> df = pd.DataFrame({'company': ['DHL', 'ALIEXPRESS'], 'rating': [5, 4]})
        >>> guardar_resenas_excel(df, "output/resenas_combinadas.xlsx")
        # Archivo guardado exitosamente
    
    Notas:
        - Requiere que pandas tenga instalada la dependencia openpyxl para Excel
        - El archivo se guarda sin índice (index=False)
        - Se registra la operación en el log
    """
    logger = setup_logger("guardar_resenas_excel")
    df.to_excel(ruta_salida, index=False)
    logger.info(f"Reseñas guardadas en: {ruta_salida}")
    
def guardar_resenas_csv(df: pd.DataFrame, ruta_salida: str) -> None:
    """
    Guarda un DataFrame de reseñas en formato CSV.
    
    Exporta el DataFrame proporcionado a un archivo CSV sin incluir el índice.
    El directorio de destino debe existir previamente; la función no crea 
    directorios automáticamente.
    
    Args:
        df (pd.DataFrame): DataFrame que contiene las reseñas a guardar.
        ruta_salida (str): Ruta completa del archivo CSV de destino, incluyendo 
                          el nombre del archivo y la extensión .csv.
    
    Returns:
        None
    
    Raises:
        PermissionError: Si no hay permisos de escritura en la ruta de destino.
        FileNotFoundError: Si el directorio padre no existe.
    
    Ejemplo:
        >>> df = pd.DataFrame({'company': ['DHL', 'ALIEXPRESS'], 'rating': [5, 4]})
        >>> guardar_resenas_csv(df, "output/resenas_combinadas.csv")
        # Archivo guardado exitosamente
    
    Notas:
        - El archivo se guarda sin índice (index=False)
        - Usa la codificación por defecto de pandas (generalmente UTF-8)
        - Se registra la operación en el log
        - Formato compatible con Excel y otras herramientas de análisis de datos
    """
    logger = setup_logger("guardar_resenas_csv")
    df.to_csv(ruta_salida, index=False)
    logger.info(f"Reseñas guardadas en: {ruta_salida}")

def main(
    carpeta_entrada: str = "review_data",
    output_xlsx: str = "output/resenas_combinadas.xlsx",
    output_csv: str = "output/resenas_combinadas.csv",
):
    """
    Función principal que ejecuta el pipeline completo de procesamiento de reseñas.
    
    Este es el punto de entrada del script. Realiza las siguientes operaciones en orden:
    1. Carga todas las reseñas desde archivos CSV en la carpeta 'review_data'
    2. Filtra y elimina las reseñas de la compañía 'SAMPLE' (datos de prueba)
    3. Elimina filas que no tienen review_id (registros incompletos)
    4. Elimina reseñas duplicadas basándose en review_id
    5. Exporta las reseñas procesadas a formato Excel
    6. Exporta las reseñas procesadas a formato CSV
    
    Variables configurables:
        carpeta_entrada: Define la carpeta donde se buscarán los archivos CSV de entrada.
                        Por defecto: "review_data"
    
    Archivos de salida generados:
        - output/resenas_combinadas.xlsx: Archivo Excel con todas las reseñas procesadas
        - output/resenas_combinadas.csv: Archivo CSV con todas las reseñas procesadas
    
    Proceso de limpieza de datos:
        - Eliminación de compañía SAMPLE: Remueve datos de prueba o ejemplos
        - Eliminación de valores nulos en review_id: Asegura que todas las reseñas 
          tengan un identificador válido
        - Eliminación de duplicados: Mantiene solo una instancia de cada review_id único
    
    Returns:
        None
    
    Raises:
        FileNotFoundError: Si la carpeta 'review_data' no contiene archivos CSV
        PermissionError: Si no hay permisos para escribir en la carpeta 'output'
    
    Ejemplo de uso:
        >>> if __name__ == "__main__":
        >>>     main()
        # Procesa todos los archivos CSV y genera los archivos de salida
    
    Notas:
        - La carpeta 'output' debe existir antes de ejecutar el script
        - Se utiliza logging para rastrear todo el proceso
        - Las operaciones de limpieza se realizan in-place para eficiencia de memoria
    """
    logger = setup_logger("procesado_resenas")
    
    # Cargar reseñas desde CSVs
    logger.info("Cargando reseñas desde CSV...")
    df_resenas = cargar_resenas_csv(carpeta_entrada)
    
    # Eliminar las filas de la compania SAMPLE (datos de prueba)
    df_resenas = df_resenas[df_resenas['company'] != 'SAMPLE']

    # Eliminar filas sin review_id (registros incompletos)
    df_resenas.dropna(subset=['review_id'], inplace=True)
    
    # Eliminar reseñas duplicadas basándose en review_id
    df_resenas.drop_duplicates(subset=['review_id'], inplace=True)
    
    # Guardar reseñas en un archivo Excel
    out_dir = os.path.dirname(output_xlsx)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    guardar_resenas_excel(df_resenas, output_xlsx)
    
    # Guardar reseñas en un archivo CSV
    out_dir = os.path.dirname(output_csv)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    guardar_resenas_csv(df_resenas, output_csv)
    
   
    
if __name__ == "__main__":
    main()
