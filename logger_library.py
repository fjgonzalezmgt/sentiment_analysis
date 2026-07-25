"""logger_library — Utilidades para configuración de logging

Provee setup_logger(...) para crear y configurar un logger profesional con:
- Soporte de salida por consola.
- Rotación de archivos (RotatingFileHandler).
- Formato personalizable y niveles de logging.
- Creación automática del directorio del archivo de log si es necesario.
- Comportamiento idempotente: no duplica handlers si el logger ya está configurado.

Uso:
    from logger_library import setup_logger
    logger = setup_logger("mi_app", log_file="logs/mi_app.log", level=logging.DEBUG)
    logger.info("Arrancando aplicación")

Notas:
- Si log_file es None o cadena vacía, solo se configura la consola (si console=True).
- El módulo logging de la stdlib es seguro para el uso en hilos; este helper no añade estado global mutable.
- La función establece logger.propagate = False para evitar mensajes duplicados cuando se usan múltiples handlers.

Parámetros relevantes de setup_logger:
- name (str): nombre del logger (usado por logging.getLogger).
- log_file (Optional[str]): ruta al archivo de log. Si se especifica, se crea un RotatingFileHandler.
- level (int): nivel de logging (logging.DEBUG, INFO, WARNING, ERROR, CRITICAL).
- console (bool): activar salida por consola.
- max_bytes (int): tamaño en bytes para rotación del archivo.
- backup_count (int): cantidad de archivos de respaldo a mantener.
- fmt (str): formato del mensaje de log.

Ejemplo mínimo:
    logger = setup_logger("app")
    logger.warning("Mensaje de prueba")

Author
------
Francisco Gonzalez

Fin de la documentación del módulo.
"""

import logging
import logging.handlers
import os
from typing import Optional


def setup_logger(
    name: str,
    log_file: Optional[str] = "events.log",
    level: int = logging.INFO,
    console: bool = True,
    max_bytes: int = 2 * 1024 * 1024,  # 2 MB
    backup_count: int = 5,
    fmt: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
) -> logging.Logger:
    """Configurar un logger con consola y archivo rotativo.

    Parameters
    ----------
    name : str
        Nombre del logger.
    log_file : str or None, default="events.log"
        Ruta del archivo. Si se omite, solo se configura la consola.
    level : int, default=logging.INFO
        Nivel mínimo de registro.
    console : bool, default=True
        Indica si se habilita salida por consola.
    max_bytes : int, default=2097152
        Tamaño máximo del archivo antes de rotarlo.
    backup_count : int, default=5
        Número de respaldos rotativos.
    fmt : str
        Formato de los mensajes.

    Returns
    -------
    logging.Logger
        Logger configurado de manera idempotente.
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False  # Evita duplicados si ya está configurado

    if not logger.handlers:  # Solo configurar si no tiene handlers aún
        formatter = logging.Formatter(fmt)

        if log_file:
            dir_path = os.path.dirname(log_file)
            if dir_path:  # Solo crear si hay un directorio explícito
                os.makedirs(dir_path, exist_ok=True)

            file_handler = logging.handlers.RotatingFileHandler(
                log_file, maxBytes=max_bytes, backupCount=backup_count
            )
            file_handler.setFormatter(formatter)
            file_handler.setLevel(level)
            logger.addHandler(file_handler)

        if console:
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(formatter)
            console_handler.setLevel(level)
            logger.addHandler(console_handler)

    return logger
