import re


SECCIONES = {

    "paciente": [
        "paciente",
        "nombre",
        "identificación",
        "identificacion"
    ],

    "estudio": [
        "estudio",
        "tomografía",
        "tomografia",
        "ecografía",
        "ecografia",
        "radiografía",
        "radiografia",
        "resonancia"
    ],

    "tecnica": [
        "técnica",
        "tecnica",
        "procedimiento",
        "se realiza",
        "protocolo"
    ],

    "hallazgos": [
        "hallazgos",
        "se observa",
        "evidencia",
        "visualiza",
        "sin alteraciones",
        "alteraciones"
    ],

    "conclusion": [
        "conclusión",
        "conclusion",
        "impresión diagnóstica",
        "impresion diagnostica",
        "diagnóstico",
        "diagnostico"
    ]
}


def limpiar_texto_clinico(texto: str) -> str:

    if not texto:
        return ""

    texto = re.sub(r'\s+', ' ', texto)

    texto = re.sub(r'\.{2,}', '.', texto)

    texto = re.sub(r',\s*,+', ', ', texto)

    basura = [
        "mmm",
        "eeee",
        "este",
        "ajá",
    ]

    for b in basura:

        texto = re.sub(
            r'\b' + re.escape(b) + r'\b',
            '',
            texto,
            flags=re.IGNORECASE
        )

    return texto.strip()


def detectar_seccion(texto: str) -> str:

    texto_lower = texto.lower()

    for seccion, palabras in SECCIONES.items():

        for palabra in palabras:

            if palabra in texto_lower:
                return seccion

    return "hallazgos"


def estructurar_informe(texto: str) -> dict:

    texto = limpiar_texto_clinico(texto)

    partes = re.split(
        r'[.\n]',
        texto
    )

    paciente = []
    estudio = []
    tecnica = []
    hallazgos = []
    conclusion = []

    for parte in partes:

        parte = parte.strip()

        if not parte:
            continue

        seccion = detectar_seccion(parte)

        if seccion == "paciente":

            paciente.append(parte)

        elif seccion == "estudio":

            estudio.append(parte)

        elif seccion == "tecnica":

            tecnica.append(parte)

        elif seccion == "conclusion":

            conclusion.append(parte)

        else:

         hallazgos.append(parte)

    if not tecnica:

        tecnica.append(
            "Estudio realizado según protocolo institucional."
        )

    if not conclusion and hallazgos:

        conclusion.append(
            hallazgos[-1]
        )

    return {

        "paciente": "\n".join(paciente),

        "estudio": "\n".join(estudio),

        "tecnica": "\n".join(tecnica),

        "hallazgos": "\n".join(hallazgos),

        "conclusion": "\n".join(conclusion)
}


def generar_html_clinico(
    estructura: dict
) -> str:

    tecnica = estructura.get(
        "tecnica",
        ""
    )

    hallazgos = estructura.get(
        "hallazgos",
        ""
    )
    paciente = estructura.get(
    "paciente",
    ""
)

    estudio = estructura.get(
        "estudio",
        ""
)

    conclusion = estructura.get(
        "conclusion",
        ""
    )

    html = f"""
    <div style="
        font-family:Arial,sans-serif;
        font-size:14px;
        line-height:1.7;
        color:#0f172a;
    ">
        <h2 style="
    color:#0f4c81;
    border-bottom:2px solid #0ea5ff;
    padding-bottom:4px;
    margin-top:18px;
">
    PACIENTE
</h2>

<p>{paciente}</p>

<h2 style="
    color:#0f4c81;
    border-bottom:2px solid #0ea5ff;
    padding-bottom:4px;
    margin-top:18px;
">
    ESTUDIO
</h2>

<p>{estudio}</p>
        <h2 style="
            color:#0f4c81;
            border-bottom:2px solid #0ea5ff;
            padding-bottom:4px;
            margin-top:18px;
        ">
            TÉCNICA
        </h2>

        <p>{tecnica}</p>

        <h2 style="
            color:#0f4c81;
            border-bottom:2px solid #0ea5ff;
            padding-bottom:4px;
            margin-top:18px;
        ">
            HALLAZGOS
        </h2>

        <p>{hallazgos}</p>

        <h2 style="
            color:#0f4c81;
            border-bottom:2px solid #0ea5ff;
            padding-bottom:4px;
            margin-top:18px;
        ">
            CONCLUSIÓN
        </h2>

        <p><strong>{conclusion}</strong></p>

    </div>
    """

    return html