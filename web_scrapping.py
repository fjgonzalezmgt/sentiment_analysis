# -*- coding: utf-8 -*-
"""Scraper de reseñas orientado a Trustpilot (archivo principal).

Author
------
Francisco Gonzalez

Resumen
--------
Este módulo extrae reseñas públicas desde páginas de Trustpilot y, como
fallback, desde sitios que sigan microformatos/JSON-LD o patrones comunes
de reseñas. Prioriza extracción desde JSON-LD y ofrece dos modos de
obtención de HTML: requests (rápido) y Playwright (para contenido
inyectado por JavaScript).

Funcionalidades principales
---------------------------
- Parseo de JSON-LD (<script type="application/ld+json">) buscando objetos
  @type = "Review".
- Adaptador específico para Trustpilot (selectores data-*) para extraer
  id, título, cuerpo, valoración, autor, fecha y ubicación.
- Fallback genérico basado en convenciones (itemprop, itemtype, data-*)
  para otros sitios.
- Paginación heurística (rel="next", enlaces textuales, parámetro ?page).
- Dedupe entre resultados (prioriza ID; si no hay ID, hash autor+fragmento).
- Guardado en CSV con codificación utf-8-sig (compatible con Excel en Windows).

Salida / Modelo de datos
------------------------
Clase dataclass Review con campos (columna CSV con este orden):
- review_id: Optional[str]
- title: Optional[str]
- body: Optional[str]
- rating: Optional[float]
- author: Optional[str]
- date_published: Optional[str]
- location: Optional[str]
- source_url: str

CSV: nombre de columnas igual a los atributos de la dataclass.

Uso (CLI)
---------
Ejemplos:
  python web_scrapping.py --url "https://es.trustpilot.com/review/www.dominio" --out reseñas.csv
  python web_scrapping.py --url "https://es.trustpilot.com/review/www.dominio" --out reseñas.csv --playwright
    python web_scrapping.py --url "https://es.trustpilot.com/review/www.dominio" --out reseñas.csv --max-reviews 200

Opciones principales (resumen)
- --url           URL de inicio (página de reseñas).
- --out           Ruta del CSV de salida.
- --timeout       Timeout por request en segundos.
- --pause         Pausa entre páginas en segundos.
- --max-pages     Límite de páginas a recorrer.
- --max-reviews   Límite de reseñas (deduplicadas) a recolectar.
- --user-agent    User-Agent HTTP a usar.
- --conservative  Si robots.txt no es legible, abortar (modo conservador).
- --playwright    Usar Playwright (cuando requests no recupera reseñas).

Dependencias
------------
- requests
- beautifulsoup4
- (opcional) lxml        -> parser más robusto (BeautifulSoup).
- (opcional) playwright  -> para JavaScript rendering; instalar navegadores:
                         `playwright install` o `playwright install chromium`.

Instalación rápida
------------------
pip install requests beautifulsoup4
# opcionalmente:
pip install lxml
pip install playwright
playwright install chromium

Consideraciones éticas y legales
--------------------------------
- Respeta robots.txt y la política de uso del sitio. El script intenta leer
  robots.txt; si no se puede leer y no se usa --conservative, el script
  continúa pero emite un aviso.
- Este código recolecta contenido público para análisis; no eluda medidas
  de protección (CAPTCHAs, bloqueos por scraping) ni realices extracción
  que viole términos de servicio o privacidad.
- Para estudios académicos, agrega pausas razonables (--pause) y limita
  el número de páginas (--max-pages).

Comportamiento y decisiones de diseño
-------------------------------------
- Se prioriza JSON-LD porque suele contener datos estructurados y estables.
- Se deduplica entre fuentes (JSON-LD vs DOM) usando una huella:
  - Si hay review_id, se usa como clave.
  - Si no, se genera SHA1 de author + primeros 200 caracteres del body.
- Se filtran CTAs o placeholders (frases como "write your review") para
  evitar reseñas vacías o botones de escritura.
- Guardado CSV con utf-8-sig para compatibilidad con Excel en Windows.

Consejos de uso
---------------
- Si la página carga reseñas mediante JS (p. ej. contenido dinámico),
  ejecute con --playwright.
- Aumente --pause si detecta bloqueos por tasa de peticiones.
- Si necesita campos adicionales, inspeccione los <script type="application/ld+json">
  y amplíe extract_from_jsonld o los parsers DOM.

Errores comunes y solución rápida
--------------------------------
- "Playwright no está instalado": instale playwright y ejecute `playwright install`.
- Error de conexión/timeout: aumentar --timeout o revisar conectividad.
- CSV vacío pero HTML contiene reseñas: probar --playwright si las reseñas se inyectan por JS.

Notas para desarrolladores
--------------------------
- Los selectores de Trustpilot pueden cambiar con el tiempo; mantener tests
  y actualizar find_review_blocks_trustpilot / parse_single_review_trustpilot.
- Para pruebas unitarias, inyectar HTML de ejemplo y validar extract_from_jsonld,
  parse_single_review_trustpilot y parse_single_review_generic.

Licencia / Créditos
-------------------
Código provisto "tal cual"; revisar compatibilidad de licencia antes de
uso en proyectos comerciales. Añadir encabezado de licencia si se integra
en repositorios con requisitos específicos.

"""

import argparse
import csv
import hashlib
import json
import os
import re
import time
import urllib.parse
from dataclasses import asdict, dataclass
from typing import List, Optional
from urllib import robotparser
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from logger_library import setup_logger

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    sync_playwright = None  # type: ignore

# ==========================
# Configuración predeterminada
# ==========================
DEFAULT_START_URL = "https://es.trustpilot.com/review/sending.es"
DEFAULT_OUTPUT_CSV: str | None = None  # si no se especifica, se calcula desde la URL
DEFAULT_TIMEOUT = 25
DEFAULT_PAUSE = 2.0
DEFAULT_MAX_PAGES = 500
DEFAULT_MAX_REVIEWS: int | None = None
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

SKIP_PHRASES = {"write your review", "escribe tu reseña", "escribe tu review"}


# ==========================
# Modelo de datos
# ==========================
@dataclass
class Review:
    """Modelo de datos para almacenar información de una reseña.
    
    Attributes
    ----------
    review_id : Optional[str]
        Identificador único de la reseña (si está disponible).
    title : Optional[str]
        Título de la reseña.
    body : Optional[str]
        Cuerpo o contenido principal de la reseña.
    rating : Optional[float]
        Valoración numérica (ej. 1.0 a 5.0).
    author : Optional[str]
        Nombre del autor de la reseña.
    date_published : Optional[str]
        Fecha de publicación en formato ISO u otro formato encontrado.
    location : Optional[str]
        Ubicación geográfica del autor (si está disponible).
    source_url : str
        URL de la página donde se encontró la reseña.
    """
    review_id: Optional[str]
    title: Optional[str]
    body: Optional[str]
    rating: Optional[float]
    author: Optional[str]
    date_published: Optional[str]
    location: Optional[str]
    source_url: str


# ==========================
# Utilidades generales
# ==========================
def text_or_none(el) -> Optional[str]:
    """Extrae texto de un elemento BeautifulSoup o retorna None.
    
    Parameters
    ----------
    el : Tag or None
        Elemento BeautifulSoup del cual extraer texto.
    
    Returns
    -------
    Optional[str]
        Texto extraído con espacios eliminados, o None si el elemento es None.
    """
    return el.get_text(strip=True) if el else None


def first_attr(el, attr) -> Optional[str]:
    """Obtiene el valor de un atributo HTML de un elemento.
    
    Parameters
    ----------
    el : Tag or None
        Elemento BeautifulSoup.
    attr : str
        Nombre del atributo a extraer.
    
    Returns
    -------
    Optional[str]
        Valor del atributo o None si no existe o el elemento es None.
    """
    if not el:
        return None
    val = el.get(attr)
    return val if val else None


def preferred_soup(html: str) -> BeautifulSoup:
    """Crea un objeto BeautifulSoup usando el mejor parser disponible.
    
    Intenta usar lxml (más rápido y robusto) como primera opción.
    Si lxml no está disponible, utiliza html.parser (incluido en Python).
    
    Parameters
    ----------
    html : str
        Código HTML a parsear.
    
    Returns
    -------
    BeautifulSoup
        Objeto BeautifulSoup listo para navegar el DOM.
    """
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:
        return BeautifulSoup(html, "html.parser")


def is_allowed(url: str, user_agent: str, abort_if_unreachable: bool) -> bool:
    """Verifica si robots.txt permite el scraping de una URL.
    
    Consulta el archivo robots.txt del sitio y determina si el User-Agent
    especificado tiene permiso para acceder a la URL.
    
    Parameters
    ----------
    url : str
        URL completa a verificar.
    user_agent : str
        User-Agent que se usará para la verificación.
    abort_if_unreachable : bool
        Si True y robots.txt no es accesible, retorna False (modo conservador).
        Si False y robots.txt no es accesible, retorna True con advertencia.
    
    Returns
    -------
    bool
        True si el acceso está permitido, False en caso contrario.
    """
    logger = setup_logger("is_allowed")
    try:
        parsed = urllib.parse.urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        rp = robotparser.RobotFileParser()
        rp.set_url(robots_url)
        rp.read()
        allowed = rp.can_fetch(user_agent, url)
        if allowed is None:
            logger.warning(f"Aviso: robots.txt no dio veredicto para {url}. Continuaré con cortesía (pausas, UA).")
            return True
        return bool(allowed)
    except Exception as e:
        msg = f"Aviso: no se pudo leer robots.txt ({e!r})."
        if abort_if_unreachable:
            logger.warning(msg + " Abortando por configuración conservadora (--conservative).")
            return False
        else:
            logger.warning(msg + " Continuaré bajo tu responsabilidad.")
            return True


def get_soup(url: str, headers: dict, timeout: int) -> BeautifulSoup:
    """Descarga HTML de una URL y retorna objeto BeautifulSoup.
    
    Parameters
    ----------
    url : str
        URL a descargar.
    headers : dict
        Diccionario de encabezados HTTP (ej. User-Agent).
    timeout : int
        Tiempo máximo de espera en segundos.
    
    Returns
    -------
    BeautifulSoup
        Objeto BeautifulSoup del contenido descargado.
    
    Raises
    ------
    requests.HTTPError
        Si la respuesta HTTP tiene código de error.
    """
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return preferred_soup(resp.text)


def _norm(s):
    """Normaliza una cadena eliminando espacios al inicio y final.
    
    Parameters
    ----------
    s : str or None
        Cadena a normalizar.
    
    Returns
    -------
    str
        Cadena normalizada, o cadena vacía si s es None.
    """
    return (s or "").strip()


def normalize_text(s: str | None) -> str | None:
    """Normaliza texto eliminando espacios múltiples y caracteres especiales.
    
    Reemplaza espacios no-separables (\u00A0) por espacios normales,
    colapsa múltiples espacios en uno solo, y elimina espacios al inicio/final.
    
    Parameters
    ----------
    s : str or None
        Texto a normalizar.
    
    Returns
    -------
    str or None
        Texto normalizado, o None si la entrada es None o queda vacía.
    """
    if not s:
        return None
    s = s.replace("\u00A0", " ")
    s = re.sub(r"\s+", " ", s, flags=re.MULTILINE).strip()
    return s or None


def clean_trustpilot_review_id(raw: str | None) -> str | None:
    """Extrae ID limpio desde URLs de Trustpilot en formato JSON-LD.
    
    Trustpilot JSON-LD incluye URLs en formato:
    https://www.trustpilot.com/#/schema/Review/www.dominio/<ID>
    Esta función extrae únicamente el <ID>.
    
    Parameters
    ----------
    raw : str or None
        URL completa o ID parcial de la reseña.
    
    Returns
    -------
    str or None
        ID extraído, URL original si no coincide el patrón, o None si raw es None.
    """
    if not raw:
        return None
    m = re.search(r"/Review/[^/]+/([A-Za-z0-9]+)$", raw)
    return m.group(1) if m else raw


def fingerprint_review(r: Review) -> str:
    """Genera una huella única para identificar y deduplicar reseñas.
    
    Prioriza el review_id si está disponible. Si no existe ID,
    genera un hash SHA1 basado en autor + primeros 200 caracteres del cuerpo.
    
    Parameters
    ----------
    r : Review
        Objeto Review a identificar.
    
    Returns
    -------
    str
        Huella única en formato 'id::<ID>' o 'ab::<hash>'.
    """
    if r.review_id:
        return f"id::{r.review_id}"
    author = (r.author or "").lower()
    body = (r.body or "").lower()[:200]
    h = hashlib.sha1((author + "|" + body).encode("utf-8")).hexdigest()
    return f"ab::{h}"


def _is_trustpilot(url: str) -> bool:
    """Detecta si una URL pertenece al dominio de Trustpilot.
    
    Parameters
    ----------
    url : str
        URL a verificar.
    
    Returns
    -------
    bool
        True si la URL es de Trustpilot, False en caso contrario.
    """  
    host = urlparse(url).netloc.lower()
    return "trustpilot." in host


def _safe_filename_component(s: str | None) -> str:
    """Convertir texto en un componente seguro para nombre de archivo.

    Parameters
    ----------
    s : str or None
        Texto original.

    Returns
    -------
    str
        Componente normalizado o ``reviews`` cuando está vacío.
    """
    s = (s or "").strip()
    if not s:
        return "reviews"
    # Permite letras, números, punto (para dominios), guion y underscore.
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s)
    s = s.strip("._-")
    return s or "reviews"


def company_from_url(url: str) -> str:
    """Extraer el identificador de empresa desde una URL de Trustpilot.

    Parameters
    ----------
    url : str
        URL de un perfil de reseñas.

    Returns
    -------
    str
        Identificador seguro de la empresa.

    Examples
    --------
    >>> company_from_url("https://es.trustpilot.com/review/sending.es")
    'sending.es'
    """
    parsed = urlparse(url)
    path = parsed.path or ""
    # Trustpilot normalmente usa /review/<empresa>
    m = re.search(r"/review/([^/?#]+)", path, re.I)
    company = m.group(1) if m else (parsed.netloc or "")
    company = company.replace("www.", "")
    return _safe_filename_component(company)


def trustpilot_review_url(company_or_url: str) -> str:
    """Construir una URL de Trustpilot desde una empresa o URL.

    Parameters
    ----------
    company_or_url : str
        Dominio, nombre corto o URL completa.

    Returns
    -------
    str
        URL de reseñas normalizada.

    Examples
    --------
    >>> trustpilot_review_url("sending.es")
    'https://es.trustpilot.com/review/sending.es'
    """
    s = (company_or_url or "").strip()
    if not s:
        return DEFAULT_START_URL
    if re.match(r"^https?://", s, flags=re.I):
        return s
    # Heurística: si viene un identificador corto (p.ej. "dhl"), muchos perfiles
    # en Trustpilot suelen usar el dominio www.<nombre>.com.
    if "." not in s and "/" not in s and "\\" not in s:
        s = f"www.{s}.com"
    return f"https://es.trustpilot.com/review/{s}"


def resolve_output_path(start_url: str, out_arg: str | None, company: str | None = None) -> str:
    """Resolver la ruta del CSV de salida.

    Parameters
    ----------
    start_url : str
        URL usada para inferir la empresa.
    out_arg : str or None
        Archivo o directorio solicitado por el usuario.
    company : str or None, default=None
        Empresa explícita que tiene prioridad sobre la URL.

    Returns
    -------
    str
        Ruta del archivo CSV resultante.

    Notes
    -----
    Cuando ``out_arg`` es ``None`` se usa
    ``review_data/trustpilot_reviews_<empresa>.csv``.
    """
    company_name = _safe_filename_component(company) if company else company_from_url(start_url)
    default_name = f"trustpilot_reviews_{company_name}.csv"
    default_path = os.path.join("review_data", default_name)

    if not out_arg:
        return default_path

    # Si parece directorio (termina con / o \) o existe como dir, guardar dentro.
    if out_arg.endswith(("/", "\\")) or os.path.isdir(out_arg):
        return os.path.join(out_arg, default_name)

    return out_arg


# ==========================
# Extracción desde JSON-LD
# ==========================
def extract_from_jsonld(soup: BeautifulSoup, page_url: str) -> List[Review]:
    """Extrae reseñas desde etiquetas <script type="application/ld+json">.
    
    Busca todos los bloques JSON-LD en la página, identifica objetos
    con @type="Review" (directos o anidados), y los convierte a objetos Review.
    
    Parameters
    ----------
    soup : BeautifulSoup
        Objeto BeautifulSoup de la página HTML.
    page_url : str
        URL de la página actual (se almacena en source_url).
    
    Returns
    -------
    List[Review]
        Lista de objetos Review extraídos del JSON-LD.
    """
    found: List[Review] = []
    scripts = soup.find_all("script", attrs={"type": "application/ld+json"})
    for sc in scripts:
        payload = sc.string or sc.text or ""
        if not payload.strip():
            continue
        try:
            data = json.loads(payload)
        except Exception:
            continue

        def _iter_nodes(x):
            """Recorrer recursivamente nodos JSON-LD.

            Parameters
            ----------
            x : Any
                Nodo, lista de nodos o valor escalar.

            Yields
            ------
            dict
                Cada objeto encontrado en la estructura.
            """
            if x is None:
                return
            if isinstance(x, list):
                for y in x:
                    yield from _iter_nodes(y)
            elif isinstance(x, dict):
                yield x
                for v in x.values():
                    if isinstance(v, (dict, list)):
                        yield from _iter_nodes(v)

        for obj in _iter_nodes(data):
            if not isinstance(obj, dict):
                continue
            if str(obj.get("@type", "")).lower() != "review":
                continue

            rating_val = None
            rating_obj = obj.get("reviewRating") or {}
            try:
                rating_val = float(rating_obj.get("ratingValue"))
            except Exception:
                pass

            author = obj.get("author")
            if isinstance(author, dict):
                author = author.get("name")

            item = Review(
                review_id=clean_trustpilot_review_id(_norm(obj.get("url")) or _norm(obj.get("@id"))),
                title=normalize_text(obj.get("name")),
                body=normalize_text(obj.get("reviewBody")),
                rating=rating_val,
                author=normalize_text(author),
                date_published=_norm(obj.get("datePublished")),
                location=None,
                source_url=page_url,
            )

            txt = " ".join([item.title or "", item.body or ""]).lower()
            if any(p in txt for p in SKIP_PHRASES):
                continue

            if any([item.body, item.title, item.rating, item.author, item.date_published]):
                found.append(item)

    return found


# ==========================
# Adaptador Trustpilot (DOM)
# ==========================
def find_review_blocks_trustpilot(soup: BeautifulSoup):
    """Encuentra bloques de reseñas en páginas de Trustpilot mediante selectores DOM.
    
    Utiliza atributos data-* específicos de Trustpilot para identificar
    contenedores de reseñas. Deduplica bloques usando identidad de objeto.
    
    Parameters
    ----------
    soup : BeautifulSoup
        Objeto BeautifulSoup de la página HTML de Trustpilot.
    
    Returns
    -------
    list
        Lista de elementos Tag de BeautifulSoup, cada uno representando una reseña.
    """
    blocks = []
    blocks.extend(soup.select("[data-service-review-id]"))
    blocks.extend(soup.select("[data-review-id]"))
    blocks.extend(soup.select("[data-reviewid]"))
    blocks.extend(soup.select("[data-review-content]"))

    # Deduplicar preservando orden
    seen, dedup = set(), []
    for b in blocks:
        key = id(b)
        if key not in seen:
            seen.add(key)
            dedup.append(b)
    return dedup


def parse_single_review_trustpilot(block, page_url: str) -> Review:
    """Extrae datos de una reseña individual de Trustpilot desde un bloque DOM.
    
    Parsea selectores específicos de Trustpilot para extraer: ID, título,
    cuerpo, valoración, autor, fecha de publicación y ubicación.
    
    Parameters
    ----------
    block : Tag
        Elemento BeautifulSoup que contiene una reseña completa.
    page_url : str
        URL de origen (se almacena en source_url).
    
    Returns
    -------
    Review
        Objeto Review con los datos extraídos.
    """
    # ID
    review_id = (
        block.get("data-service-review-id")
        or block.get("data-review-id")
        or block.get("data-reviewid")
        or None
    )

    # Cuerpo
    body = None
    el_body = block.select_one('p[data-service-review-text-typography], p')
    if el_body:
        body = text_or_none(el_body)

    # Autor
    author = None
    a_author = block.select_one("[data-consumer-profile-reviews-link]")
    if a_author and a_author.get_text(strip=True):
        author = a_author.get_text(strip=True)
    if not author:
        h = block.find(["h2", "h3", "h4"])
        if h and h.get_text(strip=True):
            author = h.get_text(strip=True)

    # Rating
    rating = None
    if block.has_attr("data-service-review-rating"):
        try:
            rating = float(block["data-service-review-rating"])
        except Exception:
            pass
    if rating is None:
        holder = (
            block.select_one("[aria-label*='out of']") or
            block.select_one("img[alt*='out of']")
        )
        if holder:
            s = holder.get("aria-label") or holder.get("alt") or holder.get_text(strip=True) or ""
            m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(?:out of|/)\s*([0-9]+)", s, re.I)
            if m:
                try:
                    rating = float(m.group(1))
                except Exception:
                    pass

    # Fecha
    date_published = None
    t = block.find("time")
    if t:
        date_published = t.get("datetime") or text_or_none(t)

    # Título (a veces, el primer <strong>)
    title = None
    strong = block.find("strong")
    if strong and strong.get_text(strip=True):
        title = strong.get_text(strip=True)

    # Ubicación (rara vez disponible; a veces en 'title' de banderita)
    location = None
    loc = block.find(attrs={"data-consumer-country-flag": True})
    if loc and loc.get("title"):
        location = loc.get("title")

    # Limpiar CTA
    txt = " ".join([t for t in [title, body] if t]).lower()
    if any(p in txt for p in SKIP_PHRASES):
        title = body = None

    return Review(
        review_id=clean_trustpilot_review_id(review_id),
        title=normalize_text(title),
        body=normalize_text(body),
        rating=rating,
        author=normalize_text(author),
        date_published=_norm(date_published),
        location=normalize_text(location),
        source_url=page_url,
    )


# ==========================
# Fallback genérico DOM (por si reusas el script para otros sitios)
# ==========================
def find_review_blocks_generic(soup: BeautifulSoup):
    """Encuentra bloques de reseñas en sitios genéricos usando patrones comunes.
    
    Utiliza microformatos (itemprop, itemtype), atributos data-review-id,
    y patrones de ID para identificar reseñas. Filtra bloques vacíos o
    que contienen frases de llamada a la acción (CTAs).
    
    Parameters
    ----------
    soup : BeautifulSoup
        Objeto BeautifulSoup de la página HTML.
    
    Returns
    -------
    list
        Lista de elementos Tag que probablemente contienen reseñas.
    """
    blocks = []
    blocks.extend(soup.find_all(attrs={"itemprop": "review"}))
    blocks.extend(soup.find_all(attrs={"itemtype": re.compile(r"/Review$", re.I)}))
    blocks.extend(soup.find_all(attrs={"data-review-id": True}))
    blocks.extend(soup.find_all(id=re.compile(r"\breview[-_ ]?\d+", re.I)))

    # Deduplicar
    seen, dedup = set(), []
    for b in blocks:
        key = id(b)
        if key not in seen:
            seen.add(key)
            dedup.append(b)

    # Filtro mínimo: que tenga cuerpo/autor/rating
    filtered = []
    for b in dedup:
        text = (b.get_text(separator=" ", strip=True) or "").lower()
        if not text:
            continue
        if any(p in text for p in SKIP_PHRASES):
            continue
        has_body = b.find(attrs={"itemprop": "description"}) or b.find("p")
        has_rating = b.find(attrs={"itemprop": "ratingValue"}) or b.find(attrs={"data-rating": True})
        has_author = b.find(attrs={"itemprop": "author"}) or b.find(class_=re.compile(r"(author|reviewer|consumer)", re.I))
        if has_body or has_rating or has_author:
            filtered.append(b)
    return filtered


def parse_single_review_generic(block, page_url: str) -> Review:
    """Extrae datos de una reseña desde un bloque genérico usando microformatos.
    
    Utiliza atributos itemprop estándar (name, description, ratingValue, etc.)
    y patrones comunes de clases CSS para extraer información de la reseña.
    
    Parameters
    ----------
    block : Tag
        Elemento BeautifulSoup que contiene una reseña.
    page_url : str
        URL de origen (se almacena en source_url).
    
    Returns
    -------
    Review
        Objeto Review con los datos extraídos.
    """
    review_id = None
    if hasattr(block, "attrs"):
        if "data-review-id" in block.attrs:
            review_id = str(block.attrs.get("data-review-id"))
        elif "id" in block.attrs and re.search(r"\breview[-_ ]?\d+", str(block.attrs["id"]), re.I):
            review_id = str(block.attrs["id"])

    title = text_or_none(block.find(attrs={"itemprop": "name"})) \
            or text_or_none(block.find(["h2", "h3", "h4"], recursive=True))
    body = text_or_none(block.find(attrs={"itemprop": "description"})) \
           or text_or_none(block.find("p"))

    rating = None
    rating_el = block.find(attrs={"itemprop": "ratingValue"})
    if rating_el:
        content_val = rating_el.get("content") or rating_el.get_text(strip=True)
        try:
            rating = float(re.search(r"([0-9]+(?:\.[0-9]+)?)", content_val).group(1))
        except Exception:
            pass
    if rating is None:
        holder = block.find(attrs={"data-rating": True}) or block.find(attrs={"title": re.compile(r"\d")})
        if holder:
            try:
                rating = float(re.search(r"([0-9]+(?:\.[0-9]+)?)", holder.get("data-rating") or holder.get("title") or "").group(1))
            except Exception:
                pass

    author = None
    author_el = block.find(attrs={"itemprop": "author"})
    if author_el:
        name_el = author_el.find(attrs={"itemprop": "name"})
        author = text_or_none(name_el) or text_or_none(author_el)
    else:
        author = text_or_none(block.find(class_=re.compile(r"(author|reviewer|consumer)", re.I)))

    date_published = None
    date_el = block.find(attrs={"itemprop": "datePublished"}) or block.find("time")
    if date_el:
        date_published = first_attr(date_el, "datetime") or text_or_none(date_el)

    location = text_or_none(block.find(attrs={"itemprop": "address"})) \
               or text_or_none(block.find(class_=re.compile(r"location|country|city", re.I)))

    txt = " ".join([t for t in [title, body] if t]).lower()
    if any(p in txt for p in SKIP_PHRASES):
        title = body = None

    return Review(
        review_id=normalize_text(review_id),
        title=normalize_text(title),
        body=normalize_text(body),
        rating=rating,
        author=normalize_text(author),
        date_published=_norm(date_published),
        location=normalize_text(location),
        source_url=page_url,
    )


# ==========================
# Paginación
# ==========================
def find_next_page_url(soup: BeautifulSoup, current_url: str) -> Optional[str]:
    """Identifica la URL de la siguiente página de reseñas.
    
    Implementa tres estrategias heurísticas en orden:
    1. Busca enlaces con atributo rel="next"
    2. Busca enlaces con texto como "next", "siguiente", etc.
    3. Incrementa parámetro ?page=N en la URL actual
    
    Parameters
    ----------
    soup : BeautifulSoup
        Objeto BeautifulSoup de la página actual.
    current_url : str
        URL de la página actual.
    
    Returns
    -------
    Optional[str]
        URL absoluta de la siguiente página, o None si no se encuentra.
    """
    # rel="next"
    link = soup.find("a", rel=lambda v: v and "next" in v.lower())
    if link and link.get("href"):
        return urllib.parse.urljoin(current_url, link["href"])

    # Botón textual
    for text in ["next", "siguiente", "older", "»", ">"]:
        a = soup.find("a", string=re.compile(text, re.I))
        if a and a.get("href"):
            return urllib.parse.urljoin(current_url, a["href"])

    # Heurística ?page=N
    parsed = urllib.parse.urlparse(current_url)
    q = urllib.parse.parse_qs(parsed.query)
    curr = int(q.get("page", [1])[0])
    q["page"] = [str(curr + 1)]
    next_query = urllib.parse.urlencode({k: v[0] for k, v in q.items()})
    return urllib.parse.urlunparse(parsed._replace(query=next_query))


# ==========================
# Scrapers
# ==========================
def _maybe_append_review(
    out: List[Review],
    seen: set[str],
    r: Review,
    max_reviews: int | None,
) -> bool:
    """Agregar una reseña válida cuando no está duplicada.

    Parameters
    ----------
    out : list[Review]
        Lista de resultados acumulados.
    seen : set[str]
        Huellas ya procesadas.
    r : Review
        Reseña candidata.
    max_reviews : int or None
        Límite total opcional.

    Returns
    -------
    bool
        True si al agregar se alcanzó el límite ``max_reviews``.
    """
    if not any([r.title, r.body, r.rating, r.author, r.date_published]):
        return False
    key = fingerprint_review(r)
    if key in seen:
        return False
    seen.add(key)
    out.append(r)
    return bool(max_reviews is not None and len(out) >= max_reviews)


def scrape_requests(
    start_url: str,
    headers: dict,
    timeout: int,
    pause: float,
    max_pages: int,
    max_reviews: int | None = None,
) -> List[Review]:
    """Realiza scraping de reseñas usando la biblioteca requests.
    
    Método rápido y ligero para sitios que no requieren JavaScript.
    Extrae reseñas de múltiples páginas siguiendo enlaces de paginación.
    Combina extracción desde JSON-LD y parseo DOM (Trustpilot o genérico).
    
    Parameters
    ----------
    start_url : str
        URL inicial de la página de reseñas.
    headers : dict
        Encabezados HTTP a incluir en las solicitudes.
    timeout : int
        Timeout en segundos para cada solicitud HTTP.
    pause : float
        Pausa en segundos entre solicitudes a páginas consecutivas.
    max_pages : int
        Número máximo de páginas a procesar.
    max_reviews : int | None, default=None
        Límite máximo de reseñas (deduplicadas) a recolectar. Si es ``None``,
        no se aplica límite.
    
    Returns
    -------
    List[Review]
        Lista de todas las reseñas extraídas (puede contener duplicados).
    """
    reviews: List[Review] = []
    seen_keys: set[str] = set()
    visited = set()
    url = start_url
    pages = 0
    is_tp = _is_trustpilot(url)
    logger = setup_logger("scrape_requests")

    if max_reviews is not None and max_reviews < 1:
        raise ValueError("max_reviews debe ser >= 1")
    while url and pages < max_pages:
        if url in visited:
            break
        visited.add(url)

        logger.info(f"Descargando: {url}")
        try:
            soup = get_soup(url, headers=headers, timeout=timeout)
        except requests.exceptions.HTTPError as ex:
            logger.warning(f"No se pudo descargar (HTTPError): {url} -> {ex}")
            break
        except requests.exceptions.RequestException as ex:
            logger.warning(f"No se pudo descargar (RequestException): {url} -> {ex}")
            break

        # 0) JSON-LD primero
        jsonld_reviews = extract_from_jsonld(soup, page_url=url)
        for r in jsonld_reviews:
            if _maybe_append_review(reviews, seen_keys, r, max_reviews):
                return reviews

        # 1) DOM
        if is_tp:
            blocks = find_review_blocks_trustpilot(soup)
            for b in blocks:
                r = parse_single_review_trustpilot(b, page_url=url)
                if _maybe_append_review(reviews, seen_keys, r, max_reviews):
                    return reviews
        else:
            blocks = find_review_blocks_generic(soup)
            for b in blocks:
                r = parse_single_review_generic(b, page_url=url)
                if _maybe_append_review(reviews, seen_keys, r, max_reviews):
                    return reviews

        candidate_next = find_next_page_url(soup, url)
        if not candidate_next or candidate_next in visited:
            break

        url = candidate_next
        pages += 1
        time.sleep(pause)

    return reviews


def scrape_playwright(
    start_url: str,
    pause: float,
    max_pages: int,
    max_reviews: int | None = None,
) -> List[Review]:
    """Realiza scraping de reseñas usando Playwright (navegador headless).
    
    Método más lento pero necesario para sitios que cargan contenido
    dinámicamente mediante JavaScript. Usa Chromium en modo headless.
    
    Parameters
    ----------
    start_url : str
        URL inicial de la página de reseñas.
    pause : float
        Pausa en segundos entre navegación de páginas.
    max_pages : int
        Número máximo de páginas a procesar.
    max_reviews : int | None, default=None
        Límite máximo de reseñas (deduplicadas) a recolectar. Si es ``None``,
        no se aplica límite.
    
    Returns
    -------
    List[Review]
        Lista de todas las reseñas extraídas, o lista vacía si Playwright
        no está instalado.
    """
    logger = setup_logger("scrape_playwright")
    if not PLAYWRIGHT_AVAILABLE:
        logger.warning("Playwright no está instalado. Instala playwright y ejecuta 'playwright install'.")
        return []

    reviews: List[Review] = []
    seen_keys: set[str] = set()
    visited = set()
    url = start_url
    pages = 0
    is_tp = _is_trustpilot(url)

    if max_reviews is not None and max_reviews < 1:
        raise ValueError("max_reviews debe ser >= 1")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(locale="es-ES")
        page = ctx.new_page()

        while url and pages < max_pages:
            if url in visited:
                break
            visited.add(url)

            logger.info(f"Cargando (Playwright): {url}")
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(1500)

            html = page.content()
            soup = preferred_soup(html)

            # 0) JSON-LD
            jsonld_reviews = extract_from_jsonld(soup, page_url=url)
            for r in jsonld_reviews:
                if _maybe_append_review(reviews, seen_keys, r, max_reviews):
                    browser.close()
                    return reviews

            # 1) DOM
            if is_tp:
                blocks = find_review_blocks_trustpilot(soup)
                for b in blocks:
                    r = parse_single_review_trustpilot(b, page_url=url)
                    if _maybe_append_review(reviews, seen_keys, r, max_reviews):
                        browser.close()
                        return reviews
            else:
                blocks = find_review_blocks_generic(soup)
                for b in blocks:
                    r = parse_single_review_generic(b, page_url=url)
                    if _maybe_append_review(reviews, seen_keys, r, max_reviews):
                        browser.close()
                        return reviews

            candidate_next = find_next_page_url(soup, url)
            if not candidate_next or candidate_next in visited:
                break

            url = candidate_next
            pages += 1
            time.sleep(pause)

        browser.close()

    return reviews


# ==========================
# Deduplicación y guardado
# ==========================
def dedup_and_prune(reviews: List[Review]) -> List[Review]:
    """Elimina duplicados y reseñas vacías de una lista.
    
    Utiliza fingerprint_review() para identificar duplicados entre
    reseñas extraídas de diferentes fuentes (JSON-LD vs DOM).
    Filtra reseñas que no tienen ningún campo informativo.
    
    Parameters
    ----------
    reviews : List[Review]
        Lista de reseñas potencialmente duplicadas.
    
    Returns
    -------
    List[Review]
        Lista limpia sin duplicados ni reseñas vacías.
    """
    seen = set()
    cleaned = []
    for r in reviews:
        if not any([r.title, r.body, r.rating, r.author, r.date_published]):
            continue
        key = fingerprint_review(r)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(r)
    return cleaned


def save_csv(reviews: List[Review], path: str):
    """Guardar reseñas en un CSV con codificación UTF-8-sig.
    
    La codificación utf-8-sig incluye BOM (Byte Order Mark) para
    compatibilidad con Microsoft Excel en Windows.
    
    Parameters
    ----------
    reviews : List[Review]
        Lista de reseñas a guardar.
    path : str
        Ruta del archivo CSV de salida.

    Returns
    -------
    None
        La función escribe el archivo.
    """
    fieldnames = list(asdict(Review(None, None, None, None, None, None, None, "")).keys())
    # utf-8-sig -> Excel en Windows muestra bien tildes/ñ
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in reviews:
            writer.writerow(asdict(r))


# ==========================
# CLI / Main
# ==========================
def parse_args() -> argparse.Namespace:
    """Parsea argumentos de línea de comandos.
    
    Returns
    -------
    argparse.Namespace
        Objeto con todos los parámetros configurados desde CLI.
    """
    ap = argparse.ArgumentParser(description="Scraper de reseñas (Trustpilot).")
    ap.add_argument("--url", default=DEFAULT_START_URL, help="URL de inicio (página de reseñas).")
    ap.add_argument(
        "--out",
        default=DEFAULT_OUTPUT_CSV,
        help=(
            "Ruta del CSV de salida. Si se omite, se genera automáticamente como "
            "review_data/trustpilot_reviews_<empresa>.csv"
        ),
    )
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="Timeout por request (s).")
    ap.add_argument("--pause", type=float, default=DEFAULT_PAUSE, help="Pausa entre páginas (s).")
    ap.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES, help="Límite de páginas a recorrer.")
    ap.add_argument(
        "--max-reviews",
        type=int,
        default=DEFAULT_MAX_REVIEWS,
        help="Límite de reseñas (deduplicadas) a recolectar. Si se omite, sin límite.",
    )
    ap.add_argument("--user-agent", default=DEFAULT_UA, help="User-Agent HTTP.")
    ap.add_argument("--conservative", action="store_true",
                    help="Si robots.txt no es legible, abortar (modo conservador). Por defecto, continuar.")
    ap.add_argument("--playwright", action="store_true",
                    help="Usar Playwright si 'requests' no extrae reseñas.")
    return ap.parse_args()


def main(company: str | None = None, max_reviews: int | None = None):
    """Orquestar el proceso completo de scraping.

    Parameters
    ----------
    company : str or None, default=None
        Empresa o dominio que sustituye la URL de la CLI.
    max_reviews : int or None, default=None
        Límite que sustituye el valor de la CLI.

    Returns
    -------
    None
        La función escribe el CSV configurado.
    
    Notes
    -----
    Primero usa ``requests`` y, cuando se solicita, Playwright como fallback.
    """
    logger = setup_logger("web_scrapping")
    args = parse_args()

    # Si se proporciona explícitamente a nivel de función, sobreescribe CLI.
    if max_reviews is not None:
        args.max_reviews = max_reviews

    # Si se invoca main(company=...), ese valor gobierna el flujo completo.
    # Se construye la URL desde el nombre de empresa (p. ej. sending.es).
    if company:
        args.url = trustpilot_review_url(company)

    headers = {
        "User-Agent": args.user_agent,
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    }

    # Verificación robots.txt
    allowed = is_allowed(args.url, user_agent=args.user_agent, abort_if_unreachable=args.conservative)
    if not allowed:
        logger.warning("El scraping a esta ruta no está permitido según robots.txt o política local.")
        logger.warning("Se cotinuará por cuenta y riesgo del usuario.")

    # Scraping con requests
    reviews = scrape_requests(
        start_url=args.url,
        headers=headers,
        timeout=args.timeout,
        pause=args.pause,
        max_pages=args.max_pages,
        max_reviews=args.max_reviews,
    )

    # Fallback con Playwright si se indicó y no se obtuvo nada
    if not reviews and args.playwright:
        logger.info("No se obtuvieron reseñas con requests. Probando con Playwright...")
        reviews = scrape_playwright(
            start_url=args.url,
            pause=args.pause,
            max_pages=args.max_pages,
            max_reviews=args.max_reviews,
        )

    # Deduplicar y limpiar
    reviews = dedup_and_prune(reviews)

    # Asegura el límite también después de deduplicar (por seguridad).
    if args.max_reviews is not None:
        if args.max_reviews < 1:
            raise ValueError("max_reviews debe ser >= 1")
        reviews = reviews[: args.max_reviews]

    logger.info(f"Total de reseñas recolectadas: {len(reviews)}")
    out_path = resolve_output_path(args.url, args.out, company=company)
    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    save_csv(reviews, out_path)
    logger.info(f"CSV guardado en: {out_path}")


if __name__ == "__main__":
    main()
