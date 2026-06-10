# transcripcion_repo.py
# ─────────────────────────────────────────────
# VITACORE · Repositorio de jobs de transcripción
#
# Todas las operaciones sobre transcripcion.jobs
# y transcripcion.logs pasan por aquí.
# Flask usa get_client() (respeta RLS).
# Celery usa get_admin_client() (salta RLS).
# ─────────────────────────────────────────────

from __future__ import annotations

import os
import time
import uuid
from datetime import date
from typing import Optional

from supabase_client import get_client, get_admin_client, get_storage, generar_url_firmada

SCHEMA = "transcripcion"


# ════════════════════════════════════════════
# JOBS
# ════════════════════════════════════════════

def crear_job(
    nro_interno:          str,
    proc_id:              str,
    proc_nombre:          str,
    user_id:              str,
    nro_factura:          str  = "",
    sede:                 str  = "",
    paciente_nombre:      str  = "",
    paciente_doc:         str  = "",
    paciente_telefono:    str  = "",   # ← NUEVO
    codigo_cups:          str  = "",   # ← NUEVO
    valor_procedimiento:  float = 0.0, # ← NUEVO
    fecha_remision:       Optional[date] = None,
    user_nombre:          str  = "",
) -> dict:
    sb = get_client()
    payload = {
        "nro_interno":         nro_interno,
        "proc_id":             str(proc_id),
        "proc_nombre":         proc_nombre,
        "user_id":             str(user_id),
        "nro_factura":         nro_factura,
        "sede":                sede,
        "paciente_nombre":     paciente_nombre,
        "paciente_doc":        paciente_doc,
        "paciente_telefono":   paciente_telefono,   # ← NUEVO
        "codigo_cups":         codigo_cups,          # ← NUEVO
        "valor_procedimiento": valor_procedimiento,  # ← NUEVO
        "user_nombre":         user_nombre,
        "estado":              "pending",
    }
    if fecha_remision:
        payload["fecha_remision"] = fecha_remision.isoformat()

    resp = (
        sb.schema(SCHEMA)
        .table("jobs")
        .insert(payload)
        .execute()
    )
    return resp.data[0]

def obtener_job(job_id: str, admin: bool = False) -> Optional[dict]:
    """
    Retorna el job por su UUID.
    admin=True para Celery (salta RLS).
    """
    sb = get_admin_client() if admin else get_client()
    resp = (
        sb.schema(SCHEMA)
        .table("jobs")
        .select("*")
        .eq("id", job_id)
        .single()
        .execute()
    )
    return resp.data


def listar_jobs_paciente(nro_interno: str, limite: int = 20) -> list:
    """
    Todos los jobs de un paciente, ordenados del más reciente.
    Útil para el historial en la pantalla de remisiones.
    """
    sb = get_client()
    resp = (
        sb.schema(SCHEMA)
        .table("jobs")
        .select("id,proc_nombre,estado,creado_en,audio_nombre_original,guardado_en")
        .eq("nro_interno", nro_interno)
        .order("creado_en", desc=True)
        .limit(limite)
        .execute()
    )
    return resp.data or []


def actualizar_estado(
    job_id: str,
    estado: str,
    extra:  dict = None,
) -> dict:
    """
    Actualiza el estado del job y cualquier campo adicional.
    Siempre se llama desde Celery (admin=True).
    """
    sb = get_admin_client()
    payload = {"estado": estado}
    if extra:
        payload.update(extra)
    resp = (
        sb.schema(SCHEMA)
        .table("jobs")
        .update(payload)
        .eq("id", job_id)
        .execute()
    )
    return resp.data[0] if resp.data else {}


def marcar_processing(job_id: str) -> None:
    actualizar_estado(job_id, "processing")


def marcar_done(
    job_id:           str,
    texto_raw:        str,
    texto_corregido:  str,
    html_clinico:     str,
    estructura:       dict,
    duracion_seg:     float,
    idioma_detectado: str = "es",
    audio_duracion:   float = 0.0,
) -> None:
    from datetime import timezone, datetime
    actualizar_estado(
        job_id, "done",
        extra={
            "texto_raw":           texto_raw,
            "texto_corregido":     texto_corregido,
            "html_clinico":        html_clinico,
            "estructura":          estructura,
            "duracion_proceso_seg": round(duracion_seg, 2),
            "idioma_detectado":    idioma_detectado,
            "audio_duracion_seg":  round(audio_duracion, 2),
            "procesado_en":        datetime.now(timezone.utc).isoformat(),
        }
    )


def marcar_error(job_id: str, mensaje: str) -> None:
    actualizar_estado(
        job_id, "error",
        extra={"error_mensaje": mensaje[:2000]}
    )


def guardar_informe(
    job_id:       str,
    informe_html: str,
    informe_final: str,
    guardado_por: str,
    folio_hc:     str = "",
) -> dict:
    """
    El médico/transcriptor confirma el informe editado.
    Actualiza campos finales y registra timestamp.
    """
    from datetime import timezone, datetime
    sb = get_client()
    payload = {
        "informe_html":  informe_html,
        "informe_final": informe_final,
        "guardado_por":  str(guardado_por),
        "guardado_en":   datetime.now(timezone.utc).isoformat(),
    }
    if folio_hc:
        payload["folio_hc"] = folio_hc

    resp = (
        sb.schema(SCHEMA)
        .table("jobs")
        .update(payload)
        .eq("id", job_id)
        .execute()
    )
    return resp.data[0] if resp.data else {}


# ════════════════════════════════════════════
# AUDIO — Storage
# ════════════════════════════════════════════

def subir_audio_storage(
    job_id:      str,
    user_id:     str,
    nro_interno: str,
    ruta_local:  str,
    nombre_orig: str,
) -> str:
    """
    Sube el audio al bucket privado de Supabase Storage.
    Retorna el storage_path guardado en BD.

    Path convention: {user_id}/{nro_interno}/{job_id}.{ext}
    """
    ext = nombre_orig.rsplit(".", 1)[-1].lower() if "." in nombre_orig else "mp3"
    storage_path = f"{user_id}/{nro_interno}/{job_id}.{ext}"

    with open(ruta_local, "rb") as f:
        audio_bytes = f.read()

    mime_map = {
        "mp3": "audio/mpeg",
        "wav": "audio/wav",
        "ogg": "audio/ogg",
        "m4a": "audio/mp4",
        "webm": "audio/webm",
    }
    content_type = mime_map.get(ext, "audio/mpeg")

    storage = get_storage(admin=True)
    storage.upload(
        path=storage_path,
        file=audio_bytes,
        file_options={"content-type": content_type, "upsert": "true"},
    )

    # Guardar path en el job
    actualizar_estado(
        job_id, "pending",
        extra={
            "audio_storage_path":    storage_path,
            "audio_nombre_original": nombre_orig,
        }
    )
    return storage_path


def descargar_audio_temporal(storage_path: str, destino: str) -> str:
    """
    Descarga el audio del bucket a un archivo temporal en el VPS.
    Usado por el worker Celery para transcribir.
    Retorna la ruta local del archivo descargado.
    """
    storage = get_storage(admin=True)
    audio_bytes = storage.download(storage_path)
    with open(destino, "wb") as f:
        f.write(audio_bytes)
    return destino


def eliminar_audio_storage(storage_path: str) -> None:
    """
    Elimina el audio del bucket después de transcribir.
    Llámalo solo desde el worker, después de marcar_done.
    """
    try:
        storage = get_storage(admin=True)
        storage.remove([storage_path])
        print(f"[Storage] Audio eliminado: {storage_path}")
    except Exception as e:
        print(f"[Storage] Error eliminando {storage_path}: {e}")


def obtener_url_audio(job: dict, expira_seg: int = 3600) -> str:
    """
    Genera URL firmada para reproducción en el browser.
    Si el job ya tiene el audio en storage, retorna URL firmada.
    """
    path = job.get("audio_storage_path")
    if not path:
        return ""
    return generar_url_firmada(path, expira_seg)


# ════════════════════════════════════════════
# LOGS DE TRAZABILIDAD
# ════════════════════════════════════════════

def log_accion(
    job_id:  str,
    accion:  str,
    user_id: str = None,
    detalle: dict = None,
    ip:      str = None,
) -> None:
    """
    Registra una acción en el log de trazabilidad.
    No lanza excepción si falla — el log nunca debe interrumpir el flujo.
    """
    try:
        sb = get_admin_client()
        payload = {
            "job_id":  job_id,
            "accion":  accion,
        }
        if user_id:
            payload["user_id"] = str(user_id)
        if detalle:
            payload["detalle"] = detalle
        if ip:
            payload["ip"] = ip

        sb.schema(SCHEMA).table("logs").insert(payload).execute()
    except Exception as e:
        print(f"[Log] Error registrando acción '{accion}': {e}")