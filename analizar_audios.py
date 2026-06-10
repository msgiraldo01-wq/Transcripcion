# analizar_audios.py
# ─────────────────────────────────────────────
# VITACORE · Análisis de transcripción de 3 audios reales
#
# INSTRUCCIONES:
# 1. Copiar este archivo a C:\Manolo\Proyectos\Transcripcion\
# 2. Copiar los 3 audios a la misma carpeta:
#    fernando.ogg, hectotr.ogg, ramon.ogg
# 3. Ejecutar: python analizar_audios.py
# 4. Copiar el resultado completo y compartirlo
# ─────────────────────────────────────────────

import os
import subprocess
import tempfile
import json
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

# ── Cliente Groq ──────────────────────────────────────────────────
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# ── Prompt base ───────────────────────────────────────────────────
PROMPT_STT = (
    "Transcripción de dictado médico radiológico en español colombiano. "
    "El médico dicta hallazgos de un estudio de imagen. "
    "Términos frecuentes: cavum libre, tabique nasal centrado, "
    "engrosamiento parietal laminar, aspecto secretor, "
    "osteomeatales libres y permeables, trazos fracturarios, "
    "senos paranasales neumatizados, cornetes nasales, "
    "hipertrofia, osteoma frontal, proceso inflamatorio, "
    "bilateral, unilateral, permeable, neumatización."
)

AUDIOS = [
    "fernando.ogg",
    "hectotr.ogg",
    "ramon.ogg",
]


def preprocesar(ruta: str) -> str:
    """Convierte el audio a WAV 16kHz mono para mejor calidad."""
    tmp = tempfile.mktemp(suffix=".wav", prefix="vt_")
    subprocess.run([
        "ffmpeg", "-y", "-i", ruta,
        "-ac", "1", "-ar", "16000",
        "-af", "highpass=f=80,lowpass=f=8000,afftdn=nf=-25,loudnorm",
        tmp
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return tmp


def transcribir_raw(ruta_wav: str) -> dict:
    """Transcripción directa sin correcciones — texto crudo de Whisper."""
    with open(ruta_wav, "rb") as f:
        resp = client.audio.transcriptions.create(
            model="whisper-large-v3",
            file=f,
            language="es",
            prompt=PROMPT_STT,
            response_format="verbose_json",
            temperature=0.0,
        )
    return {
        "texto": resp.text.strip(),
        "idioma": getattr(resp, "language", "es"),
        "duracion_audio": round(getattr(resp, "duration", 0), 1),
    }


def estructurar_llm(texto: str) -> dict:
    """Estructura el texto con el LLM."""
    resp = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {
                "role": "system",
                "content": (
                    "Eres asistente de transcripción radiológica en Colombia. "
                    "Estructura el dictado en JSON con campos: "
                    "paciente, estudio, tecnica, hallazgos, conclusion. "
                    "INCLUYE TODO sin omitir hallazgos de normalidad ni negativos. "
                    "Solo JSON, sin markdown."
                )
            },
            {
                "role": "user",
                "content": f"Dictado:\n\"\"\"\n{texto}\n\"\"\"\n\nGenera el JSON."
            }
        ],
        temperature=0.05,
        max_tokens=3000,
        response_format={"type": "json_object"},
    )
    try:
        return json.loads(resp.choices[0].message.content)
    except Exception:
        return {"hallazgos": texto}


# ── MAIN ──────────────────────────────────────────────────────────
print("=" * 70)
print("VITACORE · ANÁLISIS DE TRANSCRIPCIÓN — 3 AUDIOS")
print("=" * 70)

resultados = {}

for nombre in AUDIOS:
    if not os.path.exists(nombre):
        print(f"\n⚠ Audio no encontrado: {nombre} — omitiendo")
        continue

    print(f"\n{'─' * 70}")
    print(f"PROCESANDO: {nombre}")
    print(f"{'─' * 70}")

    try:
        # Preprocesar
        print("  [1/3] Preprocesando audio con FFmpeg...")
        wav = preprocesar(nombre)

        # STT
        print("  [2/3] Transcribiendo con Whisper large-v3...")
        stt = transcribir_raw(wav)
        os.remove(wav)

        print(f"  → Duración audio: {stt['duracion_audio']}s")
        print(f"  → Chars transcritos: {len(stt['texto'])}")
        print(f"\n  TEXTO CRUDO WHISPER:")
        print(f"  {stt['texto']}")

        # LLM
        print("\n  [3/3] Estructurando con LLM...")
        estructura = estructurar_llm(stt["texto"])

        print(f"\n  ESTRUCTURA LLM:")
        for campo, valor in estructura.items():
            if valor:
                print(f"  [{campo.upper()}]: {valor}")

        resultados[nombre] = {
            "texto_raw": stt["texto"],
            "estructura": estructura,
            "duracion": stt["duracion_audio"],
        }

    except Exception as e:
        print(f"  ✗ ERROR: {e}")
        resultados[nombre] = {"error": str(e)}

# ── Guardar resultados ────────────────────────────────────────────
with open("resultados_analisis.json", "w", encoding="utf-8") as f:
    json.dump(resultados, f, ensure_ascii=False, indent=2)

print(f"\n{'=' * 70}")
print("ANÁLISIS COMPLETADO")
print(f"Resultados guardados en: resultados_analisis.json")
print(f"Comparte ese archivo para el análisis de errores.")
print(f"{'=' * 70}")