"""
=============================================================================
 RESTAURANT SAAS - Configuración Central del Sistema
=============================================================================
 Archivo de configuración con todas las API keys, rutas y parámetros.

 IMPORTANTE: En producción, usar variables de entorno o un .env
 Nunca commitear API keys reales en el repositorio.
=============================================================================
"""

import os
from pathlib import Path

# =============================================================================
# RUTAS DEL PROYECTO
# =============================================================================
BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "restaurant_leads.db"
OUTPUT_DIR = BASE_DIR / "output"
LOGS_DIR = BASE_DIR / "logs"
SCREENSHOTS_DIR = BASE_DIR / "output" / "screenshots"

# Crear directorios si no existen
for directory in [DB_PATH.parent, OUTPUT_DIR, LOGS_DIR, SCREENSHOTS_DIR]:
    directory.mkdir(parents=True, exist_ok=True)

# =============================================================================
# API KEYS (Usar variables de entorno en producción)
# =============================================================================
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
VERCEL_API_TOKEN = os.getenv("VERCEL_API_TOKEN", "")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
TELEGRAM_BOT_TOKEN_LLAMADAS = os.getenv("TELEGRAM_BOT_TOKEN_LLAMADAS", "")
TELEGRAM_BOT_TOKEN_ERRORES = os.getenv("TELEGRAM_BOT_TOKEN_ERRORES", "")
TELEGRAM_CHAT_ID_LLAMADAS = os.getenv("TELEGRAM_CHAT_ID_LLAMADAS", "")
TELEGRAM_CHAT_ID_ERRORES = os.getenv("TELEGRAM_CHAT_ID_ERRORES", "")

# =============================================================================
# CONFIGURACIÓN DEL SCRAPER (Módulo 1)
# =============================================================================
SCRAPER_CONFIG = {
    # Radio de búsqueda en metros (Google Places API)
    "search_radius_meters": 5000,

    # Máximo de resultados por búsqueda (Google Places devuelve max 60)
    "max_results_per_query": 60,

    # Tipos de negocio a buscar en Google Places
    "place_types": ["restaurant", "cafe", "bar", "bakery", "meal_takeaway"],

    # Dominios que indican presencia web PROPIA (descartar si tienen estos)
    "owned_website_extensions": [
        ".com", ".es", ".net", ".org", ".info", ".biz", ".eu",
        ".cat", ".gal", ".eus", ".restaurant", ".menu", ".food",
        ".bar", ".cafe", ".shop", ".store", ".online", ".site",
        ".web", ".app", ".io", ".co", ".me"
    ],

    # Dominios de redes sociales (NO se consideran web propia)
    "social_media_domains": [
        "facebook.com", "fb.com", "instagram.com", "twitter.com",
        "x.com", "tiktok.com", "youtube.com", "linkedin.com",
        "tripadvisor.com", "tripadvisor.es", "yelp.com",
        "google.com/maps", "maps.google.com", "goo.gl",
        "linktr.ee", "linktree.com"
    ],

    # Umbral mínimo de reseñas para considerar un negocio "activo"
    "min_reviews": 3,

    # Antigüedad máxima de la última reseña (en días) para considerar activo
    "max_review_age_days": 180,

    # Rating mínimo para considerar el lead viable
    "min_rating": 3.0,

    # Mínimo de fotos para indicar actividad
    "min_photos": 1,

    # Delay entre requests para no saturar la API (segundos)
    "request_delay_seconds": 1.5,

    # Idioma de búsqueda
    "language": "es",

    # País/región por defecto
    "region": "es",
}

# =============================================================================
# CONFIGURACIÓN DE LA BASE DE DATOS (Módulo 2)
# =============================================================================
DATABASE_CONFIG = {
    # Prefijos de teléfono para clasificación de leads
    "whatsapp_prefixes": ["6", "7"],       # Móviles -> WhatsApp
    "call_prefixes": ["8", "9"],           # Fijos -> Llamada

    # Prefijo internacional España
    "country_code": "+34",

    # Estados posibles de un lead
    "lead_statuses": [
        "nuevo",              # Recién extraído
        "web_generada",       # Se le generó landing page
        "contactado",         # Se le envió mensaje/llamada
        "interesado",         # Respondió con interés
        "cliente",            # Pagó suscripción
        "rechazado",          # No le interesa
        "inactivo",           # No responde después de X intentos
    ],
}

# =============================================================================
# CONFIGURACIÓN DEL WEB BUILDER (Módulo 3) - Para implementación futura
# =============================================================================
WEB_BUILDER_CONFIG = {
    "claude_model": "claude-sonnet-4-20250514",
    "max_tokens": 8000,
    "temperature": 0.7,
    "vercel_project_prefix": "resto-",
}

# =============================================================================
# CONFIGURACIÓN DE OUTREACH (Módulo 4) - Para implementación futura
# =============================================================================
OUTREACH_CONFIG = {
    "elevenlabs_voice_id": "21m00Tcm4TlvDq8ikWAM",  # Voice ID por defecto
    "elevenlabs_model": "eleven_multilingual_v2",
    "whatsapp_message_template": (
        "¡Hola {nombre}! 👋\n\n"
        "Hemos creado una página web GRATUITA para {nombre_restaurante}.\n"
        "Échale un vistazo: {url_web}\n\n"
        "Si te gusta, podemos activarla con tu dominio propio. "
        "¿Te interesa? 😊"
    ),
    "max_retries": 3,
    "retry_delay_seconds": 60,
}

# =============================================================================
# CONFIGURACIÓN DEL MONITOR (Módulo 5) - Para implementación futura
# =============================================================================
MONITOR_CONFIG = {
    "check_interval_hours": 24,
    "max_load_time_seconds": 10,
    "screenshot_on_error": True,
    "critical_status_codes": [404, 500, 502, 503],
}

# =============================================================================
# LOGGING
# =============================================================================
LOGGING_CONFIG = {
    "level": "INFO",
    "format": "%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    "date_format": "%Y-%m-%d %H:%M:%S",
    "log_file": LOGS_DIR / "system.log",
    "max_file_size_mb": 10,
    "backup_count": 5,
}
