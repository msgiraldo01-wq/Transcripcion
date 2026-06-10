# supabase_client.py
# ─────────────────────────────────────────────
# VITACORE · Cliente Supabase singleton
#
# Uso:
#   from supabase_client import get_client, get_admin_client
#
#   sb = get_client()          # cliente normal (respeta RLS)
#   sb = get_admin_client()    # service_role (para jobs de Celery)
# ─────────────────────────────────────────────

import os
from functools import lru_cache
from supabase import create_client, Client

# ── Variables de entorno requeridas ──────────────────────────────
# Agregar al .env de VITACORE:
#
#   SUPABASE_URL=https://xxxxxxxxxxxx.supabase.co
#   SUPABASE_ANON_KEY=eyJ...  (clave pública anon)
#   SUPABASE_SERVICE_KEY=eyJ... (clave service_role — NUNCA al frontend)
#   REDIS_URL=redis://localhost:6379/0
# ─────────────────────────────────────────────────────────────────

def _require_env(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(
            f"[VITACORE] Variable de entorno '{name}' no definida. "
            f"Agrégala al archivo .env del proyecto."
        )
    return val


@lru_cache(maxsize=1)
def get_client() -> Client:
    """
    Cliente con clave anon.
    Respeta Row Level Security — úsalo en rutas Flask normales.
    """
    url = _require_env("SUPABASE_URL")
    key = _require_env("SUPABASE_ANON_KEY")
    return create_client(url, key)


@lru_cache(maxsize=1)
def get_admin_client() -> Client:
    """
    Cliente con service_role key.
    Salta RLS — úsalo SOLO en workers Celery y scripts de migración.
    Nunca expongas este cliente en una ruta HTTP pública.
    """
    url = _require_env("SUPABASE_URL")
    key = _require_env("SUPABASE_SERVICE_KEY")
    return create_client(url, key)


def get_storage(admin: bool = False):
    """
    Acceso directo al bucket de audios.
    admin=True para operaciones desde Celery (subir, eliminar).
    admin=False para generar URLs firmadas de descarga.
    """
    client = get_admin_client() if admin else get_client()
    return client.storage.from_("vitacore-audios")


def generar_url_firmada(storage_path: str, expira_seg: int = 3600) -> str:
    """
    Genera una URL firmada para reproducción de audio en el navegador.
    El bucket es privado, así que el frontend no puede acceder directo.

    storage_path: ej. "a3f9c.../501885/d7e2a....mp3"
    expira_seg: duración de la URL (por defecto 1 hora)
    """
    storage = get_storage(admin=False)
    resp = storage.create_signed_url(storage_path, expira_seg)
    return resp.get("signedURL", "")