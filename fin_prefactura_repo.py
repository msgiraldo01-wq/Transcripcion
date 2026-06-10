# fin_prefactura_repo.py
# ─────────────────────────────────────────────
# VITACORE · Módulo Facturación — Prefacturas
# Fase 3: Integración Historia Clínica
# ─────────────────────────────────────────────

from __future__ import annotations
import uuid
from datetime import datetime, timezone
from supabase_client import get_admin_client as get_supabase_admin


# ════════════════════════════════════════════
# HELPERS PACIENTE
# hc_pacientes tiene: primer_nombre, segundo_nombre,
# primer_apellido, segundo_apellido, nombres, apellidos,
# numero_documento, tipo_documento_id
# ════════════════════════════════════════════

def _nombre_completo(p: dict) -> str:
    if not p:
        return "Paciente sin nombre"
    # Intentar campo pre-armado
    if p.get("nombres") and p.get("apellidos"):
        return f"{p['nombres']} {p['apellidos']}".strip()
    # Armar desde campos individuales
    partes = [
        p.get("primer_nombre", ""),
        p.get("segundo_nombre", ""),
        p.get("primer_apellido", ""),
        p.get("segundo_apellido", ""),
    ]
    nombre = " ".join(x for x in partes if x).strip()
    return nombre or "Paciente sin nombre"


def _obtener_paciente(paciente_id: int) -> dict:
    sb = get_supabase_admin()
    try:
        resp = (sb.table("hc_pacientes")
                  .select("""
                    id, numero_documento, tipo_documento_id,
                    primer_nombre, segundo_nombre,
                    primer_apellido, segundo_apellido,
                    nombres, apellidos,
                    celular, telefono, email,
                    fecha_nacimiento, sexo
                  """)
                  .eq("id", paciente_id)
                  .single()
                  .execute())
        if resp.data:
            resp.data["nombre_completo"] = _nombre_completo(resp.data)
        return resp.data or {}
    except Exception:
        return {}


# ════════════════════════════════════════════
# PREFACTURAS — lectura
# ════════════════════════════════════════════

def listar_prefacturas(
    sede_id: int | None = None,
    limit:   int = 50,
    offset:  int = 0,
) -> list[dict]:
    sb = get_supabase_admin()

    query = (sb.table("fin_prefacturas")
               .select("""
                 id, paciente_id, empresa_id, cliente_id,
                 sede_id, periodo_inicio, periodo_fin,
                 estado, tiene_informe_radio,
                 estado_informe_radio, created_at
               """)
               .order("created_at", desc=True)
               .limit(limit)
               .offset(offset))

    if sede_id:
        query = query.eq("sede_id", sede_id)

    prefacturas = query.execute().data or []

    for pf in prefacturas:
        pid = pf.get("paciente_id")
        pf["paciente"] = _obtener_paciente(pid) if pid else {}

    return prefacturas


def obtener_prefactura(prefactura_id: int) -> dict | None:
    sb = get_supabase_admin()

    resp = (sb.table("fin_prefacturas")
              .select("""
                id, paciente_id, empresa_id, cliente_id,
                sede_id, periodo_inicio, periodo_fin,
                subtotal, valor_neto, estado, observaciones,
                tiene_informe_radio, estado_informe_radio, created_at
              """)
              .eq("id", prefactura_id)
              .single()
              .execute())

    if not resp.data:
        return None

    pf = resp.data
    pid = pf.get("paciente_id")
    pf["paciente"] = _obtener_paciente(pid) if pid else {}
    pf["items"]    = obtener_items_prefactura(prefactura_id)
    pf["informes"] = obtener_informes_prefactura(prefactura_id)
    return pf


def obtener_items_prefactura(prefactura_id: int) -> list[dict]:
    sb = get_supabase_admin()
    resp = (sb.table("fin_prefactura_items")
              .select("""
                id, prefactura_id, cita_id,
                cita_procedimiento_id, codigo_cups,
                descripcion, cantidad, valor_unitario, valor_total
              """)
              .eq("prefactura_id", prefactura_id)
              .execute())
    return resp.data or []


# ════════════════════════════════════════════
# INFORMES RADIOLÓGICOS
# ════════════════════════════════════════════

def obtener_informes_prefactura(prefactura_id: int) -> list[dict]:
    sb = get_supabase_admin()
    resp = (sb.table("hc_informes_radio")
              .select("*")
              .eq("prefactura_id", prefactura_id)
              .order("creado_at", desc=False)
              .execute())
    return resp.data or []


def crear_informe(
    prefactura_id:   int,
    paciente_id:     int,
    proc_id:         str,
    proc_nombre:     str,
    tipo:            str = "audio",
    empresa_id:      int | None = None,
    sede_id:         int | None = None,
    plantilla_id:    str | None = None,
    plantilla_nombre:str | None = None,
    creado_por:      int | None = None,
) -> dict:
    sb  = get_supabase_admin()
    iid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # Datos del paciente para guardar en el informe
    pac = _obtener_paciente(paciente_id)

    resp = sb.table("hc_informes_radio").insert({
        "id":                  iid,
        "prefactura_id":       prefactura_id,
        "paciente_id":         paciente_id,
        "empresa_id":          empresa_id,
        "sede_id":             sede_id,
        "proc_id":             proc_id,
        "proc_nombre":         proc_nombre,
        "tipo_transcripcion":  tipo,
        "plantilla_id":        plantilla_id,
        "plantilla_nombre":    plantilla_nombre,
        "estado":              "borrador",
        "creado_por":          creado_por,
        "creado_at":           now,
        "actualizado_at":      now,
    }).execute()

    sb.table("fin_prefacturas").update({
        "tiene_informe_radio":  True,
        "estado_informe_radio": "borrador",
    }).eq("id", prefactura_id).execute()

    _log(iid, None, "borrador", "Informe creado")
    return resp.data[0]


def obtener_informe(informe_id: str) -> dict | None:
    sb = get_supabase_admin()
    try:
        resp = (sb.table("hc_informes_radio")
                  .select("*")
                  .eq("id", informe_id)
                  .single()
                  .execute())
        return resp.data
    except Exception:
        return None


def guardar_bloques(
    informe_id:   str,
    bloques:      dict,
    informe_html: str = "",
    usuario_id:   int | None = None,
) -> dict:
    sb  = get_supabase_admin()
    now = datetime.now(timezone.utc).isoformat()
    resp = (sb.table("hc_informes_radio")
              .update({
                  "bloque_paciente":   bloques.get("paciente",   ""),
                  "bloque_estudio":    bloques.get("estudio",    ""),
                  "bloque_tecnica":    bloques.get("tecnica",    ""),
                  "bloque_hallazgos":  bloques.get("hallazgos",  ""),
                  "bloque_conclusion": bloques.get("conclusion", ""),
                  "informe_html":      informe_html,
                  "actualizado_at":    now,
                  "actualizado_por":   usuario_id,
              })
              .eq("id", informe_id)
              .execute())
    return resp.data[0] if resp.data else {}


def ligar_job_transcripcion(
    informe_id:      str,
    job_id:          str,
    storage_path:    str,
    nombre_original: str,
    duracion_seg:    float | None = None,
) -> None:
    sb = get_supabase_admin()
    sb.table("hc_informes_radio").update({
        "job_transcripcion_id": job_id,
        "audio_storage_path":   storage_path,
        "audio_nombre_orig":    nombre_original,
        "audio_duracion_seg":   duracion_seg,
        "actualizado_at":       datetime.now(timezone.utc).isoformat(),
    }).eq("id", informe_id).execute()


def enviar_a_revision(informe_id: str, usuario_id: int | None = None) -> dict:
    return _cambiar_estado(informe_id, "pendiente_revision",
                           "Enviado a revisión del radiólogo", usuario_id)


def devolver_correccion(
    informe_id:  str,
    observacion: str,
    usuario_id:  int | None = None,
) -> dict:
    return _cambiar_estado(informe_id, "en_correccion", observacion, usuario_id)


def liberar_informe(
    informe_id:      str,
    medico_id:       int,
    medico_nombre:   str,
    medico_registro: str,
    firma_url:       str,
    usuario_id:      int | None = None,
) -> dict:
    sb      = get_supabase_admin()
    now     = datetime.now(timezone.utc).isoformat()
    informe = obtener_informe(informe_id)
    if not informe:
        raise ValueError(f"Informe {informe_id} no encontrado")

    resp = (sb.table("hc_informes_radio")
              .update({
                  "estado":           "liberado",
                  "medico_id":        medico_id,
                  "medico_nombre":    medico_nombre,
                  "medico_registro":  medico_registro,
                  "firma_url":        firma_url,
                  "fecha_liberacion": now,
                  "actualizado_at":   now,
                  "actualizado_por":  usuario_id,
              })
              .eq("id", informe_id)
              .execute())

    sb.table("fin_prefacturas").update({
        "estado_informe_radio": "liberado",
    }).eq("id", informe["prefactura_id"]).execute()

    _log(informe_id, informe["estado"], "liberado",
         f"Liberado por {medico_nombre} · Reg. {medico_registro}")

    return resp.data[0] if resp.data else {}


# ════════════════════════════════════════════
# MÉDICO / FIRMA
# ════════════════════════════════════════════

def obtener_medico(medico_id: int) -> dict | None:
    sb = get_supabase_admin()
    try:
        resp = (sb.table("hc_profesionales")
                  .select("id, nombre_completo, registro_profesional, especialidad_id, firma_url")
                  .eq("id", medico_id)
                  .single()
                  .execute())
        return resp.data
    except Exception:
        return None


def listar_medicos_radiologia() -> list[dict]:
    sb = get_supabase_admin()
    resp = (sb.table("hc_profesionales")
              .select("id, nombre_completo, registro_profesional, firma_url")
              .execute())
    return resp.data or []


def guardar_firma_medico(medico_id: int, firma_bytes: bytes) -> str:
    sb   = get_supabase_admin()
    ruta = f"{medico_id}/firma.png"
    sb.storage.from_("firmas-medicos").upload(
        ruta, firma_bytes,
        {"content-type": "image/png", "upsert": "true"}
    )
    url = sb.storage.from_("firmas-medicos").get_public_url(ruta)
    sb.table("hc_profesionales").update({
        "firma_url":            url,
        "firma_actualizada_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", medico_id).execute()
    return url


# ════════════════════════════════════════════
# PLANTILLAS
# ════════════════════════════════════════════

def listar_plantillas(especialidad: str = "radiologia") -> list[dict]:
    sb = get_supabase_admin()
    resp = (sb.table("hc_plantillas_radio")
              .select("id, nombre, descripcion, tipo_estudio, storage_path")
              .eq("especialidad", especialidad)
              .eq("activo", True)
              .order("nombre")
              .execute())
    return resp.data or []


def url_plantilla(storage_path: str, expira_seg: int = 300) -> str:
    sb   = get_supabase_admin()
    resp = sb.storage.from_("plantillas-radio").create_signed_url(
        storage_path, expira_seg
    )
    return resp.get("signedURL", "")


# ════════════════════════════════════════════
# HELPERS INTERNOS
# ════════════════════════════════════════════

def _cambiar_estado(
    informe_id:   str,
    nuevo_estado: str,
    observacion:  str,
    usuario_id:   int | None,
) -> dict:
    sb      = get_supabase_admin()
    informe = obtener_informe(informe_id)
    ant     = informe["estado"] if informe else None
    now     = datetime.now(timezone.utc).isoformat()

    resp = (sb.table("hc_informes_radio")
              .update({
                  "estado":          nuevo_estado,
                  "actualizado_at":  now,
                  "actualizado_por": usuario_id,
              })
              .eq("id", informe_id)
              .execute())

    if informe:
        sb.table("fin_prefacturas").update({
            "estado_informe_radio": nuevo_estado,
        }).eq("id", informe["prefactura_id"]).execute()

    _log(informe_id, ant, nuevo_estado, observacion, usuario_id)
    return resp.data[0] if resp.data else {}


def _log(
    informe_id: str,
    anterior:   str | None,
    nuevo:      str,
    obs:        str = "",
    usuario_id: int | None = None,
) -> None:
    try:
        get_supabase_admin().table("hc_informes_radio_log").insert({
            "informe_id":      informe_id,
            "estado_anterior": anterior,
            "estado_nuevo":    nuevo,
            "observacion":     obs,
            "usuario_id":      usuario_id,
        }).execute()
    except Exception:
        pass