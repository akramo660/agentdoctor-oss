"""
=============================================================================
 MÓDULO 2: CLASIFICADOR DE LEADS Y BASE DE DATOS (database.py)
=============================================================================

 Descripción:
 ------------
 Este módulo gestiona toda la persistencia de datos del sistema usando SQLite.
 Se encarga de:
   1. Crear y mantener el esquema de la base de datos.
   2. Clasificar leads por tipo de teléfono (WhatsApp vs Llamada).
   3. Guardar leads en las tablas correspondientes.
   4. Consultar, actualizar y gestionar el ciclo de vida de los leads.
   5. Registrar el historial de interacciones.
   6. Gestionar los clientes con suscripción activa.

 Esquema de la Base de Datos:
 ----------------------------
 - leads_whatsapp:  Leads con teléfono móvil (6xx, 7xx) -> contactar por WA
 - leads_llamada:   Leads con teléfono fijo (8xx, 9xx) -> contactar por llamada
 - webs_generadas:  Registro de webs desplegadas para cada lead
 - interacciones:   Historial de contactos realizados
 - clientes:        Leads que han pagado suscripción
 - monitoreo:       Registro de checks de las webs de clientes

 Clasificación:
 --------------
 Teléfono empieza por 6 o 7 -> leads_whatsapp (móvil = WhatsApp)
 Teléfono empieza por 8 o 9 -> leads_llamada (fijo = llamada comercial)

 Dependencias:
 -------------
 - sqlite3: Base de datos embebida (incluida en Python).
 - logging: Para registro de actividad.
 - json: Para serializar/deserializar datos complejos.

 Autor: Restaurant SaaS System
 Versión: 1.0.0
=============================================================================
"""

import sqlite3
import logging
import json
from datetime import datetime
from typing import Optional
from pathlib import Path
from contextlib import contextmanager

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import DB_PATH, DATABASE_CONFIG

# =============================================================================
# CONFIGURACIÓN DEL LOGGER
# =============================================================================
logger = logging.getLogger("database")
logger.setLevel(logging.INFO)

if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(handler)


# =============================================================================
# CLASE PRINCIPAL: LeadDatabase
# =============================================================================
class LeadDatabase:
    """
    Gestor de base de datos SQLite para leads de restaurantes.

    Maneja la clasificación, almacenamiento y consulta de leads
    separados por tipo de contacto (WhatsApp vs Llamada).

    Atributos:
        db_path (Path): Ruta al archivo de la base de datos SQLite.
        config (dict): Configuración desde settings.py.

    Ejemplo de uso:
        >>> db = LeadDatabase()
        >>> db.save_leads(leads_from_scraper)
        >>> whatsapp_leads = db.get_pending_whatsapp_leads()
        >>> call_leads = db.get_pending_call_leads()
    """

    def __init__(self, db_path: Optional[str] = None):
        """
        Inicializa la conexión a la base de datos.

        Args:
            db_path: Ruta al archivo SQLite. Si no se provee, usa la de settings.

        Note:
            Crea las tablas automáticamente si no existen.
        """
        self.db_path = Path(db_path) if db_path else DB_PATH
        self.config = DATABASE_CONFIG

        # Asegurar que el directorio existe
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Crear tablas si no existen
        self._initialize_database()

        logger.info(f"💾 Base de datos inicializada en: {self.db_path}")

    # =========================================================================
    # CONTEXT MANAGER: Conexión segura a la BD
    # =========================================================================
    @contextmanager
    def _get_connection(self):
        """
        Context manager para obtener una conexión segura a SQLite.

        Garantiza que la conexión se cierre correctamente y que los
        cambios se confirmen (commit) o reviertan (rollback) según
        corresponda.

        Yields:
            sqlite3.Connection: Conexión activa a la base de datos.

        Ejemplo:
            >>> with self._get_connection() as conn:
            ...     cursor = conn.execute("SELECT * FROM leads_whatsapp")
        """
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row  # Acceso por nombre de columna
        conn.execute("PRAGMA journal_mode=WAL")  # Write-Ahead Logging
        conn.execute("PRAGMA foreign_keys=ON")    # Integridad referencial
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"❌ Error en transacción de BD: {e}")
            raise
        finally:
            conn.close()

    # =========================================================================
    # INICIALIZACIÓN: Crear esquema de la base de datos
    # =========================================================================
    def _initialize_database(self):
        """
        Crea todas las tablas del sistema si no existen.

        Esquema:
        - leads_whatsapp: Restaurantes contactables por WhatsApp (6xx, 7xx)
        - leads_llamada: Restaurantes contactables por teléfono fijo (8xx, 9xx)
        - webs_generadas: Registro de landing pages creadas
        - interacciones: Historial de contactos con leads
        - clientes: Restaurantes que han pagado suscripción
        - monitoreo: Log de verificaciones de webs activas
        """
        with self._get_connection() as conn:
            # =================================================================
            # TABLA: leads_whatsapp (Teléfonos móviles: 6xx, 7xx)
            # =================================================================
            conn.execute("""
                CREATE TABLE IF NOT EXISTS leads_whatsapp (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,

                    -- Datos del restaurante
                    nombre TEXT NOT NULL,
                    telefono TEXT NOT NULL UNIQUE,
                    telefono_raw TEXT,
                    direccion TEXT,
                    ciudad TEXT,
                    latitud REAL,
                    longitud REAL,

                    -- Métricas
                    rating REAL DEFAULT 0,
                    total_resenas INTEGER DEFAULT 0,
                    nivel_precio INTEGER,
                    tipo_cocina TEXT,

                    -- Contenido (almacenado como JSON)
                    horarios TEXT,          -- JSON: lista de strings
                    fotos_urls TEXT,        -- JSON: lista de URLs
                    resenas_destacadas TEXT, -- JSON: lista de reseñas

                    -- URLs
                    google_maps_url TEXT,
                    website_actual TEXT,     -- URL actual (vacía o red social)
                    place_id TEXT UNIQUE,

                    -- Estado y seguimiento
                    estado TEXT DEFAULT 'nuevo',
                    web_generada_url TEXT,   -- URL de la web que le generamos
                    fecha_extraccion TEXT,
                    fecha_contacto TEXT,
                    fecha_respuesta TEXT,
                    intentos_contacto INTEGER DEFAULT 0,
                    notas TEXT,

                    -- Metadatos
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # =================================================================
            # TABLA: leads_llamada (Teléfonos fijos: 8xx, 9xx)
            # =================================================================
            conn.execute("""
                CREATE TABLE IF NOT EXISTS leads_llamada (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,

                    -- Datos del restaurante
                    nombre TEXT NOT NULL,
                    telefono TEXT NOT NULL UNIQUE,
                    telefono_raw TEXT,
                    direccion TEXT,
                    ciudad TEXT,
                    latitud REAL,
                    longitud REAL,

                    -- Métricas
                    rating REAL DEFAULT 0,
                    total_resenas INTEGER DEFAULT 0,
                    nivel_precio INTEGER,
                    tipo_cocina TEXT,

                    -- Contenido (almacenado como JSON)
                    horarios TEXT,
                    fotos_urls TEXT,
                    resenas_destacadas TEXT,

                    -- URLs
                    google_maps_url TEXT,
                    website_actual TEXT,
                    place_id TEXT UNIQUE,

                    -- Estado y seguimiento
                    estado TEXT DEFAULT 'nuevo',
                    web_generada_url TEXT,
                    audio_llamada_url TEXT,  -- URL del audio generado por ElevenLabs
                    fecha_extraccion TEXT,
                    fecha_contacto TEXT,
                    fecha_respuesta TEXT,
                    intentos_contacto INTEGER DEFAULT 0,
                    notas TEXT,

                    -- Metadatos
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # =================================================================
            # TABLA: webs_generadas (Landing pages creadas)
            # =================================================================
            conn.execute("""
                CREATE TABLE IF NOT EXISTS webs_generadas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    lead_id INTEGER NOT NULL,
                    lead_tabla TEXT NOT NULL,  -- 'whatsapp' o 'llamada'
                    nombre_restaurante TEXT,
                    url_desplegada TEXT,
                    vercel_project_id TEXT,
                    html_generado TEXT,        -- Código fuente HTML guardado
                    prompt_usado TEXT,          -- Prompt enviado a Claude
                    estado TEXT DEFAULT 'activa',  -- activa, pausada, eliminada
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # =================================================================
            # TABLA: interacciones (Historial de contactos)
            # =================================================================
            conn.execute("""
                CREATE TABLE IF NOT EXISTS interacciones (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    lead_id INTEGER NOT NULL,
                    lead_tabla TEXT NOT NULL,
                    tipo_contacto TEXT NOT NULL,  -- 'whatsapp', 'llamada', 'email'
                    mensaje_enviado TEXT,
                    respuesta_recibida TEXT,
                    estado TEXT,  -- 'enviado', 'entregado', 'leido', 'respondido'
                    audio_url TEXT,               -- URL del audio (para llamadas)
                    telegram_notificado INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # =================================================================
            # TABLA: clientes (Leads que pagaron suscripción)
            # =================================================================
            conn.execute("""
                CREATE TABLE IF NOT EXISTS clientes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    lead_id INTEGER NOT NULL,
                    lead_tabla TEXT NOT NULL,
                    nombre_restaurante TEXT,
                    telefono TEXT,
                    web_url TEXT,
                    dominio_propio TEXT,
                    plan TEXT DEFAULT 'basico',   -- basico, pro, premium
                    precio_mensual REAL,
                    fecha_inicio TEXT,
                    fecha_fin TEXT,
                    estado TEXT DEFAULT 'activo',  -- activo, pausado, cancelado
                    ultimo_pago TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # =================================================================
            # TABLA: monitoreo (Checks de webs de clientes)
            # =================================================================
            conn.execute("""
                CREATE TABLE IF NOT EXISTS monitoreo (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cliente_id INTEGER NOT NULL,
                    web_url TEXT NOT NULL,
                    status_code INTEGER,
                    tiempo_carga_ms INTEGER,
                    error_detectado INTEGER DEFAULT 0,
                    tipo_error TEXT,
                    screenshot_path TEXT,
                    telegram_notificado INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (cliente_id) REFERENCES clientes(id)
                )
            """)

            # =================================================================
            # ÍNDICES para optimizar consultas frecuentes
            # =================================================================
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_wa_estado
                ON leads_whatsapp(estado)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_wa_ciudad
                ON leads_whatsapp(ciudad)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_ll_estado
                ON leads_llamada(estado)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_ll_ciudad
                ON leads_llamada(ciudad)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_clientes_estado
                ON clientes(estado)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_monitoreo_cliente
                ON monitoreo(cliente_id)
            """)

            logger.info("✅ Esquema de base de datos creado/verificado")

    # =========================================================================
    # CLASIFICACIÓN: Determinar tipo de lead por teléfono
    # =========================================================================
    def classify_phone(self, phone: str) -> str:
        """
        Clasifica un número de teléfono como 'whatsapp' o 'llamada'.

        Reglas de clasificación:
        - 6xx, 7xx -> 'whatsapp' (teléfono móvil)
        - 8xx, 9xx -> 'llamada' (teléfono fijo)

        Args:
            phone: Número de teléfono limpio (9 dígitos).

        Returns:
            'whatsapp' o 'llamada'.

        Raises:
            ValueError: Si el teléfono no puede clasificarse.

        Ejemplos:
            >>> db.classify_phone("612345678")
            'whatsapp'
            >>> db.classify_phone("915678901")
            'llamada'
        """
        if not phone or len(phone) < 1:
            raise ValueError(f"Teléfono inválido: {phone}")

        first_digit = phone[0]

        if first_digit in self.config["whatsapp_prefixes"]:
            return "whatsapp"
        elif first_digit in self.config["call_prefixes"]:
            return "llamada"
        else:
            raise ValueError(
                f"No se puede clasificar el teléfono '{phone}'. "
                f"Primer dígito '{first_digit}' no reconocido. "
                f"Esperado: {self.config['whatsapp_prefixes']} (WhatsApp) "
                f"o {self.config['call_prefixes']} (Llamada)"
            )

    # =========================================================================
    # GUARDADO: Guardar leads clasificados en la BD
    # =========================================================================
    def save_leads(self, leads: list[dict]) -> dict:
        """
        Guarda una lista de leads clasificándolos automáticamente.

        Para cada lead:
        1. Clasifica por teléfono (WhatsApp o Llamada).
        2. Verifica si ya existe (por place_id o teléfono).
        3. Inserta en la tabla correspondiente.
        4. Serializa datos complejos (horarios, fotos, reseñas) como JSON.

        Args:
            leads: Lista de diccionarios con datos de leads del scraper.

        Returns:
            Diccionario con estadísticas del guardado:
            {
                "total": int,
                "whatsapp_nuevos": int,
                "llamada_nuevos": int,
                "duplicados": int,
                "errores": int,
                "no_clasificados": int,
            }

        Ejemplo:
            >>> from modules.scraper import RestaurantScraper
            >>> scraper = RestaurantScraper()
            >>> leads = scraper.prospect_city("Madrid")
            >>> stats = db.save_leads(leads)
            >>> print(f"Guardados: {stats['whatsapp_nuevos']} WA, "
            ...       f"{stats['llamada_nuevos']} llamada")
        """
        stats = {
            "total": len(leads),
            "whatsapp_nuevos": 0,
            "llamada_nuevos": 0,
            "duplicados": 0,
            "errores": 0,
            "no_clasificados": 0,
        }

        for lead in leads:
            try:
                phone = lead.get("telefono", "")

                # Clasificar el lead
                try:
                    lead_type = self.classify_phone(phone)
                except ValueError as e:
                    logger.warning(f"⚠️  {e}")
                    stats["no_clasificados"] += 1
                    continue

                # Verificar si ya existe
                if self._lead_exists(phone, lead_type):
                    logger.info(
                        f"  ℹ️  Lead duplicado: {lead['nombre']} ({phone})"
                    )
                    stats["duplicados"] += 1
                    continue

                # Guardar en la tabla correspondiente
                if lead_type == "whatsapp":
                    self._insert_whatsapp_lead(lead)
                    stats["whatsapp_nuevos"] += 1
                    logger.info(
                        f"  📱 WhatsApp: {lead['nombre']} ({phone})"
                    )
                else:
                    self._insert_call_lead(lead)
                    stats["llamada_nuevos"] += 1
                    logger.info(
                        f"  📞 Llamada: {lead['nombre']} ({phone})"
                    )

            except Exception as e:
                logger.error(
                    f"❌ Error guardando lead '{lead.get('nombre', '?')}': {e}"
                )
                stats["errores"] += 1

        # Imprimir resumen
        logger.info("=" * 60)
        logger.info("💾 RESUMEN DE GUARDADO EN BASE DE DATOS")
        logger.info("=" * 60)
        logger.info(f"  Total procesados:     {stats['total']}")
        logger.info(f"  📱 Nuevos WhatsApp:   {stats['whatsapp_nuevos']}")
        logger.info(f"  📞 Nuevos Llamada:    {stats['llamada_nuevos']}")
        logger.info(f"  🔄 Duplicados:        {stats['duplicados']}")
        logger.info(f"  ⚠️  No clasificados:  {stats['no_clasificados']}")
        logger.info(f"  ❌ Errores:            {stats['errores']}")
        logger.info("=" * 60)

        return stats

    # =========================================================================
    # INSERCIÓN: Guardar lead individual en leads_whatsapp
    # =========================================================================
    def _insert_whatsapp_lead(self, lead: dict):
        """
        Inserta un lead en la tabla leads_whatsapp.

        Args:
            lead: Diccionario con datos del lead.
        """
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO leads_whatsapp (
                    nombre, telefono, telefono_raw, direccion, ciudad,
                    latitud, longitud, rating, total_resenas, nivel_precio,
                    tipo_cocina, horarios, fotos_urls, resenas_destacadas,
                    google_maps_url, website_actual, place_id,
                    fecha_extraccion, estado
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                lead.get("nombre", ""),
                lead.get("telefono", ""),
                lead.get("telefono_raw", ""),
                lead.get("direccion", ""),
                lead.get("ciudad", ""),
                lead.get("coordenadas", {}).get("lat"),
                lead.get("coordenadas", {}).get("lng"),
                lead.get("rating", 0),
                lead.get("total_resenas", 0),
                lead.get("nivel_precio"),
                lead.get("tipo_cocina", ""),
                json.dumps(lead.get("horarios", []), ensure_ascii=False),
                json.dumps(lead.get("fotos_urls", []), ensure_ascii=False),
                json.dumps(lead.get("resenas_destacadas", []), ensure_ascii=False),
                lead.get("google_maps_url", ""),
                lead.get("website_actual", ""),
                lead.get("place_id", ""),
                lead.get("fecha_extraccion", datetime.now().isoformat()),
                "nuevo",
            ))

    # =========================================================================
    # INSERCIÓN: Guardar lead individual en leads_llamada
    # =========================================================================
    def _insert_call_lead(self, lead: dict):
        """
        Inserta un lead en la tabla leads_llamada.

        Args:
            lead: Diccionario con datos del lead.
        """
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO leads_llamada (
                    nombre, telefono, telefono_raw, direccion, ciudad,
                    latitud, longitud, rating, total_resenas, nivel_precio,
                    tipo_cocina, horarios, fotos_urls, resenas_destacadas,
                    google_maps_url, website_actual, place_id,
                    fecha_extraccion, estado
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                lead.get("nombre", ""),
                lead.get("telefono", ""),
                lead.get("telefono_raw", ""),
                lead.get("direccion", ""),
                lead.get("ciudad", ""),
                lead.get("coordenadas", {}).get("lat"),
                lead.get("coordenadas", {}).get("lng"),
                lead.get("rating", 0),
                lead.get("total_resenas", 0),
                lead.get("nivel_precio"),
                lead.get("tipo_cocina", ""),
                json.dumps(lead.get("horarios", []), ensure_ascii=False),
                json.dumps(lead.get("fotos_urls", []), ensure_ascii=False),
                json.dumps(lead.get("resenas_destacadas", []), ensure_ascii=False),
                lead.get("google_maps_url", ""),
                lead.get("website_actual", ""),
                lead.get("place_id", ""),
                lead.get("fecha_extraccion", datetime.now().isoformat()),
                "nuevo",
            ))

    # =========================================================================
    # VERIFICACIÓN: Comprobar si un lead ya existe
    # =========================================================================
    def _lead_exists(self, phone: str, lead_type: str) -> bool:
        """
        Verifica si un lead ya existe en la base de datos.

        Args:
            phone: Número de teléfono limpio.
            lead_type: Tipo de lead ('whatsapp' o 'llamada').

        Returns:
            True si el lead ya existe.
        """
        table = "leads_whatsapp" if lead_type == "whatsapp" else "leads_llamada"

        with self._get_connection() as conn:
            cursor = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE telefono = ?",
                (phone,)
            )
            count = cursor.fetchone()[0]
            return count > 0

    # =========================================================================
    # CONSULTAS: Obtener leads pendientes
    # =========================================================================
    def get_pending_whatsapp_leads(
        self, limit: int = 50, ciudad: Optional[str] = None
    ) -> list[dict]:
        """
        Obtiene leads de WhatsApp pendientes de contactar.

        Args:
            limit: Máximo de leads a devolver.
            ciudad: Filtrar por ciudad (opcional).

        Returns:
            Lista de diccionarios con datos de leads.
        """
        return self._get_leads_by_status(
            "leads_whatsapp", "nuevo", limit, ciudad
        )

    def get_pending_call_leads(
        self, limit: int = 50, ciudad: Optional[str] = None
    ) -> list[dict]:
        """
        Obtiene leads de llamada pendientes de contactar.

        Args:
            limit: Máximo de leads a devolver.
            ciudad: Filtrar por ciudad (opcional).

        Returns:
            Lista de diccionarios con datos de leads.
        """
        return self._get_leads_by_status(
            "leads_llamada", "nuevo", limit, ciudad
        )

    def _get_leads_by_status(
        self,
        table: str,
        status: str,
        limit: int = 50,
        ciudad: Optional[str] = None,
    ) -> list[dict]:
        """
        Consulta genérica de leads por estado.

        Args:
            table: Nombre de la tabla.
            status: Estado a filtrar.
            limit: Máximo de resultados.
            ciudad: Filtro opcional por ciudad.

        Returns:
            Lista de diccionarios con datos de leads.
        """
        with self._get_connection() as conn:
            query = f"SELECT * FROM {table} WHERE estado = ?"
            params = [status]

            if ciudad:
                query += " AND ciudad LIKE ?"
                params.append(f"%{ciudad}%")

            query += " ORDER BY rating DESC, total_resenas DESC LIMIT ?"
            params.append(limit)

            cursor = conn.execute(query, params)
            rows = cursor.fetchall()

            leads = []
            for row in rows:
                lead = dict(row)
                # Deserializar campos JSON
                for json_field in ["horarios", "fotos_urls", "resenas_destacadas"]:
                    if lead.get(json_field):
                        try:
                            lead[json_field] = json.loads(lead[json_field])
                        except (json.JSONDecodeError, TypeError):
                            lead[json_field] = []
                leads.append(lead)

            return leads

    # =========================================================================
    # ACTUALIZACIÓN: Cambiar estado de un lead
    # =========================================================================
    def update_lead_status(
        self, lead_id: int, table: str, new_status: str, **kwargs
    ):
        """
        Actualiza el estado de un lead y campos opcionales.

        Args:
            lead_id: ID del lead.
            table: 'whatsapp' o 'llamada'.
            new_status: Nuevo estado del lead.
            **kwargs: Campos adicionales a actualizar.
                     Ej: web_generada_url="https://...",
                         fecha_contacto="2024-01-15",
                         notas="Interesado, llamar mañana"

        Raises:
            ValueError: Si el estado no es válido.

        Ejemplo:
            >>> db.update_lead_status(
            ...     lead_id=1,
            ...     table="whatsapp",
            ...     new_status="contactado",
            ...     web_generada_url="https://resto-pepe.vercel.app",
            ...     fecha_contacto=datetime.now().isoformat()
            ... )
        """
        valid_statuses = self.config["lead_statuses"]
        if new_status not in valid_statuses:
            raise ValueError(
                f"Estado '{new_status}' no válido. "
                f"Opciones: {valid_statuses}"
            )

        table_name = f"leads_{table}"

        # Construir query dinámico con campos opcionales
        set_clauses = ["estado = ?", "updated_at = CURRENT_TIMESTAMP"]
        params = [new_status]

        for key, value in kwargs.items():
            set_clauses.append(f"{key} = ?")
            params.append(value)

        params.append(lead_id)

        with self._get_connection() as conn:
            conn.execute(
                f"UPDATE {table_name} SET {', '.join(set_clauses)} "
                f"WHERE id = ?",
                params,
            )

        logger.info(
            f"✅ Lead #{lead_id} ({table}) actualizado a: {new_status}"
        )

    # =========================================================================
    # REGISTRO: Guardar web generada
    # =========================================================================
    def save_generated_web(
        self,
        lead_id: int,
        lead_tabla: str,
        nombre_restaurante: str,
        url_desplegada: str,
        html_generado: str = "",
        prompt_usado: str = "",
        vercel_project_id: str = "",
    ) -> int:
        """
        Registra una web generada y desplegada para un lead.

        Args:
            lead_id: ID del lead.
            lead_tabla: 'whatsapp' o 'llamada'.
            nombre_restaurante: Nombre del restaurante.
            url_desplegada: URL de Vercel donde se desplegó.
            html_generado: Código fuente HTML (para backup).
            prompt_usado: Prompt enviado a Claude.
            vercel_project_id: ID del proyecto en Vercel.

        Returns:
            ID de la web generada.
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                INSERT INTO webs_generadas (
                    lead_id, lead_tabla, nombre_restaurante,
                    url_desplegada, vercel_project_id,
                    html_generado, prompt_usado
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                lead_id, lead_tabla, nombre_restaurante,
                url_desplegada, vercel_project_id,
                html_generado, prompt_usado,
            ))

            web_id = cursor.lastrowid

        # Actualizar el lead con la URL de la web
        self.update_lead_status(
            lead_id, lead_tabla, "web_generada",
            web_generada_url=url_desplegada,
        )

        logger.info(
            f"🌐 Web registrada: {nombre_restaurante} -> {url_desplegada}"
        )
        return web_id

    # =========================================================================
    # REGISTRO: Guardar interacción
    # =========================================================================
    def save_interaction(
        self,
        lead_id: int,
        lead_tabla: str,
        tipo_contacto: str,
        mensaje_enviado: str = "",
        audio_url: str = "",
    ) -> int:
        """
        Registra una interacción/contacto con un lead.

        Args:
            lead_id: ID del lead.
            lead_tabla: 'whatsapp' o 'llamada'.
            tipo_contacto: 'whatsapp', 'llamada', o 'email'.
            mensaje_enviado: Texto del mensaje enviado.
            audio_url: URL del audio generado (para llamadas).

        Returns:
            ID de la interacción.
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                INSERT INTO interacciones (
                    lead_id, lead_tabla, tipo_contacto,
                    mensaje_enviado, audio_url, estado
                ) VALUES (?, ?, ?, ?, ?, 'enviado')
            """, (
                lead_id, lead_tabla, tipo_contacto,
                mensaje_enviado, audio_url,
            ))

            # Incrementar contador de intentos en el lead
            table_name = f"leads_{lead_tabla}"
            conn.execute(f"""
                UPDATE {table_name}
                SET intentos_contacto = intentos_contacto + 1,
                    fecha_contacto = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (datetime.now().isoformat(), lead_id))

            return cursor.lastrowid

    # =========================================================================
    # CLIENTES: Registrar nuevo cliente
    # =========================================================================
    def register_client(
        self,
        lead_id: int,
        lead_tabla: str,
        web_url: str,
        plan: str = "basico",
        precio_mensual: float = 29.99,
        dominio_propio: str = "",
    ) -> int:
        """
        Registra un lead como cliente con suscripción.

        Args:
            lead_id: ID del lead.
            lead_tabla: 'whatsapp' o 'llamada'.
            web_url: URL de la web del cliente.
            plan: Tipo de plan (basico, pro, premium).
            precio_mensual: Precio de la suscripción.
            dominio_propio: Dominio propio del cliente (si tiene).

        Returns:
            ID del cliente.
        """
        # Obtener datos del lead
        table_name = f"leads_{lead_tabla}"
        with self._get_connection() as conn:
            cursor = conn.execute(
                f"SELECT nombre, telefono FROM {table_name} WHERE id = ?",
                (lead_id,)
            )
            lead = cursor.fetchone()

            if not lead:
                raise ValueError(f"Lead #{lead_id} no encontrado en {table_name}")

            cursor = conn.execute("""
                INSERT INTO clientes (
                    lead_id, lead_tabla, nombre_restaurante, telefono,
                    web_url, dominio_propio, plan, precio_mensual,
                    fecha_inicio, estado
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'activo')
            """, (
                lead_id, lead_tabla, lead["nombre"], lead["telefono"],
                web_url, dominio_propio, plan, precio_mensual,
                datetime.now().isoformat(),
            ))

            client_id = cursor.lastrowid

        # Actualizar estado del lead
        self.update_lead_status(lead_id, lead_tabla, "cliente")

        logger.info(
            f"🎉 Nuevo cliente registrado: {lead['nombre']} "
            f"(Plan: {plan}, {precio_mensual}€/mes)"
        )
        return client_id

    # =========================================================================
    # CLIENTES: Obtener clientes activos (para monitoreo)
    # =========================================================================
    def get_active_clients(self) -> list[dict]:
        """
        Obtiene todos los clientes con suscripción activa.

        Returns:
            Lista de diccionarios con datos de clientes activos.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM clientes WHERE estado = 'activo' "
                "ORDER BY nombre_restaurante"
            )
            return [dict(row) for row in cursor.fetchall()]

    # =========================================================================
    # ESTADÍSTICAS: Dashboard general
    # =========================================================================
    def get_stats(self) -> dict:
        """
        Obtiene estadísticas generales del sistema.

        Returns:
            Diccionario con estadísticas de todas las tablas.

        Ejemplo:
            >>> stats = db.get_stats()
            >>> print(f"Total leads WA: {stats['whatsapp']['total']}")
        """
        with self._get_connection() as conn:
            stats = {}

            # Estadísticas WhatsApp
            cursor = conn.execute(
                "SELECT estado, COUNT(*) as cnt "
                "FROM leads_whatsapp GROUP BY estado"
            )
            wa_stats = {row["estado"]: row["cnt"] for row in cursor.fetchall()}
            wa_total = sum(wa_stats.values())
            stats["whatsapp"] = {"total": wa_total, "por_estado": wa_stats}

            # Estadísticas Llamada
            cursor = conn.execute(
                "SELECT estado, COUNT(*) as cnt "
                "FROM leads_llamada GROUP BY estado"
            )
            ll_stats = {row["estado"]: row["cnt"] for row in cursor.fetchall()}
            ll_total = sum(ll_stats.values())
            stats["llamada"] = {"total": ll_total, "por_estado": ll_stats}

            # Estadísticas Webs
            cursor = conn.execute("SELECT COUNT(*) FROM webs_generadas")
            stats["webs_generadas"] = cursor.fetchone()[0]

            # Estadísticas Clientes
            cursor = conn.execute(
                "SELECT estado, COUNT(*) as cnt "
                "FROM clientes GROUP BY estado"
            )
            cl_stats = {row["estado"]: row["cnt"] for row in cursor.fetchall()}
            stats["clientes"] = cl_stats

            # Estadísticas Interacciones
            cursor = conn.execute("SELECT COUNT(*) FROM interacciones")
            stats["interacciones_total"] = cursor.fetchone()[0]

            return stats

    # =========================================================================
    # UTILIDAD: Obtener un lead por ID
    # =========================================================================
    def get_lead_by_id(self, lead_id: int, lead_type: str) -> Optional[dict]:
        """
        Obtiene un lead específico por su ID.

        Args:
            lead_id: ID del lead.
            lead_type: 'whatsapp' o 'llamada'.

        Returns:
            Diccionario con datos del lead, o None.
        """
        table = f"leads_{lead_type}"
        with self._get_connection() as conn:
            cursor = conn.execute(
                f"SELECT * FROM {table} WHERE id = ?", (lead_id,)
            )
            row = cursor.fetchone()
            if row:
                lead = dict(row)
                for json_field in ["horarios", "fotos_urls", "resenas_destacadas"]:
                    if lead.get(json_field):
                        try:
                            lead[json_field] = json.loads(lead[json_field])
                        except (json.JSONDecodeError, TypeError):
                            lead[json_field] = []
                return lead
            return None

    # =========================================================================
    # UTILIDAD: Obtener todos los leads de una ciudad
    # =========================================================================
    def get_leads_by_city(
        self, ciudad: str, include_all_statuses: bool = False
    ) -> dict:
        """
        Obtiene todos los leads de una ciudad específica.

        Args:
            ciudad: Nombre de la ciudad.
            include_all_statuses: Si True, incluye todos los estados.
                                 Si False, solo 'nuevo'.

        Returns:
            Diccionario con listas separadas de leads WhatsApp y Llamada.
        """
        result = {"whatsapp": [], "llamada": []}

        for lead_type in ["whatsapp", "llamada"]:
            table = f"leads_{lead_type}"
            with self._get_connection() as conn:
                if include_all_statuses:
                    query = f"SELECT * FROM {table} WHERE ciudad LIKE ?"
                    params = [f"%{ciudad}%"]
                else:
                    query = (
                        f"SELECT * FROM {table} "
                        f"WHERE ciudad LIKE ? AND estado = 'nuevo'"
                    )
                    params = [f"%{ciudad}%"]

                cursor = conn.execute(query, params)
                for row in cursor.fetchall():
                    lead = dict(row)
                    for json_field in [
                        "horarios", "fotos_urls", "resenas_destacadas"
                    ]:
                        if lead.get(json_field):
                            try:
                                lead[json_field] = json.loads(lead[json_field])
                            except (json.JSONDecodeError, TypeError):
                                lead[json_field] = []
                    result[lead_type].append(lead)

        return result

    # =========================================================================
    # UTILIDAD: Imprimir resumen bonito
    # =========================================================================
    def print_summary(self):
        """
        Imprime un resumen visual del estado actual de la base de datos.
        """
        stats = self.get_stats()

        print("\n" + "=" * 60)
        print("📊 RESUMEN DE LA BASE DE DATOS")
        print("=" * 60)

        print(f"\n📱 LEADS WHATSAPP (Total: {stats['whatsapp']['total']})")
        for estado, count in stats["whatsapp"]["por_estado"].items():
            print(f"   {estado}: {count}")

        print(f"\n📞 LEADS LLAMADA (Total: {stats['llamada']['total']})")
        for estado, count in stats["llamada"]["por_estado"].items():
            print(f"   {estado}: {count}")

        print(f"\n🌐 Webs generadas: {stats['webs_generadas']}")
        print(f"📬 Interacciones: {stats['interacciones_total']}")

        if stats["clientes"]:
            print(f"\n🎯 CLIENTES:")
            for estado, count in stats["clientes"].items():
                print(f"   {estado}: {count}")

        print("=" * 60)

    # =========================================================================
    # UTILIDAD: Exportar leads a JSON
    # =========================================================================
    def export_leads_json(
        self, output_path: str, lead_type: str = "all"
    ) -> str:
        """
        Exporta leads a un archivo JSON.

        Args:
            output_path: Ruta del archivo de salida.
            lead_type: 'whatsapp', 'llamada', o 'all'.

        Returns:
            Ruta del archivo generado.
        """
        export_data = {}

        if lead_type in ("whatsapp", "all"):
            with self._get_connection() as conn:
                cursor = conn.execute("SELECT * FROM leads_whatsapp")
                export_data["leads_whatsapp"] = [
                    dict(row) for row in cursor.fetchall()
                ]

        if lead_type in ("llamada", "all"):
            with self._get_connection() as conn:
                cursor = conn.execute("SELECT * FROM leads_llamada")
                export_data["leads_llamada"] = [
                    dict(row) for row in cursor.fetchall()
                ]

        export_data["exported_at"] = datetime.now().isoformat()
        export_data["total_records"] = sum(
            len(v) for k, v in export_data.items() if isinstance(v, list)
        )

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(export_data, f, ensure_ascii=False, indent=2)

        logger.info(f"📤 Exportados {export_data['total_records']} leads a {output_path}")
        return output_path

    # =========================================================================
    # MONITOREO: Guardar resultado de check
    # =========================================================================
    def save_monitoring_result(
        self,
        cliente_id: int,
        web_url: str,
        status_code: int,
        tiempo_carga_ms: int,
        error_detectado: bool = False,
        tipo_error: str = "",
        screenshot_path: str = "",
    ) -> int:
        """
        Guarda el resultado de una verificación de web de cliente.

        Args:
            cliente_id: ID del cliente.
            web_url: URL verificada.
            status_code: Código HTTP obtenido.
            tiempo_carga_ms: Tiempo de carga en milisegundos.
            error_detectado: Si se detectó algún error.
            tipo_error: Descripción del error (si aplica).
            screenshot_path: Ruta de la captura de pantalla (si hay error).

        Returns:
            ID del registro de monitoreo.
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                INSERT INTO monitoreo (
                    cliente_id, web_url, status_code, tiempo_carga_ms,
                    error_detectado, tipo_error, screenshot_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                cliente_id, web_url, status_code, tiempo_carga_ms,
                1 if error_detectado else 0, tipo_error, screenshot_path,
            ))
            return cursor.lastrowid


# =============================================================================
# EJECUCIÓN DIRECTA (para pruebas)
# =============================================================================
if __name__ == "__main__":
    """
    Ejecutar directamente para probar la base de datos:
        python -m modules.database
        # o
        python modules/database.py
    """
    print("=" * 60)
    print("💾 RESTAURANT SAAS - Módulo de Base de Datos")
    print("=" * 60)

    # Crear instancia de la base de datos
    db = LeadDatabase()

    # Importar el scraper para obtener datos de ejemplo
    from modules.scraper import RestaurantScraper

    scraper = RestaurantScraper()
    leads = scraper.prospect_city("Madrid, España")

    print(f"\n📥 Guardando {len(leads)} leads en la base de datos...\n")

    # Guardar leads (se clasifican automáticamente)
    stats = db.save_leads(leads)

    # Mostrar resumen
    db.print_summary()

    # Consultar leads pendientes
    wa_leads = db.get_pending_whatsapp_leads()
    call_leads = db.get_pending_call_leads()

    print(f"\n📱 Leads WhatsApp pendientes: {len(wa_leads)}")
    for lead in wa_leads:
        print(f"   - {lead['nombre']} ({lead['telefono']})")

    print(f"\n📞 Leads Llamada pendientes: {len(call_leads)}")
    for lead in call_leads:
        print(f"   - {lead['nombre']} ({lead['telefono']})")

    # Exportar a JSON
    export_path = str(db.db_path.parent / "leads_export.json")
    db.export_leads_json(export_path)
    print(f"\n📤 Datos exportados a: {export_path}")
