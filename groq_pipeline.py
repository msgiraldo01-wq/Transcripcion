# groq_pipeline.py
# ─────────────────────────────────────────────
# VITACORE · Pipeline Groq — v4 Performance Fix
#
# FIX CRÍTICO DE RENDIMIENTO:
#
# PROBLEMA: incrementar_uso_correccion() hacía GET+PATCH a Supabase
# por cada corrección aplicada. Con 11 correcciones = 11×30s = 330s
#
# SOLUCIÓN: Eliminar completamente incrementar_uso_correccion()
# del pipeline hot path. Los contadores de uso son una métrica
# secundaria — no justifican 290 segundos de latencia.
# Si se necesitan en el futuro, implementar como batch asíncrono.
# ─────────────────────────────────────────────

import os
import re
import time
import subprocess
import tempfile
import difflib
import unicodedata
from typing import Optional

from groq import Groq

from diccionario_repo import (
    obtener_terminos,
    obtener_correcciones,
)
from estructura_clinica import generar_html_clinico

# NOTA: incrementar_uso_correccion eliminado del import
# para evitar llamadas individuales a Supabase en el pipeline

_groq_client: Optional[Groq] = None

def get_groq() -> Groq:
    global _groq_client
    if _groq_client is None:
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError("[Groq] Variable GROQ_API_KEY no definida en .env")
        _groq_client = Groq(api_key=api_key)
    return _groq_client


# ════════════════════════════════════════════
# PREPROCESAMIENTO DE AUDIO
# ════════════════════════════════════════════

def preprocess_audio(ruta_audio: str) -> str:
    tmp_fd, salida = tempfile.mkstemp(suffix=".wav", prefix="vt_pre_")
    os.close(tmp_fd)
    cmd = [
        "ffmpeg", "-y", "-i", ruta_audio,
        "-ac", "1", "-ar", "16000",
        "-af", "highpass=f=80,lowpass=f=8000,afftdn=nf=-25,loudnorm",
        salida
    ]
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode != 0:
        raise RuntimeError(f"FFmpeg: {r.stderr.decode('utf-8', errors='ignore')[:300]}")
    return salida


# ════════════════════════════════════════════
# PROMPT STT v4
# Corto, sin palabras sueltas al final que Whisper continúe
# ════════════════════════════════════════════

PROMPT_STT_BASE = (
    "Dictado médico radiológico en español colombiano. "
    "Senos paranasales, cornetes nasales, tabique nasal, cavum, "
    "ostium maxilar, torus tubárico, fóvea etmoidal, fosita de Rosenmüller, "
    "lámina papirácea, complejo osteomeatal, neumatización, "
    "engrosamiento parietal, aspecto secretor, nivel hidroaéreo, "
    "morfología paradójica, trazos fracturarios. Fin del contexto."
)


def _construir_prompt_stt(bloque: str = "general") -> str:
    return PROMPT_STT_BASE


GROQ_MAX_BYTES = 25 * 1024 * 1024


def transcribir_audio_groq(
    ruta_audio: str,
    bloque:     str = "general",
    idioma:     str = "es",
) -> dict:
    groq   = get_groq()
    prompt = _construir_prompt_stt(bloque)
    tam    = os.path.getsize(ruta_audio)

    if tam > GROQ_MAX_BYTES:
        return _transcribir_segmentado(ruta_audio, prompt, idioma)

    t0 = time.time()
    with open(ruta_audio, "rb") as f:
        resp = groq.audio.transcriptions.create(
            model="whisper-large-v3",
            file=f,
            language=idioma,
            prompt=prompt,
            response_format="verbose_json",
            temperature=0.0,
        )
    return {
        "texto":          resp.text.strip(),
        "idioma":         getattr(resp, "language", idioma),
        "duracion":       round(time.time() - t0, 2),
        "audio_duracion": getattr(resp, "duration", 0.0),
    }


def _transcribir_segmentado(ruta_audio: str, prompt: str, idioma: str) -> dict:
    groq    = get_groq()
    tmp_dir = tempfile.mkdtemp(prefix="vt_chunks_")
    patron  = os.path.join(tmp_dir, "chunk_%03d.wav")
    subprocess.run([
        "ffmpeg", "-y", "-i", ruta_audio,
        "-f", "segment", "-segment_time", "1200",
        "-ac", "1", "-ar", "16000", patron
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    chunks = sorted([
        os.path.join(tmp_dir, f)
        for f in os.listdir(tmp_dir) if f.startswith("chunk_")
    ])
    textos = []
    t0     = time.time()
    for chunk in chunks:
        with open(chunk, "rb") as f:
            r = groq.audio.transcriptions.create(
                model="whisper-large-v3", file=f,
                language=idioma, prompt=prompt,
                response_format="text", temperature=0.0,
            )
        textos.append(str(r).strip())
        os.remove(chunk)
    try:
        os.rmdir(tmp_dir)
    except Exception:
        pass
    return {
        "texto":          " ".join(textos),
        "idioma":         idioma,
        "duracion":       round(time.time() - t0, 2),
        "audio_duracion": 0.0,
    }


# ════════════════════════════════════════════
# LIMPIEZA DE ARTEFACTOS DEL PROMPT
# ════════════════════════════════════════════

_ARTEFACTOS = [
    "fin del contexto",
    "neumatizados neumatizadores libres",
    "neumatizados neumatizadores",
    "neumatizadores libres",
    "dictado médico radiológico en español colombiano",
]

def limpiar_artefactos(texto: str) -> str:
    resultado = texto
    for art in _ARTEFACTOS:
        resultado = re.sub(
            re.escape(art), '', resultado, flags=re.IGNORECASE
        )
    resultado = re.sub(r'\s+', ' ', resultado)
    resultado = re.sub(r',\s*\.', '.', resultado)
    resultado = re.sub(r'\.\s*\.', '.', resultado)
    return resultado.strip()


# ════════════════════════════════════════════
# CORRECTOR DIFUSO
# SIN llamadas a Supabase — solo operaciones en memoria
# ════════════════════════════════════════════

def normalizar_str(txt: str) -> str:
    txt = txt.lower()
    return unicodedata.normalize("NFKD", txt).encode("ascii", "ignore").decode()


def corregir_texto(
    texto:        str,
    correcciones: dict,
    terminos:     list,
) -> tuple:
    """
    Aplica correcciones en memoria — CERO llamadas a Supabase.
    Las correcciones y términos ya están cargados en caché.
    """
    if not texto:
        return texto, []

    resultado   = texto
    sugerencias = []

    # ── Correcciones exactas — solo regex en memoria ─────────────
    for malo, bueno in correcciones.items():
        if not malo:
            continue
        p = re.compile(r'\b' + re.escape(malo) + r'\b', re.IGNORECASE)
        if p.search(resultado):
            resultado = p.sub(bueno, resultado)
            # SIN incrementar_uso_correccion() — era la causa del problema

    # ── Corrector difuso — solo operaciones en memoria ───────────
    tnorm    = [normalizar_str(t) for t in terminos]
    palabras = list(set(re.findall(r'\b[\wáéíóúñüÁÉÍÓÚÑÜ]+\b', resultado)))

    for palabra in palabras:
        if len(palabra) < 5:
            continue
        pnorm   = normalizar_str(palabra)
        matches = difflib.get_close_matches(pnorm, tnorm, n=1, cutoff=0.94)
        if matches:
            idx = tnorm.index(matches[0])
            sug = terminos[idx]
            if palabra.lower() != sug.lower():
                sugerencias.append({"original": palabra, "sugerida": sug})

    # ── Limpieza ─────────────────────────────────────────────────
    resultado = re.sub(r'\s+', ' ', resultado).strip()
    if resultado:
        resultado = resultado[0].upper() + resultado[1:]

    return resultado, sugerencias


# ════════════════════════════════════════════
# PROMPT LLM v3 — sin cambios
# ════════════════════════════════════════════

PROMPT_LLM_V3 = """Eres un asistente de transcripción de informes radiológicos para hospitales en Colombia.

TAREA: Recibir el dictado de un radiólogo y estructurarlo en JSON con exactamente 5 campos string.

REGLAS ABSOLUTAS:

1. TODOS LOS CAMPOS SON STRINGS DE TEXTO PLANO.
   NO uses objetos, arrays, ni JSON anidado dentro de los campos.

   CORRECTO:
   {"hallazgos": "Engrosamiento parietal laminar. Cavum libre. Sin trazos fracturarios."}

   INCORRECTO:
   {"hallazgos": {"senos": "engrosamiento", "cavum": "libre"}}

2. INCLUYE TODO LO QUE DIJO EL MÉDICO en el campo hallazgos.
   No resumas. No comprimas. No omitas.
   Incluye hallazgos normales, negativos y medidas exactas.

3. NO INVENTES DATOS.
   Si el médico no mencionó paciente, estudio o técnica deja el campo vacío: ""
   Nunca escribas "No especificado" ni estructuras vacías.

4. CORRECCIÓN ORTOGRÁFICA:
   Corrige errores obvios: "osteomía" → "ostium", "torus tubarico" → "torus tubárico",
   "sin oso" → "sinuoso", "actura simétrica" → "altura simétrica",
   "asalto de la derecha" → "hacia la derecha", "hidro aéreo" → "hidroaéreo".

5. CONCORDANCIA: Verifica género de adjetivos.
   "tabique centrado" ✓ (no "centrada"), "seno derecho" ✓ (no "derecha").

6. CONCLUSIÓN: Si el médico no dictó conclusión, genera una breve basada en hallazgos.

FORMATO — solo JSON, sin markdown:
{
  "paciente": "",
  "estudio": "",
  "tecnica": "",
  "hallazgos": "texto completo de todos los hallazgos",
  "conclusion": "impresión diagnóstica"
}"""


def estructurar_con_llm(texto: str) -> dict:
    import json
    groq = get_groq()

    prompt_usuario = (
        f"Dictado del radiólogo:\n\"\"\"\n{texto}\n\"\"\"\n\n"
        f"Recuerda: hallazgos debe ser un STRING, no un objeto JSON."
    )

    try:
        resp = groq.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": PROMPT_LLM_V3},
                {"role": "user",   "content": prompt_usuario},
            ],
            temperature=0.05,
            max_tokens=3000,
            response_format={"type": "json_object"},
        )

        raw  = resp.choices[0].message.content.strip()
        data = json.loads(raw)

        # Garantizar 5 campos string
        campos = ["paciente", "estudio", "tecnica", "hallazgos", "conclusion"]
        for campo in campos:
            val = data.get(campo, "")
            if isinstance(val, (dict, list)):
                if isinstance(val, dict):
                    partes = []
                    for k, v in val.items():
                        if isinstance(v, dict):
                            partes.extend(str(v2) for v2 in v.values() if v2)
                        elif v:
                            partes.append(str(v))
                    val = ". ".join(partes)
                elif isinstance(val, list):
                    val = ". ".join(str(i) for i in val)
            data[campo] = val or ""

        return data

    except json.JSONDecodeError as e:
        print(f"[LLM] Error JSON: {e}")
        return {
            "paciente": "", "estudio": "", "tecnica": "",
            "hallazgos": texto, "conclusion": "",
        }
    except Exception as e:
        print(f"[LLM] Error: {e}")
        raise


# ════════════════════════════════════════════
# PIPELINE COMPLETO
# ════════════════════════════════════════════

def transcribir_con_groq(
    ruta_audio:   str,
    especialidad: str = "radiologia",
    bloque:       str = "general",
    idioma:       str = "es",
    user_id:      Optional[str] = None,
) -> dict:
    audio_procesado = None
    try:
        t0 = time.time()

        # Cargar diccionarios — cacheados 5 min, UNA sola llamada
        t_dic = time.time()
        correcciones = obtener_correcciones(especialidad, user_id)
        terminos     = obtener_terminos(especialidad, user_id)
        print(f"[Groq] Diccionario cargado en {round(time.time()-t_dic,1)}s "
              f"({len(correcciones)} correcciones, {len(terminos)} términos)")

        # Preprocesar audio
        print(f"[Groq] Preprocesando: {os.path.basename(ruta_audio)}")
        t_ffmpeg = time.time()
        audio_procesado = preprocess_audio(ruta_audio)
        print(f"[Groq] FFmpeg ok: {round(time.time()-t_ffmpeg,1)}s")

        # STT
        print(f"[Groq] STT whisper-large-v3...")
        t_stt = time.time()
        stt   = transcribir_audio_groq(audio_procesado, bloque, idioma)
        print(f"[Groq] STT ok: {len(stt['texto'])} chars en {round(time.time()-t_stt,1)}s")
        print(f"[Groq] Raw: {stt['texto'][:200]}...")

        # Limpiar artefactos + corregir — TODO en memoria, sin Supabase
        texto_limpio    = limpiar_artefactos(stt["texto"])
        texto_corregido, sugerencias = corregir_texto(
            texto_limpio, correcciones, terminos
        )

        # LLM estructuración
        print(f"[Groq] Estructurando LLM...")
        t_llm     = time.time()
        estructura = estructurar_con_llm(texto_corregido)
        print(f"[Groq] LLM ok: {round(time.time()-t_llm,1)}s")

        if not isinstance(estructura.get("hallazgos"), str):
            estructura["hallazgos"] = str(estructura.get("hallazgos", ""))

        html_clinico = generar_html_clinico(estructura)
        duracion     = round(time.time() - t0, 2)
        print(f"[Groq] Pipeline TOTAL: {duracion}s")

        return {
            "texto_raw":       stt["texto"],
            "texto_corregido": texto_corregido,
            "estructura":      estructura,
            "html_clinico":    html_clinico,
            "sugerencias":     sugerencias,
            "idioma":          stt["idioma"],
            "audio_duracion":  stt["audio_duracion"],
            "modelo_stt":      "groq-whisper-large-v3",
            "modelo_llm":      "groq-llama-3.3-70b-versatile",
            "error":           None,
        }

    except Exception as e:
        print(f"[Groq] ERROR: {e}")
        return {
            "texto_raw": "", "texto_corregido": "", "estructura": {},
            "html_clinico": "", "sugerencias": [], "idioma": idioma,
            "audio_duracion": 0.0, "modelo_stt": "groq-whisper-large-v3",
            "modelo_llm": "groq-llama-3.3-70b-versatile", "error": str(e),
        }
    finally:
        if audio_procesado and os.path.exists(audio_procesado):
            try:
                os.remove(audio_procesado)
            except Exception:
                pass