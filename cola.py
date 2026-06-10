# cola.py
# ─────────────────────────────────────────────
# VITACORE · Cola Celery — Fix Fase 4
#
# Con async_mode="threading" en Flask-SocketIO,
# Celery no puede emitir WebSocket directamente.
# 
# Estrategia: Celery actualiza Supabase (ya lo hacía).
# Flask tiene un endpoint /estado que el frontend consulta.
# El WebSocket se usa solo para la conexión inicial y
# para notificar cuando el job ya estaba done al conectar.
# El polling ligero (cada 2s) detecta el cambio en Supabase.
# ─────────────────────────────────────────────

from dotenv import load_dotenv
load_dotenv()

import os
import time
import tempfile

from celery import Celery
from celery.utils.log import get_task_logger

from transcripcion_repo import (
    obtener_job,
    marcar_processing,
    marcar_done,
    marcar_error,
    descargar_audio_temporal,
    log_accion,
)
from groq_pipeline import transcribir_con_groq

logger = get_task_logger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "vitacore_transcripcion",
    broker=REDIS_URL,
    backend=REDIS_URL,
)

celery_app.conf.update(
    task_serializer            = "json",
    result_serializer          = "json",
    accept_content             = ["json"],
    task_acks_late             = True,
    task_reject_on_worker_lost = True,
    result_expires             = 86400,
    task_default_queue         = "transcripcion",
    timezone                   = "America/Bogota",
    enable_utc                 = True,
    task_annotations           = {
        "cola.procesar_transcripcion": {"rate_limit": "18/m"}
    },
)


@celery_app.task(
    bind=True,
    name="cola.procesar_transcripcion",
    max_retries=3,
    soft_time_limit=300,
    time_limit=360,
)
def procesar_transcripcion(
    self,
    job_id:       str,
    especialidad: str = "radiologia",
    bloque:       str = "general",
    user_id:      str = None,
) -> dict:
    """
    Pipeline completo. Al terminar actualiza Supabase.
    El frontend detecta el cambio por polling ligero a /estado.
    """
    t_inicio = time.time()
    tmp_path = None

    try:
        logger.info(f"[Job {job_id[:8]}] Iniciando")

        job = obtener_job(job_id, admin=True)
        if not job:
            raise ValueError(f"Job {job_id} no encontrado")

        if job["estado"] not in ("pending", "error"):
            logger.warning(f"[Job {job_id[:8]}] Estado inválido: {job['estado']}")
            return {"ok": False, "error": "Estado inválido"}

        marcar_processing(job_id)
        log_accion(job_id, "start_transcription", user_id=user_id)

        storage_path = job.get("audio_storage_path")
        if not storage_path:
            raise ValueError("Job sin audio en storage")

        # Descargar audio
        ext = storage_path.rsplit(".", 1)[-1] if "." in storage_path else "mp3"
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=f".{ext}", prefix="vitacore_")
        os.close(tmp_fd)

        logger.info(f"[Job {job_id[:8]}] Descargando audio...")
        descargar_audio_temporal(storage_path, tmp_path)

        # Pipeline Groq
        logger.info(f"[Job {job_id[:8]}] Pipeline Groq...")
        resultado = transcribir_con_groq(
            ruta_audio   = tmp_path,
            especialidad = especialidad,
            bloque       = bloque,
            user_id      = user_id,
        )

        if resultado["error"]:
            raise RuntimeError(resultado["error"])

        # Persistir en Supabase — el polling del frontend lo detecta
        duracion = round(time.time() - t_inicio, 2)
        marcar_done(
            job_id           = job_id,
            texto_raw        = resultado["texto_raw"],
            texto_corregido  = resultado["texto_corregido"],
            html_clinico     = resultado["html_clinico"],
            estructura       = resultado["estructura"],
            duracion_seg     = duracion,
            idioma_detectado = resultado.get("idioma", "es"),
            audio_duracion   = resultado.get("audio_duracion", 0.0),
        )

        log_accion(job_id, "finish_transcription", user_id=user_id,
                   detalle={"duracion_seg": duracion,
                            "chars": len(resultado["texto_corregido"])})

        logger.info(f"[Job {job_id[:8]}] Completado en {duracion}s")

        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
            tmp_path = None

        return {"ok": True, "job_id": job_id, "duracion": duracion}

    except Exception as exc:
        duracion = round(time.time() - t_inicio, 2)
        logger.error(f"[Job {job_id[:8]}] Error: {exc}")

        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass

        try:
            marcar_error(job_id, str(exc))
            log_accion(job_id, "error_transcription", user_id=user_id,
                       detalle={"error": str(exc)})
        except Exception:
            pass

        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=15 * (self.request.retries + 1))

        return {"ok": False, "job_id": job_id, "error": str(exc)}


def encolar_transcripcion(
    job_id:       str,
    especialidad: str = "radiologia",
    bloque:       str = "general",
    user_id:      str = None,
) -> str:
    task = procesar_transcripcion.apply_async(
        kwargs={
            "job_id":       job_id,
            "especialidad": especialidad,
            "bloque":       bloque,
            "user_id":      user_id,
        },
        task_id=f"tx-{job_id}",
    )
    return task.id