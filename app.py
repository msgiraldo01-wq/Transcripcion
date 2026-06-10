# app.py
# ─────────────────────────────────────────────
# VITACORE · Servidor Flask + SocketIO
# Fase 3+4: WebSocket + Prefacturas + Informes Radiológicos
# ─────────────────────────────────────────────

import os
import uuid
import tempfile

from flask import (
    Flask, render_template, request,
    jsonify, send_file, redirect, url_for
)
from flask_socketio import SocketIO, join_room, emit
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

load_dotenv()

from transcripcion_repo import (
    crear_job,
    obtener_job,
    guardar_informe,
    subir_audio_storage,
    obtener_url_audio,
    log_accion,
)
from diccionario_repo import (
    obtener_diccionario_completo,
    agregar_termino,
    eliminar_termino,
    agregar_correccion,
)
from cola import encolar_transcripcion
from transcripcion import exportar_word, exportar_pdf, limpiar_uploads

from fin_prefactura_repo import (
    listar_prefacturas,
    obtener_prefactura,
    obtener_items_prefactura,
    obtener_informes_prefactura,
    crear_informe,
    obtener_informe,
    guardar_bloques,
    enviar_a_revision,
    liberar_informe,
    listar_medicos_radiologia,
    listar_plantillas,
    url_plantilla,
    guardar_firma_medico,
    ligar_job_transcripcion,
    obtener_medico,
)
from plantilla_parser import parsear_plantilla_bytes
from blueprints.auth.routes import bp_auth
from blueprints.auth.decorators import login_required, rol_required

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "CAMBIA_EN_PRODUCCION")

# Config de Supabase para el login
app.config["SUPABASE_URL"]              = os.getenv("SUPABASE_URL")
app.config["SUPABASE_ANON_KEY"]         = os.getenv("SUPABASE_ANON_KEY")
app.config["SUPABASE_SERVICE_ROLE_KEY"] = os.getenv("SUPABASE_SERVICE_KEY")

app.register_blueprint(bp_auth)


socketio = SocketIO(
    app,
    async_mode="threading",
    cors_allowed_origins="*",
    logger=False,
    engineio_logger=False,
)

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads", "audio")
EXPORT_FOLDER = os.path.join(BASE_DIR, "static", "exports")
ALLOWED_AUDIO = {"mp3", "wav", "ogg", "m4a", "webm"}
MAX_FILE_MB   = 100

app.config["UPLOAD_FOLDER"]      = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_MB * 1024 * 1024

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(EXPORT_FOLDER, exist_ok=True)

# ── User IDs de desarrollo ────────────────────────────────────────
# UUID para transcripcion.jobs (campo user_id es UUID)
DEV_USER_UUID = os.getenv("DEV_USER_ID", "00000000-0000-0000-0000-000000000001")
# Entero para fin_prefacturas / hc_ (campo creado_por es bigint)
DEV_USER_INT  = int(os.getenv("DEV_USER_ID_INT", "1"))


# ════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════

def extension_valida(nombre: str) -> bool:
    return "." in nombre and nombre.rsplit(".", 1)[1].lower() in ALLOWED_AUDIO


def get_user_id() -> str:
    return DEV_USER_UUID


def get_ip() -> str:
    return request.headers.get("X-Forwarded-For", request.remote_addr)


# ════════════════════════════════════════════
# WEBSOCKET
# ════════════════════════════════════════════

@socketio.on("connect")
def on_connect():
    print(f"[WS] Cliente conectado: {request.sid}")


@socketio.on("disconnect")
def on_disconnect():
    print(f"[WS] Cliente desconectado: {request.sid}")


@socketio.on("unirse_job")
def on_unirse_job(data):
    job_id = data.get("job_id", "")
    if job_id:
        join_room(job_id)
        print(f"[WS] Cliente {request.sid} unido al job {job_id[:8]}...")
        job = obtener_job(job_id)
        if job and job.get("estado") == "done":
            emit("job_listo", {
                "job_id":      job_id,
                "html_clinico": job.get("html_clinico", ""),
                "estructura":  job.get("estructura") or {},
                "duracion":    job.get("duracion_proceso_seg", 0),
                "sugerencias": [],
            })
        elif job and job.get("estado") == "error":
            emit("job_error", {
                "job_id":  job_id,
                "mensaje": job.get("error_mensaje", "Error desconocido"),
            })


def notificar_job_listo(job_id: str, resultado: dict) -> None:
    socketio.emit(
        "job_listo",
        {
            "job_id":       job_id,
            "html_clinico": resultado.get("html_clinico", ""),
            "estructura":   resultado.get("estructura") or {},
            "duracion":     resultado.get("duracion", 0),
            "sugerencias":  resultado.get("sugerencias", []),
        },
        room=job_id,
        namespace="/",
    )
    print(f"[WS] Notificación enviada al job {job_id[:8]}")


def notificar_job_error(job_id: str, mensaje: str) -> None:
    socketio.emit(
        "job_error",
        {"job_id": job_id, "mensaje": mensaje},
        room=job_id,
        namespace="/",
    )


app.notificar_job_listo = notificar_job_listo
app.notificar_job_error = notificar_job_error


# ════════════════════════════════════════════
# VISTAS PRINCIPALES
# ════════════════════════════════════════════

@app.route("/")
def index():
    return redirect(url_for("vista_prefacturas"))


@app.route("/remisiones")
def vista_remisiones():
    return render_template("remisiones.html")


@app.route("/transcripcion/<job_id>")
def vista_transcripcion(job_id: str):
    job = obtener_job(job_id)
    if not job:
        return render_template("404.html", mensaje="Transcripción no encontrada"), 404

    print(f"[Vista] Job estado: {job.get('estado')}")
    print(f"[Vista] html_clinico presente: {bool(job.get('html_clinico'))}")

    audio_url = obtener_url_audio(job, expira_seg=3600)

    return render_template(
        "transcripcion.html",
        job=job,
        audio_url=audio_url,
        job_id=job_id,
    )


@app.route("/favicon.ico")
def favicon():
    return "", 204


# ════════════════════════════════════════════
# SUBIR AUDIO DESDE PREFACTURA
# ════════════════════════════════════════════

@app.route("/transcripcion/subir-audio", methods=["POST"])
def api_subir_audio_informe():
    """
    Recibe audio desde transcripcion_upload.html.
    Crea job, encola en Celery y liga al informe radiológico.
    """
    informe_id   = request.form.get("informe_id")
    especialidad = request.form.get("especialidad", "radiologia")
    bloque       = request.form.get("bloque", "general")

    if not informe_id:
        return jsonify({"ok": False, "error": "informe_id requerido"}), 400
    if "audio" not in request.files:
        return jsonify({"ok": False, "error": "Archivo audio requerido"}), 400

    archivo = request.files["audio"]
    if not archivo.filename:
        return jsonify({"ok": False, "error": "Archivo vacío"}), 400

    archivo.seek(0, 2)
    tam = archivo.tell()
    archivo.seek(0)
    if tam > 25 * 1024 * 1024:
        return jsonify({"ok": False, "error": "Audio supera 25MB"}), 400

    try:
        informe = obtener_informe(informe_id)
        if not informe:
            return jsonify({"ok": False, "error": "Informe no encontrado"}), 404

        paciente_id   = informe["paciente_id"]
        prefactura_id = informe["prefactura_id"]
        proc_nombre   = informe["proc_nombre"]
        proc_id       = str(informe.get("proc_id") or prefactura_id)
        nro_interno   = f"pf{prefactura_id}"

        # ── Obtener teléfono del paciente ─────────────────────────
        from fin_prefactura_repo import _obtener_paciente
        pac     = _obtener_paciente(paciente_id)
        pac_tel = (pac.get("celular") or pac.get("telefono") or "") if pac else ""

        # ── Obtener CUPS y valor del procedimiento ────────────────
        items      = obtener_items_prefactura(prefactura_id)
        item_match = next(
            (i for i in items
             if str(i.get("cita_procedimiento_id", "")) == str(proc_id)
             or str(i.get("id", "")) == str(proc_id)),
            items[0] if items else {}
        )
        item_cups  = item_match.get("codigo_cups", "")
        item_valor = float(item_match.get("valor_total", 0) or 0)

        nombre_original = archivo.filename
        ext = nombre_original.rsplit(".", 1)[-1].lower() if "." in nombre_original else "ogg"

        # Guardar temporal
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=f".{ext}", prefix="vt_")
        os.close(tmp_fd)
        archivo.save(tmp_path)

        # 1. Crear job PRIMERO — Supabase asigna el UUID real
        job = crear_job(
            nro_interno          = nro_interno,
            proc_id              = proc_id,
            proc_nombre          = proc_nombre,
            user_id              = DEV_USER_UUID,
            nro_factura          = str(prefactura_id),
            paciente_nombre      = proc_nombre,
            paciente_doc         = str(paciente_id),
            paciente_telefono    = pac_tel,    # ← NUEVO
            codigo_cups          = item_cups,  # ← NUEVO
            valor_procedimiento  = item_valor, # ← NUEVO
        )
        job_id = job["id"]   # UUID asignado por Supabase

        # 2. Subir audio al storage (usa el job_id real)
        storage_path = subir_audio_storage(
            job_id      = job_id,
            user_id     = DEV_USER_UUID,
            nro_interno = nro_interno,
            ruta_local  = tmp_path,
            nombre_orig = nombre_original,
        )

        try:
            os.remove(tmp_path)
        except Exception:
            pass

        # 3. Ligar job al informe radiológico
        ligar_job_transcripcion(
            informe_id      = informe_id,
            job_id          = job_id,
            storage_path    = storage_path,
            nombre_original = nombre_original,
        )

        # 4. Encolar en Celery
        from cola import procesar_transcripcion
        procesar_transcripcion.apply_async(
            args    = [job_id],
            kwargs  = {"especialidad": especialidad, "bloque": bloque},
            queue   = "transcripcion",
            task_id = job_id,
        )

        redirect_url = url_for("vista_transcripcion", job_id=job_id)

        return jsonify({
            "ok":           True,
            "job_id":       job_id,
            "informe_id":   informe_id,
            "redirect_url": redirect_url,
        })

    except Exception as e:
        print(f"[subir_audio] ERROR: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


# ════════════════════════════════════════════
# ESTADO DEL JOB (polling fallback)
# ════════════════════════════════════════════

@app.route("/transcripcion/<job_id>/estado", methods=["GET"])
def estado_job(job_id: str):
    job = obtener_job(job_id)
    if not job:
        return jsonify({"ok": False, "error": "Job no encontrado"}), 404

    resp = {
        "ok":    True,
        "estado": job["estado"],
        "listo":  job["estado"] in ("done", "error"),
    }

    if job["estado"] == "done":
        resp["html_clinico"] = job.get("html_clinico", "")
        resp["duracion"]     = job.get("duracion_proceso_seg", 0)
        resp["estructura"]   = job.get("estructura") or {}
    elif job["estado"] == "error":
        resp["error"] = job.get("error_mensaje", "Error desconocido")

    return jsonify(resp)


@app.route("/transcripcion/<job_id>/procesar", methods=["POST"])
def procesar(job_id: str):
    job = obtener_job(job_id)
    if not job:
        return jsonify({"ok": False, "error": "Job no encontrado"}), 404

    if job["estado"] == "done":
        return jsonify({"ok": True, "estado": "done",
                        "html_clinico": job.get("html_clinico", "")})

    data         = request.get_json(silent=True) or {}
    especialidad = data.get("especialidad", "radiologia")
    bloque       = data.get("bloque", "general")

    encolar_transcripcion(job_id=job_id, especialidad=especialidad,
                          bloque=bloque, user_id=DEV_USER_UUID)

    return jsonify({"ok": True, "estado": "processing"})


# ════════════════════════════════════════════
# GUARDAR EN HC
# ════════════════════════════════════════════

@app.route("/transcripcion/<job_id>/guardar", methods=["POST"])
def guardar(job_id: str):
    data          = request.get_json(silent=True) or {}
    informe_html  = data.get("informe_html",  "").strip()
    informe_final = data.get("informe_final", "").strip()

    if not informe_html and not informe_final:
        return jsonify({"ok": False, "error": "Informe vacío"}), 400

    try:
        # Guardar en transcripcion.jobs (flujo original)
        guardar_informe(
            job_id        = job_id,
            informe_html  = informe_html,
            informe_final = informe_final,
            guardado_por  = DEV_USER_UUID,
        )

        # ── NUEVO: si hay un informe radiológico ligado, actualizar también ──
        job = obtener_job(job_id)
        if job:
            # Buscar informe en hc_informes_radio por job_transcripcion_id
            from supabase_client import get_admin_client
            sb = get_admin_client()
            resp = sb.table("hc_informes_radio")\
                     .select("id")\
                     .eq("job_transcripcion_id", job_id)\
                     .execute()

            if resp.data:
                informe_id = resp.data[0]["id"]
                estructura = job.get("estructura") or {}
                guardar_bloques(
                    informe_id   = informe_id,
                    bloques      = {
                        "paciente":   estructura.get("paciente",   ""),
                        "estudio":    estructura.get("estudio",    ""),
                        "tecnica":    estructura.get("tecnica",    ""),
                        "hallazgos":  estructura.get("hallazgos",  ""),
                        "conclusion": estructura.get("conclusion", ""),
                    },
                    informe_html = informe_html,
                    usuario_id   = DEV_USER_INT,
                )
                # Cambiar estado a pendiente_revision
                enviar_a_revision(informe_id, usuario_id=DEV_USER_INT)

        log_accion(job_id, "save_informe", user_id=DEV_USER_UUID, ip=get_ip())
        return jsonify({"ok": True})

    except Exception as e:
        print(f"[guardar] ERROR: {e}")
        import traceback; traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500

# ════════════════════════════════════════════
# EXPORTAR
# ════════════════════════════════════════════

@app.route("/transcripcion/<job_id>/exportar-word", methods=["POST"])
def exportar_word_ruta(job_id: str):
    data   = request.get_json(silent=True) or {}
    texto  = data.get("texto", "").strip()
    nombre = data.get("nombre", "informe").strip() or "informe"
    if not texto:
        return jsonify({"ok": False, "error": "Texto vacío"}), 400

    nombre_archivo = secure_filename(f"{nombre}_{uuid.uuid4().hex[:6]}.docx")
    ruta = exportar_word(texto, nombre_archivo, EXPORT_FOLDER)
    log_accion(job_id, "export_word", user_id=DEV_USER_UUID, ip=get_ip())
    return send_file(ruta, as_attachment=True, download_name=nombre_archivo,
                     mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


@app.route("/transcripcion/<job_id>/exportar-pdf", methods=["POST"])
def exportar_pdf_ruta(job_id: str):
    data   = request.get_json(silent=True) or {}
    texto  = data.get("texto", "").strip()
    nombre = data.get("nombre", "informe").strip() or "informe"
    if not texto:
        return jsonify({"ok": False, "error": "Texto vacío"}), 400

    nombre_archivo = secure_filename(f"{nombre}_{uuid.uuid4().hex[:6]}.pdf")
    ruta = exportar_pdf(texto, nombre_archivo, EXPORT_FOLDER)
    log_accion(job_id, "export_pdf", user_id=DEV_USER_UUID, ip=get_ip())
    return send_file(ruta, as_attachment=True, download_name=nombre_archivo,
                     mimetype="application/pdf")


# ════════════════════════════════════════════
# DICCIONARIO
# ════════════════════════════════════════════

@app.route("/diccionario", methods=["GET"])
def api_diccionario():
    try:
        return jsonify(obtener_diccionario_completo(DEV_USER_UUID))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/diccionario/agregar", methods=["POST"])
def api_agregar_termino():
    data      = request.get_json(silent=True) or {}
    termino   = data.get("termino",   "").strip()
    categoria = data.get("categoria", "propios").strip()
    if not termino:
        return jsonify({"ok": False, "error": "Falta el término"}), 400
    agregado = agregar_termino(termino, categoria, user_id=DEV_USER_UUID)
    return jsonify({"ok": True, "agregado": agregado})


@app.route("/diccionario/eliminar", methods=["POST"])
def api_eliminar_termino():
    data    = request.get_json(silent=True) or {}
    termino = data.get("termino", "").strip()
    if not termino:
        return jsonify({"ok": False, "error": "Falta el término"}), 400
    eliminado = eliminar_termino(termino, user_id=DEV_USER_UUID)
    return jsonify({"ok": True, "eliminado": eliminado})


@app.route("/diccionario/correccion", methods=["POST"])
@app.route("/diccionario/aprender",   methods=["POST"])
def api_agregar_correccion():
    data       = request.get_json(silent=True) or {}
    incorrecto = data.get("incorrecto", data.get("original", "")).strip()
    correcto   = data.get("correcto",   data.get("sugerida", "")).strip()
    if not incorrecto or not correcto:
        return jsonify({"ok": False, "error": "Datos incompletos"}), 400
    ok = agregar_correccion(incorrecto, correcto, user_id=DEV_USER_UUID)
    return jsonify({"ok": ok})


# ════════════════════════════════════════════
# FASE 3 — PREFACTURAS
# ════════════════════════════════════════════

@app.route("/prefacturas")
@login_required
def vista_prefacturas():
    return render_template("prefactura.html")


@app.route("/api/prefacturas")
def api_prefacturas():
    try:
        sede_id = request.args.get("sede_id", type=int)
        limit   = request.args.get("limit",   type=int, default=50)
        offset  = request.args.get("offset",  type=int, default=0)

        prefacturas = listar_prefacturas(sede_id=sede_id, limit=limit, offset=offset)
        for pf in prefacturas:
            pf["items"]    = obtener_items_prefactura(pf["id"])
            pf["informes"] = obtener_informes_prefactura(pf["id"])

        return jsonify({"ok": True, "prefacturas": prefacturas})
    except Exception as e:
        print(f"[api_prefacturas] ERROR: {e}")
        import traceback; traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/prefacturas/crear-informe", methods=["POST"])
def crear_informe_radio():
    body = request.get_json()
    prefactura_id    = body.get("prefactura_id")
    paciente_id      = body.get("paciente_id")
    proc_id          = str(body.get("proc_id", ""))
    proc_nombre      = body.get("proc_nombre", "Estudio radiológico")
    tipo             = body.get("tipo", "audio")
    plantilla_id     = body.get("plantilla_id")
    plantilla_nombre = body.get("plantilla_nombre")

    if not prefactura_id or not paciente_id:
        return jsonify({"ok": False, "error": "prefactura_id y paciente_id requeridos"}), 400

    pf = obtener_prefactura(int(prefactura_id))
    if not pf:
        return jsonify({"ok": False, "error": "Prefactura no encontrada"}), 404

    try:
        informe = crear_informe(
            prefactura_id    = int(prefactura_id),
            paciente_id      = int(paciente_id),
            proc_id          = proc_id,
            proc_nombre      = proc_nombre,
            tipo             = tipo,
            empresa_id       = pf.get("empresa_id"),
            sede_id          = pf.get("sede_id"),
            plantilla_id     = plantilla_id,
            plantilla_nombre = plantilla_nombre,
            creado_por       = DEV_USER_INT,
        )
        informe_id = informe["id"]

        if tipo == "audio":
            redirect_url = url_for("vista_transcripcion_desde_prefactura",
                                   informe_id=informe_id)
        else:
            redirect_url = url_for("vista_informe_radio", informe_id=informe_id)

        return jsonify({"ok": True, "informe_id": informe_id,
                        "redirect_url": redirect_url})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/prefacturas/transcripcion/<informe_id>")
def vista_transcripcion_desde_prefactura(informe_id: str):
    informe = obtener_informe(informe_id)
    if not informe:
        return "Informe no encontrado", 404

    pf = obtener_prefactura(informe["prefactura_id"])
    if not pf:
        return "Prefactura no encontrada", 404

    return render_template(
        "transcripcion_upload.html",
        informe   = informe,
        prefactura= pf,
        paciente  = pf.get("paciente", {}),
    )


# ════════════════════════════════════════════
# FASE 3 — INFORMES RADIOLÓGICOS
# ════════════════════════════════════════════

@app.route("/informe/<informe_id>")
def vista_informe_radio(informe_id: str):
    informe = obtener_informe(informe_id)
    if not informe:
        return "Informe no encontrado", 404

    pf = obtener_prefactura(informe["prefactura_id"])
    
    if pf:
        pf["items"] = obtener_items_prefactura(pf["id"])

    medico  = obtener_medico(informe["medico_id"]) if informe.get("medico_id") else None
    medicos = listar_medicos_radiologia()

    return render_template(
        "informe_radio.html",
        informe    = informe,
        prefactura = pf,
        paciente   = pf.get("paciente", {}) if pf else {},
        medico     = medico,
        medicos    = medicos,
    )

@app.route("/informe/<informe_id>/guardar", methods=["POST"])
def api_guardar_informe(informe_id: str):
    body = request.get_json()
    bloques = {
        "paciente":   body.get("bloque_paciente",   ""),
        "estudio":    body.get("bloque_estudio",     ""),
        "tecnica":    body.get("bloque_tecnica",     ""),
        "hallazgos":  body.get("bloque_hallazgos",   ""),
        "conclusion": body.get("bloque_conclusion",  ""),
    }
    try:
        guardar_bloques(
            informe_id   = informe_id,
            bloques      = bloques,
            informe_html = body.get("informe_html", ""),
            usuario_id   = DEV_USER_INT,
        )
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/informe/<informe_id>/enviar-revision", methods=["POST"])
def api_enviar_revision(informe_id: str):
    try:
        enviar_a_revision(informe_id, usuario_id=DEV_USER_INT)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/informe/<informe_id>/liberar", methods=["POST"])
def api_liberar_informe(informe_id: str):
    body      = request.get_json()
    medico_id = body.get("medico_id")

    if not medico_id:
        return jsonify({"ok": False, "error": "medico_id requerido"}), 400

    medico = obtener_medico(int(medico_id))
    if not medico:
        return jsonify({"ok": False, "error": "Médico no encontrado"}), 404

    if not medico.get("firma_url"):
        return jsonify({"ok": False, "error": "El médico no tiene firma registrada",
                        "requiere_firma": True}), 400

    try:
        liberar_informe(
            informe_id      = informe_id,
            medico_id       = int(medico_id),
            medico_nombre   = medico["nombre_completo"],
            medico_registro = medico.get("registro_profesional", ""),
            firma_url       = medico["firma_url"],
            usuario_id      = DEV_USER_INT,
        )
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ════════════════════════════════════════════
# FIRMA DEL MÉDICO
# ════════════════════════════════════════════

@app.route("/medico/<int:medico_id>/subir-firma", methods=["POST"])
def api_subir_firma(medico_id: int):
    if "firma" not in request.files:
        return jsonify({"ok": False, "error": "Archivo firma requerido"}), 400

    archivo = request.files["firma"]
    if not archivo.filename.lower().endswith(".png"):
        return jsonify({"ok": False, "error": "Solo se acepta PNG"}), 400

    contenido = archivo.read()
    if len(contenido) > 2 * 1024 * 1024:
        return jsonify({"ok": False, "error": "Máximo 2MB"}), 400

    try:
        url = guardar_firma_medico(medico_id, contenido)
        return jsonify({"ok": True, "firma_url": url})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ════════════════════════════════════════════
# PLANTILLAS
# ════════════════════════════════════════════

@app.route("/plantillas-radio")
def api_plantillas():
    return jsonify({"ok": True, "plantillas": listar_plantillas()})


@app.route("/plantillas-radio/<plantilla_id>/descargar")
def descargar_plantilla(plantilla_id: str):
    plantillas = listar_plantillas()
    plantilla  = next((p for p in plantillas if p["id"] == plantilla_id), None)
    if not plantilla:
        return "Plantilla no encontrada", 404
    signed_url = url_plantilla(plantilla["storage_path"])
    if not signed_url:
        return "No se pudo generar la URL", 500
    return redirect(signed_url)


@app.route("/plantillas-radio/<plantilla_id>/parsear", methods=["POST"])
def parsear_plantilla(plantilla_id: str):
    """
    Descarga la plantilla Word, extrae los 5 bloques y
    los guarda en el informe radiológico indicado.
    """
    from plantilla_parser import parsear_plantilla_bytes
 
    body       = request.get_json(silent=True) or {}
    informe_id = body.get("informe_id")
 
    if not informe_id:
        return jsonify({"ok": False, "error": "informe_id requerido"}), 400
 
    # Buscar la plantilla
    plantillas = listar_plantillas()
    plantilla  = next((p for p in plantillas if p["id"] == plantilla_id), None)
    if not plantilla:
        return jsonify({"ok": False, "error": "Plantilla no encontrada"}), 404
 
    try:
        # Descargar el .docx desde Storage
        from supabase_client import get_admin_client
        sb = get_admin_client()
        contenido_bytes = sb.storage.from_("plantillas-radio")\
                            .download(plantilla["storage_path"])
 
        # Parsear los 5 bloques
        from plantilla_parser import parsear_plantilla_bytes
        bloques = parsear_plantilla_bytes(contenido_bytes)
 
        if not bloques["ok"]:
            return jsonify({"ok": False,
                            "error": f"Error al leer plantilla: {bloques['error']}"}), 500
 
        # Obtener datos del paciente desde el informe
        informe = obtener_informe(informe_id)
        if not informe:
            return jsonify({"ok": False, "error": "Informe no encontrado"}), 404
 
        # Enriquecer bloque paciente con datos reales
        paciente_id = informe.get("paciente_id")
        if paciente_id:
            from fin_prefactura_repo import _obtener_paciente
            pac = _obtener_paciente(paciente_id)
            if pac:
                nombre = pac.get("nombre_completo") or \
                         f"{pac.get('primer_nombre','')} {pac.get('primer_apellido','')}".strip()
                doc    = pac.get("numero_documento", "")
                bloques["paciente"] = f"Paciente: {nombre}\nDocumento: {doc}"
 
        # Guardar bloques en el informe
        guardar_bloques(
            informe_id   = informe_id,
            bloques      = bloques,
            informe_html = "",
            usuario_id   = DEV_USER_INT,
        )
 
        # URL para ir al editor del informe
        redirect_url = url_for("vista_informe_radio", informe_id=informe_id)
 
        return jsonify({
            "ok":          True,
            "bloques":     bloques,
            "redirect_url": redirect_url,
        })
 
    except Exception as e:
        print(f"[parsear_plantilla] ERROR: {e}")
        import traceback; traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500
 
 
@app.route("/plantillas-radio/subir", methods=["POST"])
def api_subir_plantilla():
    """
    Sube una nueva plantilla Word al bucket plantillas-radio
    y la registra en hc_plantillas_radio.
    """
    if "plantilla" not in request.files:
        return jsonify({"ok": False, "error": "Archivo requerido"}), 400
 
    archivo      = request.files["plantilla"]
    nombre       = request.form.get("nombre", "").strip()
    descripcion  = request.form.get("descripcion", "").strip()
    tipo_estudio = request.form.get("tipo_estudio", "").strip()
    especialidad = request.form.get("especialidad", "radiologia").strip()
 
    if not nombre:
        return jsonify({"ok": False, "error": "Nombre requerido"}), 400
 
    if not archivo.filename.lower().endswith(".docx"):
        return jsonify({"ok": False, "error": "Solo se aceptan archivos .docx"}), 400
 
    contenido = archivo.read()
    if len(contenido) > 20 * 1024 * 1024:
        return jsonify({"ok": False, "error": "Máximo 20MB"}), 400
 
    try:
        from werkzeug.utils import secure_filename
        nombre_archivo = secure_filename(archivo.filename)
        storage_path   = f"{especialidad}/{nombre_archivo}"
 
        from supabase_client import get_admin_client
        sb = get_admin_client()
        sb.storage.from_("plantillas-radio").upload(
            storage_path, contenido,
            {"content-type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
             "upsert": "true"}
        )
 
        resp = sb.table("hc_plantillas_radio").insert({
            "nombre":       nombre,
            "descripcion":  descripcion,
            "especialidad": especialidad,
            "tipo_estudio": tipo_estudio,
            "storage_path": storage_path,
            "creado_por":   DEV_USER_INT,
        }).execute()
 
        return jsonify({"ok": True, "plantilla": resp.data[0] if resp.data else {}})
 
    except Exception as e:
        print(f"[subir_plantilla] ERROR: {e}")
        import traceback; traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500
 





# ════════════════════════════════════════════
# EXPORTAR INFORME RADIOLÓGICO (sin job_id)
# ════════════════════════════════════════════

@app.route("/informe/<informe_id>/exportar-word", methods=["POST"])
def exportar_word_informe(informe_id: str):
    data   = request.get_json(silent=True) or {}
    texto  = data.get("texto", "").strip()
    nombre = data.get("nombre", "informe").strip() or "informe"
    if not texto:
        return jsonify({"ok": False, "error": "Texto vacío"}), 400

    nombre_archivo = secure_filename(f"{nombre}_{uuid.uuid4().hex[:6]}.docx")
    ruta = exportar_word(texto, nombre_archivo, EXPORT_FOLDER)
    return send_file(
        ruta,
        as_attachment=True,
        download_name=nombre_archivo,
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )


@app.route("/informe/<informe_id>/exportar-pdf", methods=["POST"])
def exportar_pdf_informe(informe_id: str):
    data   = request.get_json(silent=True) or {}
    texto  = data.get("texto", "").strip()
    nombre = data.get("nombre", "informe").strip() or "informe"
    if not texto:
        return jsonify({"ok": False, "error": "Texto vacío"}), 400

    nombre_archivo = secure_filename(f"{nombre}_{uuid.uuid4().hex[:6]}.pdf")
    ruta = exportar_pdf(texto, nombre_archivo, EXPORT_FOLDER)
    return send_file(
        ruta,
        as_attachment=True,
        download_name=nombre_archivo,
        mimetype="application/pdf"
    )
    
# ════════════════════════════════════════════
# ARRANCAR
# ════════════════════════════════════════════

if __name__ == "__main__":
    socketio.run(
        app,
        debug=False,
        port=5000,
        use_reloader=True,
        log_output=False,
    )
    