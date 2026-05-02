"""
=============================================================================
 MÓDULO 1: SCRAPER Y FILTRO DE CALIDAD (scraper.py)
=============================================================================

 Descripción:
 ------------
 Este módulo se encarga de buscar restaurantes en una ciudad dada usando
 la API de Google Maps (Places API). Aplica filtros estrictos de calidad
 para quedarnos SOLO con negocios que:
   1. NO tienen web propia (solo redes sociales o nada).
   2. Están ACTIVOS (reseñas recientes, fotos, horarios).

 Flujo de ejecución:
 -------------------
 1. search_restaurants(city) -> Busca restaurantes en Google Places.
 2. _fetch_place_details(place_id) -> Obtiene detalles completos.
 3. _filter_has_own_website(place) -> Descarta los que ya tienen web.
 4. _filter_is_active(place) -> Descarta negocios inactivos/abandonados.
 5. _extract_lead_data(place) -> Extrae datos limpios del lead.
 6. prospect_city(city) -> Orquesta todo el pipeline para una ciudad.

 Dependencias:
 -------------
 - requests: Para llamadas HTTP a Google Places API.
 - logging: Para registro de actividad.

 Autor: Restaurant SaaS System
 Versión: 1.0.0
=============================================================================
"""

import requests
import logging
import time
import re
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlparse

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import (
    GOOGLE_MAPS_API_KEY,
    SCRAPER_CONFIG,
)

# =============================================================================
# CONFIGURACIÓN DEL LOGGER
# =============================================================================
logger = logging.getLogger("scraper")
logger.setLevel(logging.INFO)

if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(handler)


# =============================================================================
# CLASE PRINCIPAL: RestaurantScraper
# =============================================================================
class RestaurantScraper:
    """
    Scraper inteligente de restaurantes sin presencia web.

    Utiliza Google Places API para buscar restaurantes en una ciudad,
    filtrar los que ya tienen web propia y quedarse solo con leads
    de negocios activos pero sin digitalizar.

    Atributos:
        api_key (str): API key de Google Maps.
        config (dict): Configuración del scraper desde settings.
        session (requests.Session): Sesión HTTP reutilizable.
        stats (dict): Estadísticas de la ejecución actual.

    Ejemplo de uso:
        >>> scraper = RestaurantScraper()
        >>> leads = scraper.prospect_city("Madrid, España")
        >>> print(f"Encontrados {len(leads)} leads cualificados")
    """

    # URLs base de Google Places API
    PLACES_NEARBY_URL = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
    PLACES_TEXT_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    PLACES_DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"
    GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
    PLACE_PHOTO_URL = "https://maps.googleapis.com/maps/api/place/photo"

    def __init__(self, api_key: Optional[str] = None):
        """
        Inicializa el scraper con la API key y configuración.

        Args:
            api_key: Google Maps API key. Si no se provee, usa la de settings.

        Raises:
            ValueError: Si no se encuentra API key.
        """
        self.api_key = api_key or GOOGLE_MAPS_API_KEY
        self.config = SCRAPER_CONFIG
        self.session = requests.Session()

        # Estadísticas de ejecución
        self.stats = {
            "total_found": 0,
            "filtered_has_website": 0,
            "filtered_inactive": 0,
            "filtered_no_phone": 0,
            "qualified_leads": 0,
            "api_calls": 0,
            "errors": 0,
        }

        if not self.api_key:
            logger.warning(
                "⚠️  No se encontró GOOGLE_MAPS_API_KEY. "
                "El scraper funcionará en modo DEMO con datos simulados."
            )

    # =========================================================================
    # MÉTODO PRINCIPAL: Prospectar una ciudad completa
    # =========================================================================
    def prospect_city(self, city: str, keywords: Optional[list] = None) -> list[dict]:
        """
        Pipeline completo de prospección para una ciudad.

        Busca restaurantes, filtra por calidad y devuelve leads cualificados.

        Args:
            city: Nombre de la ciudad (ej: "Madrid, España").
            keywords: Lista opcional de keywords adicionales de búsqueda.
                     Ej: ["restaurante", "bar de tapas", "cafetería"]

        Returns:
            Lista de diccionarios con datos de leads cualificados.
            Cada dict contiene: nombre, telefono, direccion, horarios,
            fotos_urls, resenas, rating, coordenadas, place_id, etc.

        Ejemplo:
            >>> scraper = RestaurantScraper()
            >>> leads = scraper.prospect_city("Sevilla, España")
            >>> for lead in leads:
            ...     print(f"{lead['nombre']} - {lead['telefono']}")
        """
        logger.info(f"🔍 Iniciando prospección en: {city}")
        self._reset_stats()

        # Si no hay API key, usar modo demo
        if not self.api_key:
            logger.info("📦 Modo DEMO activado - Generando datos de ejemplo")
            return self._generate_demo_data(city)

        # Paso 1: Obtener coordenadas de la ciudad
        lat, lng = self._geocode_city(city)
        if lat is None or lng is None:
            logger.error(f"❌ No se pudo geocodificar la ciudad: {city}")
            return []

        logger.info(f"📍 Coordenadas: {lat}, {lng}")

        # Paso 2: Buscar restaurantes con diferentes keywords
        if keywords is None:
            keywords = ["restaurante", "bar", "cafetería", "comida"]

        all_places = []
        seen_place_ids = set()

        for keyword in keywords:
            query = f"{keyword} en {city}"
            places = self._search_places(query, lat, lng)

            for place in places:
                pid = place.get("place_id")
                if pid and pid not in seen_place_ids:
                    seen_place_ids.add(pid)
                    all_places.append(place)

            # Respetar rate limiting
            time.sleep(self.config["request_delay_seconds"])

        self.stats["total_found"] = len(all_places)
        logger.info(f"📊 Total de restaurantes encontrados: {len(all_places)}")

        # Paso 3: Obtener detalles y filtrar cada lugar
        qualified_leads = []

        for i, place in enumerate(all_places, 1):
            place_id = place.get("place_id")
            place_name = place.get("name", "Desconocido")

            logger.info(
                f"🔄 Procesando {i}/{len(all_places)}: {place_name}"
            )

            # Obtener detalles completos del lugar
            details = self._fetch_place_details(place_id)
            if not details:
                self.stats["errors"] += 1
                continue

            # Filtro 1: ¿Tiene web propia?
            if self._has_own_website(details):
                self.stats["filtered_has_website"] += 1
                logger.info(f"  ❌ Descartado (tiene web propia): {place_name}")
                continue

            # Filtro 2: ¿Está activo?
            if not self._is_active_business(details):
                self.stats["filtered_inactive"] += 1
                logger.info(f"  ❌ Descartado (negocio inactivo): {place_name}")
                continue

            # Filtro 3: ¿Tiene teléfono?
            if not self._has_phone(details):
                self.stats["filtered_no_phone"] += 1
                logger.info(f"  ❌ Descartado (sin teléfono): {place_name}")
                continue

            # Extraer datos del lead
            lead_data = self._extract_lead_data(details, city)
            if lead_data:
                qualified_leads.append(lead_data)
                self.stats["qualified_leads"] += 1
                logger.info(
                    f"  ✅ Lead cualificado: {place_name} "
                    f"({lead_data['telefono']})"
                )

            # Rate limiting entre requests de detalles
            time.sleep(self.config["request_delay_seconds"])

        # Imprimir resumen
        self._print_stats()
        return qualified_leads

    # =========================================================================
    # GEOCODING: Obtener coordenadas de una ciudad
    # =========================================================================
    def _geocode_city(self, city: str) -> tuple[Optional[float], Optional[float]]:
        """
        Convierte el nombre de una ciudad a coordenadas (lat, lng).

        Args:
            city: Nombre de la ciudad.

        Returns:
            Tupla (latitud, longitud) o (None, None) si falla.
        """
        try:
            params = {
                "address": city,
                "key": self.api_key,
                "language": self.config["language"],
                "region": self.config["region"],
            }
            response = self.session.get(self.GEOCODE_URL, params=params, timeout=10)
            self.stats["api_calls"] += 1
            response.raise_for_status()

            data = response.json()
            if data.get("status") == "OK" and data.get("results"):
                location = data["results"][0]["geometry"]["location"]
                return location["lat"], location["lng"]

            logger.warning(f"⚠️  Geocoding status: {data.get('status')}")
            return None, None

        except requests.RequestException as e:
            logger.error(f"❌ Error en geocoding: {e}")
            self.stats["errors"] += 1
            return None, None

    # =========================================================================
    # BÚSQUEDA: Buscar restaurantes en Google Places
    # =========================================================================
    def _search_places(
        self, query: str, lat: float, lng: float
    ) -> list[dict]:
        """
        Busca restaurantes usando Google Places Text Search API.

        Maneja paginación automática (next_page_token) para obtener
        todos los resultados disponibles (hasta 60 por búsqueda).

        Args:
            query: Término de búsqueda (ej: "restaurante en Madrid").
            lat: Latitud del centro de búsqueda.
            lng: Longitud del centro de búsqueda.

        Returns:
            Lista de diccionarios con datos básicos de cada lugar.
        """
        all_results = []
        next_page_token = None

        while True:
            try:
                params = {
                    "query": query,
                    "location": f"{lat},{lng}",
                    "radius": self.config["search_radius_meters"],
                    "type": "restaurant",
                    "language": self.config["language"],
                    "key": self.api_key,
                }

                if next_page_token:
                    params["pagetoken"] = next_page_token

                response = self.session.get(
                    self.PLACES_TEXT_URL, params=params, timeout=15
                )
                self.stats["api_calls"] += 1
                response.raise_for_status()

                data = response.json()

                if data.get("status") not in ("OK", "ZERO_RESULTS"):
                    logger.warning(
                        f"⚠️  Places API status: {data.get('status')} - "
                        f"{data.get('error_message', '')}"
                    )
                    break

                results = data.get("results", [])
                all_results.extend(results)

                logger.info(
                    f"  📄 Página obtenida: {len(results)} resultados "
                    f"(Total: {len(all_results)})"
                )

                # Verificar si hay más páginas
                next_page_token = data.get("next_page_token")
                if not next_page_token:
                    break

                # Google requiere un delay antes de usar next_page_token
                time.sleep(2)

                # Verificar si alcanzamos el máximo
                if len(all_results) >= self.config["max_results_per_query"]:
                    break

            except requests.RequestException as e:
                logger.error(f"❌ Error en búsqueda: {e}")
                self.stats["errors"] += 1
                break

        return all_results

    # =========================================================================
    # DETALLES: Obtener información completa de un lugar
    # =========================================================================
    def _fetch_place_details(self, place_id: str) -> Optional[dict]:
        """
        Obtiene detalles completos de un restaurante por su place_id.

        Solicita campos específicos para minimizar costos de API:
        - Contacto: teléfono, website, horarios
        - Info: nombre, dirección, tipo de cocina
        - Métricas: rating, reseñas, fotos
        - Actividad: última reseña, estado operativo

        Args:
            place_id: ID único del lugar en Google Places.

        Returns:
            Diccionario con detalles del lugar, o None si falla.
        """
        try:
            # Campos que necesitamos (optimizado para costo)
            fields = ",".join([
                # Básicos
                "name",
                "place_id",
                "formatted_address",
                "geometry",
                "types",
                "business_status",
                # Contacto
                "formatted_phone_number",
                "international_phone_number",
                "website",
                "url",  # URL de Google Maps
                # Métricas
                "rating",
                "user_ratings_total",
                "price_level",
                # Contenido
                "photos",
                "reviews",
                # Operación
                "opening_hours",
                "utc_offset",
            ])

            params = {
                "place_id": place_id,
                "fields": fields,
                "language": self.config["language"],
                "key": self.api_key,
            }

            response = self.session.get(
                self.PLACES_DETAILS_URL, params=params, timeout=15
            )
            self.stats["api_calls"] += 1
            response.raise_for_status()

            data = response.json()

            if data.get("status") == "OK":
                return data.get("result", {})

            logger.warning(
                f"⚠️  Details API status: {data.get('status')} "
                f"para place_id: {place_id}"
            )
            return None

        except requests.RequestException as e:
            logger.error(f"❌ Error obteniendo detalles ({place_id}): {e}")
            self.stats["errors"] += 1
            return None

    # =========================================================================
    # FILTRO 1: ¿El restaurante tiene web propia?
    # =========================================================================
    def _has_own_website(self, details: dict) -> bool:
        """
        Determina si un restaurante tiene una web PROPIA.

        Lógica de filtrado:
        - Si NO tiene campo 'website' -> NO tiene web -> ACEPTAR (return False)
        - Si el website es de redes sociales -> NO cuenta como web propia -> ACEPTAR
        - Si el website es un dominio propio (.com, .es, etc.) -> DESCARTAR (return True)

        Args:
            details: Diccionario con detalles del lugar de Google Places.

        Returns:
            True si tiene web propia (DESCARTAR).
            False si no tiene web propia (ACEPTAR como lead).
        """
        website = details.get("website", "")

        # Si no tiene website, perfecto -> es un lead potencial
        if not website:
            return False

        website_lower = website.lower().strip()

        # Verificar si es una URL de red social (no cuenta como web propia)
        for social_domain in self.config["social_media_domains"]:
            if social_domain in website_lower:
                logger.debug(
                    f"  ℹ️  Web es red social ({social_domain}): {website}"
                )
                return False  # Red social = no tiene web propia

        # Si llegamos aquí, tiene una web que no es red social
        # Verificar si parece un dominio propio
        try:
            parsed = urlparse(website_lower)
            domain = parsed.netloc or parsed.path

            # Limpiar el dominio
            domain = domain.split("/")[0].split("?")[0]

            # Verificar extensiones de dominio propio
            for ext in self.config["owned_website_extensions"]:
                if domain.endswith(ext):
                    logger.debug(
                        f"  ℹ️  Tiene web propia ({ext}): {website}"
                    )
                    return True  # Tiene web propia -> DESCARTAR

        except Exception:
            pass

        # Si no pudimos determinar, asumir que tiene web propia
        # (mejor descartar un lead dudoso que molestar a quien ya tiene web)
        return True

    # =========================================================================
    # FILTRO 2: ¿El negocio está activo?
    # =========================================================================
    def _is_active_business(self, details: dict) -> bool:
        """
        Determina si un restaurante está activo y operando.

        Criterios de actividad:
        1. business_status debe ser "OPERATIONAL"
        2. Rating >= umbral mínimo configurado
        3. Número de reseñas >= umbral mínimo
        4. Última reseña no más antigua que max_review_age_days
        5. Tiene al menos min_photos fotos

        Args:
            details: Diccionario con detalles del lugar.

        Returns:
            True si el negocio está activo (ACEPTAR).
            False si parece inactivo/abandonado (DESCARTAR).
        """
        # Criterio 1: Estado operativo
        business_status = details.get("business_status", "OPERATIONAL")
        if business_status != "OPERATIONAL":
            logger.debug(
                f"  ℹ️  Estado no operativo: {business_status}"
            )
            return False

        # Criterio 2: Rating mínimo
        rating = details.get("rating", 0)
        if rating < self.config["min_rating"]:
            logger.debug(
                f"  ℹ️  Rating muy bajo: {rating} "
                f"(mínimo: {self.config['min_rating']})"
            )
            return False

        # Criterio 3: Número mínimo de reseñas
        total_reviews = details.get("user_ratings_total", 0)
        if total_reviews < self.config["min_reviews"]:
            logger.debug(
                f"  ℹ️  Pocas reseñas: {total_reviews} "
                f"(mínimo: {self.config['min_reviews']})"
            )
            return False

        # Criterio 4: Antigüedad de la última reseña
        reviews = details.get("reviews", [])
        if reviews:
            latest_review_time = max(
                r.get("time", 0) for r in reviews
            )
            if latest_review_time > 0:
                latest_date = datetime.fromtimestamp(latest_review_time)
                max_age = timedelta(
                    days=self.config["max_review_age_days"]
                )
                if datetime.now() - latest_date > max_age:
                    logger.debug(
                        f"  ℹ️  Última reseña muy antigua: {latest_date}"
                    )
                    return False

        # Criterio 5: Tiene fotos
        photos = details.get("photos", [])
        if len(photos) < self.config["min_photos"]:
            logger.debug(
                f"  ℹ️  Pocas fotos: {len(photos)} "
                f"(mínimo: {self.config['min_photos']})"
            )
            return False

        return True

    # =========================================================================
    # FILTRO 3: ¿Tiene teléfono?
    # =========================================================================
    def _has_phone(self, details: dict) -> bool:
        """
        Verifica si el restaurante tiene número de teléfono.

        Args:
            details: Diccionario con detalles del lugar.

        Returns:
            True si tiene teléfono.
        """
        phone = details.get("formatted_phone_number") or details.get(
            "international_phone_number"
        )
        return bool(phone and phone.strip())

    # =========================================================================
    # EXTRACCIÓN: Limpiar y estructurar datos del lead
    # =========================================================================
    def _extract_lead_data(self, details: dict, city: str) -> Optional[dict]:
        """
        Extrae y estructura los datos relevantes de un restaurante
        para convertirlo en un lead procesable.

        Args:
            details: Diccionario con detalles completos del lugar.
            city: Ciudad donde se encontró.

        Returns:
            Diccionario con datos del lead estructurados, o None si falla.
        """
        try:
            # Limpiar teléfono
            phone_raw = (
                details.get("international_phone_number")
                or details.get("formatted_phone_number")
                or ""
            )
            phone_clean = self._clean_phone_number(phone_raw)

            if not phone_clean:
                return None

            # Extraer URLs de fotos
            photos = details.get("photos", [])
            photo_urls = []
            for photo in photos[:5]:  # Máximo 5 fotos
                photo_ref = photo.get("photo_reference")
                if photo_ref:
                    photo_url = (
                        f"{self.PLACE_PHOTO_URL}"
                        f"?maxwidth=800"
                        f"&photo_reference={photo_ref}"
                        f"&key={self.api_key}"
                    )
                    photo_urls.append(photo_url)

            # Extraer reseñas destacadas
            reviews = details.get("reviews", [])
            featured_reviews = []
            for review in sorted(
                reviews, key=lambda r: r.get("rating", 0), reverse=True
            )[:3]:  # Top 3 reseñas por rating
                featured_reviews.append({
                    "autor": review.get("author_name", "Anónimo"),
                    "rating": review.get("rating", 0),
                    "texto": review.get("text", ""),
                    "fecha": review.get("relative_time_description", ""),
                    "timestamp": review.get("time", 0),
                })

            # Extraer horarios
            opening_hours = details.get("opening_hours", {})
            horarios = opening_hours.get("weekday_text", [])

            # Determinar tipo de cocina (de los types de Google)
            types = details.get("types", [])
            tipo_cocina = self._determine_cuisine_type(types)

            # Construir objeto lead
            lead = {
                "nombre": details.get("name", "Desconocido"),
                "telefono": phone_clean,
                "telefono_raw": phone_raw,
                "direccion": details.get("formatted_address", ""),
                "ciudad": city,
                "coordenadas": {
                    "lat": details.get("geometry", {})
                    .get("location", {}).get("lat"),
                    "lng": details.get("geometry", {})
                    .get("location", {}).get("lng"),
                },
                "rating": details.get("rating", 0),
                "total_resenas": details.get("user_ratings_total", 0),
                "nivel_precio": details.get("price_level"),
                "tipo_cocina": tipo_cocina,
                "horarios": horarios,
                "fotos_urls": photo_urls,
                "resenas_destacadas": featured_reviews,
                "google_maps_url": details.get("url", ""),
                "website_actual": details.get("website", ""),
                "place_id": details.get("place_id", ""),
                "business_status": details.get(
                    "business_status", "OPERATIONAL"
                ),
                "fecha_extraccion": datetime.now().isoformat(),
            }

            return lead

        except Exception as e:
            logger.error(f"❌ Error extrayendo datos del lead: {e}")
            return None

    # =========================================================================
    # UTILIDADES INTERNAS
    # =========================================================================
    def _clean_phone_number(self, phone: str) -> str:
        """
        Limpia y normaliza un número de teléfono.

        Elimina espacios, guiones, paréntesis y prefijos internacionales
        para obtener un número limpio de 9 dígitos (España).

        Args:
            phone: Número de teléfono en cualquier formato.

        Returns:
            Número limpio de 9 dígitos, o cadena vacía si no es válido.

        Ejemplos:
            >>> scraper._clean_phone_number("+34 612 345 678")
            '612345678'
            >>> scraper._clean_phone_number("91 234 56 78")
            '912345678'
        """
        if not phone:
            return ""

        # Eliminar todo excepto dígitos
        digits = re.sub(r"[^\d]", "", phone)

        # Eliminar prefijo de país (+34)
        if digits.startswith("34") and len(digits) > 9:
            digits = digits[2:]

        # Verificar que tenga 9 dígitos (formato España)
        if len(digits) == 9:
            return digits

        return ""

    def _determine_cuisine_type(self, types: list[str]) -> str:
        """
        Determina el tipo de cocina basándose en los types de Google Places.

        Args:
            types: Lista de tipos de Google Places.

        Returns:
            Descripción del tipo de cocina.
        """
        cuisine_mapping = {
            "restaurant": "Restaurante",
            "cafe": "Cafetería",
            "bar": "Bar",
            "bakery": "Panadería/Pastelería",
            "meal_takeaway": "Comida para llevar",
            "meal_delivery": "Comida a domicilio",
            "night_club": "Club nocturno",
            "food": "Comida",
        }

        for place_type in types:
            if place_type in cuisine_mapping:
                return cuisine_mapping[place_type]

        return "Restaurante"

    def _reset_stats(self):
        """Resetea las estadísticas de ejecución."""
        self.stats = {
            "total_found": 0,
            "filtered_has_website": 0,
            "filtered_inactive": 0,
            "filtered_no_phone": 0,
            "qualified_leads": 0,
            "api_calls": 0,
            "errors": 0,
        }

    def _print_stats(self):
        """Imprime un resumen de las estadísticas de ejecución."""
        logger.info("=" * 60)
        logger.info("📊 RESUMEN DE PROSPECCIÓN")
        logger.info("=" * 60)
        logger.info(f"  Total encontrados:       {self.stats['total_found']}")
        logger.info(f"  Descartados (con web):   {self.stats['filtered_has_website']}")
        logger.info(f"  Descartados (inactivos): {self.stats['filtered_inactive']}")
        logger.info(f"  Descartados (sin tel):   {self.stats['filtered_no_phone']}")
        logger.info(f"  ✅ Leads cualificados:   {self.stats['qualified_leads']}")
        logger.info(f"  Llamadas a API:          {self.stats['api_calls']}")
        logger.info(f"  Errores:                 {self.stats['errors']}")
        logger.info("=" * 60)

    # =========================================================================
    # MODO DEMO: Datos simulados para pruebas sin API key
    # =========================================================================
    def _generate_demo_data(self, city: str) -> list[dict]:
        """
        Genera datos de ejemplo para probar el sistema sin API key.

        Crea 8 leads de ejemplo con datos realistas que simulan
        restaurantes sin presencia web en la ciudad indicada.

        Args:
            city: Ciudad para los datos de ejemplo.

        Returns:
            Lista de diccionarios con leads de ejemplo.
        """
        demo_leads = [
            {
                "nombre": "Bar El Rincón de Pepe",
                "telefono": "612345678",
                "telefono_raw": "+34 612 345 678",
                "direccion": f"Calle Mayor 15, {city}",
                "ciudad": city,
                "coordenadas": {"lat": 40.4168, "lng": -3.7038},
                "rating": 4.3,
                "total_resenas": 87,
                "nivel_precio": 2,
                "tipo_cocina": "Bar",
                "horarios": [
                    "Lunes: 08:00–23:00",
                    "Martes: 08:00–23:00",
                    "Miércoles: 08:00–23:00",
                    "Jueves: 08:00–23:00",
                    "Viernes: 08:00–01:00",
                    "Sábado: 09:00–01:00",
                    "Domingo: 09:00–16:00",
                ],
                "fotos_urls": [
                    "https://example.com/photo1.jpg",
                    "https://example.com/photo2.jpg",
                ],
                "resenas_destacadas": [
                    {
                        "autor": "María García",
                        "rating": 5,
                        "texto": "Las mejores tapas del barrio. El pulpo está increíble.",
                        "fecha": "hace 2 semanas",
                        "timestamp": int(datetime.now().timestamp()) - 1209600,
                    },
                    {
                        "autor": "Juan López",
                        "rating": 4,
                        "texto": "Buen ambiente y precios razonables. Recomendado.",
                        "fecha": "hace 1 mes",
                        "timestamp": int(datetime.now().timestamp()) - 2592000,
                    },
                ],
                "google_maps_url": "https://maps.google.com/?cid=123456",
                "website_actual": "",
                "place_id": "ChIJDEMO001",
                "business_status": "OPERATIONAL",
                "fecha_extraccion": datetime.now().isoformat(),
            },
            {
                "nombre": "Restaurante Casa Manolo",
                "telefono": "915678901",
                "telefono_raw": "+34 915 678 901",
                "direccion": f"Avenida de la Constitución 42, {city}",
                "ciudad": city,
                "coordenadas": {"lat": 40.4200, "lng": -3.7100},
                "rating": 4.5,
                "total_resenas": 156,
                "nivel_precio": 2,
                "tipo_cocina": "Restaurante",
                "horarios": [
                    "Lunes: Cerrado",
                    "Martes: 13:00–16:00, 20:00–23:30",
                    "Miércoles: 13:00–16:00, 20:00–23:30",
                    "Jueves: 13:00–16:00, 20:00–23:30",
                    "Viernes: 13:00–16:00, 20:00–00:00",
                    "Sábado: 13:00–00:00",
                    "Domingo: 13:00–17:00",
                ],
                "fotos_urls": [
                    "https://example.com/photo3.jpg",
                    "https://example.com/photo4.jpg",
                    "https://example.com/photo5.jpg",
                ],
                "resenas_destacadas": [
                    {
                        "autor": "Carlos Ruiz",
                        "rating": 5,
                        "texto": "Cocina casera de verdad. El cocido madrileño espectacular.",
                        "fecha": "hace 1 semana",
                        "timestamp": int(datetime.now().timestamp()) - 604800,
                    },
                ],
                "google_maps_url": "https://maps.google.com/?cid=789012",
                "website_actual": "",
                "place_id": "ChIJDEMO002",
                "business_status": "OPERATIONAL",
                "fecha_extraccion": datetime.now().isoformat(),
            },
            {
                "nombre": "Cafetería La Esquina",
                "telefono": "698765432",
                "telefono_raw": "+34 698 765 432",
                "direccion": f"Plaza del Sol 3, {city}",
                "ciudad": city,
                "coordenadas": {"lat": 40.4170, "lng": -3.7035},
                "rating": 4.1,
                "total_resenas": 45,
                "nivel_precio": 1,
                "tipo_cocina": "Cafetería",
                "horarios": [
                    "Lunes a Viernes: 07:00–20:00",
                    "Sábado: 08:00–14:00",
                    "Domingo: Cerrado",
                ],
                "fotos_urls": ["https://example.com/photo6.jpg"],
                "resenas_destacadas": [
                    {
                        "autor": "Ana Martínez",
                        "rating": 4,
                        "texto": "Desayunos geniales y café excelente.",
                        "fecha": "hace 3 semanas",
                        "timestamp": int(datetime.now().timestamp()) - 1814400,
                    },
                ],
                "google_maps_url": "https://maps.google.com/?cid=345678",
                "website_actual": "https://www.facebook.com/laesquinacafe",
                "place_id": "ChIJDEMO003",
                "business_status": "OPERATIONAL",
                "fecha_extraccion": datetime.now().isoformat(),
            },
            {
                "nombre": "Tapas El Andaluz",
                "telefono": "712345098",
                "telefono_raw": "+34 712 345 098",
                "direccion": f"Calle de la Luna 8, {city}",
                "ciudad": city,
                "coordenadas": {"lat": 40.4210, "lng": -3.7090},
                "rating": 4.7,
                "total_resenas": 203,
                "nivel_precio": 2,
                "tipo_cocina": "Bar",
                "horarios": [
                    "Lunes: 12:00–00:00",
                    "Martes: 12:00–00:00",
                    "Miércoles: 12:00–00:00",
                    "Jueves: 12:00–00:00",
                    "Viernes: 12:00–02:00",
                    "Sábado: 12:00–02:00",
                    "Domingo: 12:00–18:00",
                ],
                "fotos_urls": [
                    "https://example.com/photo7.jpg",
                    "https://example.com/photo8.jpg",
                ],
                "resenas_destacadas": [
                    {
                        "autor": "Pedro Sánchez",
                        "rating": 5,
                        "texto": "El mejor salmorejo que he probado fuera de Córdoba.",
                        "fecha": "hace 5 días",
                        "timestamp": int(datetime.now().timestamp()) - 432000,
                    },
                    {
                        "autor": "Laura Fernández",
                        "rating": 5,
                        "texto": "Ambiente auténtico y tapas de 10. Imprescindible.",
                        "fecha": "hace 2 semanas",
                        "timestamp": int(datetime.now().timestamp()) - 1209600,
                    },
                ],
                "google_maps_url": "https://maps.google.com/?cid=901234",
                "website_actual": "",
                "place_id": "ChIJDEMO004",
                "business_status": "OPERATIONAL",
                "fecha_extraccion": datetime.now().isoformat(),
            },
            {
                "nombre": "Pizzería Don Giovanni",
                "telefono": "876543210",
                "telefono_raw": "+34 876 543 210",
                "direccion": f"Calle Gran Vía 25, {city}",
                "ciudad": city,
                "coordenadas": {"lat": 40.4205, "lng": -3.7060},
                "rating": 4.0,
                "total_resenas": 92,
                "nivel_precio": 2,
                "tipo_cocina": "Restaurante",
                "horarios": [
                    "Lunes a Domingo: 12:00–23:30",
                ],
                "fotos_urls": [
                    "https://example.com/photo9.jpg",
                    "https://example.com/photo10.jpg",
                ],
                "resenas_destacadas": [
                    {
                        "autor": "Roberto Iglesias",
                        "rating": 4,
                        "texto": "Pizza al horno de leña muy buena. La masa es espectacular.",
                        "fecha": "hace 1 mes",
                        "timestamp": int(datetime.now().timestamp()) - 2592000,
                    },
                ],
                "google_maps_url": "https://maps.google.com/?cid=567890",
                "website_actual": "",
                "place_id": "ChIJDEMO005",
                "business_status": "OPERATIONAL",
                "fecha_extraccion": datetime.now().isoformat(),
            },
            {
                "nombre": "Asador Los Arcos",
                "telefono": "934567890",
                "telefono_raw": "+34 934 567 890",
                "direccion": f"Paseo de la Castellana 100, {city}",
                "ciudad": city,
                "coordenadas": {"lat": 40.4300, "lng": -3.6900},
                "rating": 4.6,
                "total_resenas": 178,
                "nivel_precio": 3,
                "tipo_cocina": "Restaurante",
                "horarios": [
                    "Lunes: Cerrado",
                    "Martes a Sábado: 13:00–16:00, 20:30–23:30",
                    "Domingo: 13:00–17:00",
                ],
                "fotos_urls": [
                    "https://example.com/photo11.jpg",
                    "https://example.com/photo12.jpg",
                    "https://example.com/photo13.jpg",
                ],
                "resenas_destacadas": [
                    {
                        "autor": "Miguel Torres",
                        "rating": 5,
                        "texto": "Chuletón espectacular. El mejor asador de la zona.",
                        "fecha": "hace 4 días",
                        "timestamp": int(datetime.now().timestamp()) - 345600,
                    },
                ],
                "google_maps_url": "https://maps.google.com/?cid=234567",
                "website_actual": "",
                "place_id": "ChIJDEMO006",
                "business_status": "OPERATIONAL",
                "fecha_extraccion": datetime.now().isoformat(),
            },
            {
                "nombre": "Taberna La Traviesa",
                "telefono": "654321987",
                "telefono_raw": "+34 654 321 987",
                "direccion": f"Calle del Pez 12, {city}",
                "ciudad": city,
                "coordenadas": {"lat": 40.4230, "lng": -3.7050},
                "rating": 4.4,
                "total_resenas": 124,
                "nivel_precio": 1,
                "tipo_cocina": "Bar",
                "horarios": [
                    "Lunes a Jueves: 18:00–01:00",
                    "Viernes y Sábado: 18:00–03:00",
                    "Domingo: 18:00–00:00",
                ],
                "fotos_urls": ["https://example.com/photo14.jpg"],
                "resenas_destacadas": [
                    {
                        "autor": "Elena Díaz",
                        "rating": 5,
                        "texto": "Cañas y vermut como en ningún sitio. La tapa de croquetas, top.",
                        "fecha": "hace 1 semana",
                        "timestamp": int(datetime.now().timestamp()) - 604800,
                    },
                ],
                "google_maps_url": "https://maps.google.com/?cid=678901",
                "website_actual": "https://www.instagram.com/tabernatraviesa",
                "place_id": "ChIJDEMO007",
                "business_status": "OPERATIONAL",
                "fecha_extraccion": datetime.now().isoformat(),
            },
            {
                "nombre": "Sushi Zen",
                "telefono": "678901234",
                "telefono_raw": "+34 678 901 234",
                "direccion": f"Calle Serrano 55, {city}",
                "ciudad": city,
                "coordenadas": {"lat": 40.4280, "lng": -3.6850},
                "rating": 4.8,
                "total_resenas": 312,
                "nivel_precio": 3,
                "tipo_cocina": "Restaurante",
                "horarios": [
                    "Lunes: Cerrado",
                    "Martes a Domingo: 13:00–15:30, 20:00–23:00",
                ],
                "fotos_urls": [
                    "https://example.com/photo15.jpg",
                    "https://example.com/photo16.jpg",
                    "https://example.com/photo17.jpg",
                    "https://example.com/photo18.jpg",
                ],
                "resenas_destacadas": [
                    {
                        "autor": "Yuki Tanaka",
                        "rating": 5,
                        "texto": "Auténtico sushi japonés. El chef sabe lo que hace.",
                        "fecha": "hace 3 días",
                        "timestamp": int(datetime.now().timestamp()) - 259200,
                    },
                    {
                        "autor": "Raquel Moreno",
                        "rating": 5,
                        "texto": "Omakase increíble. Merece cada euro.",
                        "fecha": "hace 1 semana",
                        "timestamp": int(datetime.now().timestamp()) - 604800,
                    },
                ],
                "google_maps_url": "https://maps.google.com/?cid=456789",
                "website_actual": "",
                "place_id": "ChIJDEMO008",
                "business_status": "OPERATIONAL",
                "fecha_extraccion": datetime.now().isoformat(),
            },
        ]

        self.stats["total_found"] = 12
        self.stats["filtered_has_website"] = 3
        self.stats["filtered_inactive"] = 1
        self.stats["qualified_leads"] = len(demo_leads)

        logger.info(f"✅ Generados {len(demo_leads)} leads de ejemplo para {city}")
        self._print_stats()

        return demo_leads


# =============================================================================
# EJECUCIÓN DIRECTA (para pruebas)
# =============================================================================
if __name__ == "__main__":
    """
    Ejecutar directamente para probar el scraper:
        python -m modules.scraper
        # o
        python modules/scraper.py
    """
    print("=" * 60)
    print("🍽️  RESTAURANT SAAS - Módulo de Scraping")
    print("=" * 60)

    # Crear instancia del scraper
    scraper = RestaurantScraper()

    # Ejecutar prospección (usará modo demo si no hay API key)
    leads = scraper.prospect_city("Madrid, España")

    # Mostrar resultados
    print(f"\n✅ Se encontraron {len(leads)} leads cualificados:\n")
    for i, lead in enumerate(leads, 1):
        print(f"  {i}. {lead['nombre']}")
        print(f"     📞 Teléfono: {lead['telefono']}")
        print(f"     📍 Dirección: {lead['direccion']}")
        print(f"     ⭐ Rating: {lead['rating']} ({lead['total_resenas']} reseñas)")
        print(f"     🍽️  Tipo: {lead['tipo_cocina']}")
        print(f"     🌐 Web actual: {lead['website_actual'] or 'Ninguna'}")
        print()
