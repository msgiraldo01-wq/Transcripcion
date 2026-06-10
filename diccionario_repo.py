# diccionario_repo.py
# ─────────────────────────────────────────────
# VITACORE · Repositorio del Diccionario Médico
#
# Estructura de términos:
#   user_id = NULL  → término GLOBAL  (visible para todos)
#   user_id = UUID  → término PERSONAL (solo ese usuario)
#
# La API devuelve ambos separados para que el frontend
# pueda mostrar dos secciones diferenciadas.
# ─────────────────────────────────────────────

import time
from supabase_client import get_admin_client as get_supabase_admin


# ── Cache en memoria ──────────────────────────────────────────────
_CACHE: dict = {}
_CACHE_TTL   = 300  # 5 minutos


def _cache_key(especialidad: str, user_id: str | None) -> str:
    return f"{especialidad}:{user_id or 'global'}"


def _cache_get(key: str):
    entry = _CACHE.get(key)
    if entry and (time.time() - entry["ts"]) < _CACHE_TTL:
        return entry["data"]
    return None


def _cache_set(key: str, data):
    _CACHE[key] = {"data": data, "ts": time.time()}


def _cache_invalidar(especialidad: str, user_id: str | None = None):
    """Invalida el cache de un usuario y el global."""
    for k in list(_CACHE.keys()):
        if k.startswith(especialidad):
            del _CACHE[k]


# ════════════════════════════════════════════
# OBTENER TÉRMINOS (para pipeline STT)
# ════════════════════════════════════════════

def obtener_terminos(especialidad: str = "radiologia", user_id: str | None = None) -> list:
    """
    Retorna lista plana de strings — usada por el corrector difuso.
    Incluye términos globales + personales del usuario.
    """
    clave = _cache_key(especialidad, user_id)
    cached = _cache_get(clave + ":terminos")
    if cached is not None:
        return cached

    sb = get_supabase_admin()
    terminos = []

    # Globales
    resp_g = (sb.schema("transcripcion")
                .table("diccionario")
                .select("termino")
                .eq("especialidad", especialidad)
                .is_("user_id", "null")
                .eq("activo", True)
                .execute())
    terminos += [r["termino"] for r in (resp_g.data or [])]

    # Personales del usuario
    if user_id:
        resp_p = (sb.schema("transcripcion")
                    .table("diccionario")
                    .select("termino")
                    .eq("especialidad", especialidad)
                    .eq("user_id", user_id)
                    .eq("activo", True)
                    .execute())
        terminos += [r["termino"] for r in (resp_p.data or [])]

    resultado = list(set(terminos))
    _cache_set(clave + ":terminos", resultado)
    return resultado


# ════════════════════════════════════════════
# OBTENER CORRECCIONES (para pipeline STT)
# ════════════════════════════════════════════

def obtener_correcciones(especialidad: str = "radiologia", user_id: str | None = None) -> dict:
    """
    Retorna dict {incorrecto: correcto} — usado por el corrector exacto.
    Las correcciones personales sobreescriben las globales si hay conflicto.
    """
    clave = _cache_key(especialidad, user_id)
    cached = _cache_get(clave + ":correcciones")
    if cached is not None:
        return cached

    sb = get_supabase_admin()
    correcciones = {}

    # Globales primero
    resp_g = (sb.schema("transcripcion")
                .table("correcciones")
                .select("incorrecto,correcto")
                .eq("especialidad", especialidad)
                .eq("activo", True)
                .is_("user_id", "null")
                .execute())
    for r in (resp_g.data or []):
        if r["incorrecto"]:
            correcciones[r["incorrecto"].lower()] = r["correcto"]

    # Personales (pueden sobreescribir globales)
    if user_id:
        resp_p = (sb.schema("transcripcion")
                    .table("correcciones")
                    .select("incorrecto,correcto")
                    .eq("especialidad", especialidad)
                    .eq("activo", True)
                    .eq("user_id", user_id)
                    .execute())
        for r in (resp_p.data or []):
            if r["incorrecto"]:
                correcciones[r["incorrecto"].lower()] = r["correcto"]

    _cache_set(clave + ":correcciones", correcciones)
    return correcciones


# ════════════════════════════════════════════
# OBTENER DICCIONARIO COMPLETO (para el frontend)
# Retorna estructura separada: global vs personal
# ════════════════════════════════════════════

def obtener_diccionario_completo(user_id: str | None = None) -> dict:
    """
    Retorna el diccionario estructurado para el panel frontend.
    Separado en secciones: global y personal.
    """
    sb = get_supabase_admin()

    # ── Términos globales ────────────────────────────────────────
    resp_global = (sb.schema("transcripcion")
                     .table("diccionario")
                     .select("termino,categoria,especialidad")
                     .eq("activo", True)
                     .is_("user_id", "null")
                     .order("categoria")
                     .execute())

    # ── Términos personales ──────────────────────────────────────
    terminos_personales = []
    if user_id:
        resp_personal = (sb.schema("transcripcion")
                           .table("diccionario")
                           .select("termino,categoria,especialidad")
                           .eq("activo", True)
                           .eq("user_id", user_id)
                           .order("categoria")
                           .execute())
        terminos_personales = resp_personal.data or []

    # ── Correcciones globales ────────────────────────────────────
    resp_corr_global = (sb.schema("transcripcion")
                          .table("correcciones")
                          .select("incorrecto,correcto,usos")
                          .eq("activo", True)
                          .is_("user_id", "null")
                          .order("usos", desc=True)
                          .execute())

    # ── Correcciones personales ──────────────────────────────────
    correcciones_personales = []
    if user_id:
        resp_corr_personal = (sb.schema("transcripcion")
                                .table("correcciones")
                                .select("incorrecto,correcto,usos")
                                .eq("activo", True)
                                .eq("user_id", user_id)
                                .order("usos", desc=True)
                                .execute())
        correcciones_personales = resp_corr_personal.data or []

    # ── Agrupar por categoría ────────────────────────────────────
    def agrupar_por_cat(registros):
        cats = {}
        for r in registros:
            cat = r.get("categoria", "general")
            if cat not in cats:
                cats[cat] = []
            cats[cat].append(r["termino"])
        return cats

    terminos_globales   = resp_global.data or []
    cats_global         = agrupar_por_cat(terminos_globales)
    cats_personal       = agrupar_por_cat(terminos_personales)

    return {
        "ok": True,
        # Estructura legacy para compatibilidad con el AC
        "especialidades": {
            "radiologia": {
                "terminos": [r["termino"] for r in terminos_globales + terminos_personales]
            }
        },
        # Nueva estructura separada para el panel UI
        "global": {
            "terminos_por_categoria": cats_global,
            "total": len(terminos_globales),
            "correcciones": [
                {"incorrecto": r["incorrecto"], "correcto": r["correcto"], "usos": r.get("usos", 0)}
                for r in (resp_corr_global.data or [])
            ],
        },
        "personal": {
            "terminos_por_categoria": cats_personal,
            "total": len(terminos_personales),
            "correcciones": [
                {"incorrecto": r["incorrecto"], "correcto": r["correcto"], "usos": r.get("usos", 0)}
                for r in correcciones_personales
            ],
            "user_id": user_id or "",
        },
    }


# ════════════════════════════════════════════
# AGREGAR / ELIMINAR TÉRMINO
# ════════════════════════════════════════════

def agregar_termino(
    termino:     str,
    categoria:   str = "propios",
    especialidad: str = "radiologia",
    user_id:     str | None = None,
    es_global:   bool = False,
) -> bool:
    """
    Agrega un término al diccionario.
    - es_global=True → término global (sin user_id), solo admins
    - es_global=False → término personal del user_id
    """
    if not termino.strip():
        return False

    sb = get_supabase_admin()

    uid_insertar = None if es_global else user_id

    # Verificar si ya existe
    query = (sb.schema("transcripcion")
               .table("diccionario")
               .select("id")
               .eq("termino", termino.strip())
               .eq("categoria", categoria)
               .eq("especialidad", especialidad))

    if uid_insertar:
        query = query.eq("user_id", uid_insertar)
    else:
        query = query.is_("user_id", "null")

    existente = query.execute()
    if existente.data:
        return False  # ya existe

    sb.schema("transcripcion").table("diccionario").insert({
        "termino":     termino.strip(),
        "categoria":   categoria,
        "especialidad": especialidad,
        "user_id":     uid_insertar,
        "activo":      True,
    }).execute()

    _cache_invalidar(especialidad, user_id)
    return True


def eliminar_termino(
    termino:     str,
    especialidad: str = "radiologia",
    user_id:     str | None = None,
) -> bool:
    """
    Elimina (desactiva) un término.
    Solo puede eliminar sus propios términos personales.
    Los globales no se eliminan desde aquí.
    """
    if not termino.strip() or not user_id:
        return False

    sb = get_supabase_admin()

    resp = (sb.schema("transcripcion")
              .table("diccionario")
              .update({"activo": False})
              .eq("termino", termino.strip())
              .eq("especialidad", especialidad)
              .eq("user_id", user_id)
              .execute())

    _cache_invalidar(especialidad, user_id)
    return bool(resp.data)


# ════════════════════════════════════════════
# AGREGAR CORRECCIÓN
# ════════════════════════════════════════════

def agregar_correccion(
    incorrecto:  str,
    correcto:    str,
    especialidad: str = "radiologia",
    user_id:     str | None = None,
    es_global:   bool = False,
) -> bool:
    """
    Agrega una corrección automática.
    - es_global=True → corrección global (sin user_id)
    - es_global=False → corrección personal del user_id
    """
    if not incorrecto.strip() or not correcto.strip():
        return False

    sb = get_supabase_admin()
    uid_insertar = None if es_global else user_id

    try:
        sb.schema("transcripcion").table("correcciones").upsert({
            "incorrecto":   incorrecto.strip().lower(),
            "correcto":     correcto.strip(),
            "especialidad": especialidad,
            "user_id":      uid_insertar,
            "activo":       True,
            "usos":         0,
        }, on_conflict="incorrecto,especialidad,user_id").execute()

        _cache_invalidar(especialidad, user_id)
        return True
    except Exception as e:
        print(f"[diccionario_repo] Error agregar_correccion: {e}")
        return False


def incrementar_uso_correccion(incorrecto: str, especialidad: str = "radiologia") -> None:
    """
    Incrementa el contador de usos de una corrección.
    Se llama desde el pipeline — operación no crítica.
    """
    # No implementado en el pipeline por rendimiento
    # Se puede implementar como tarea asíncrona si se necesita
    pass


# ════════════════════════════════════════════
# OBTENER FRASES (para autocompletado futuro)
# ════════════════════════════════════════════

def obtener_frases(especialidad: str = "radiologia", user_id: str | None = None) -> list:
    sb = get_supabase_admin()
    resp = (sb.schema("transcripcion")
              .table("frases")
              .select("frase")
              .eq("especialidad", especialidad)
              .eq("activo", True)
              .execute())
    return [r["frase"] for r in (resp.data or [])]