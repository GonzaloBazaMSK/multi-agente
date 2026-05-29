"""
Mapeo de texto libre de profesión/especialidad → valores de la picklist Zoho CRM.

Zoho tiene picklists estrictas. El usuario escribe texto libre ("soy cardiólogo",
"trabajo en UCI pediátrica") — este módulo lo mapea al valor exacto de la picklist.
Si no matchea nada → devuelve "Otra Especialidad" y guarda el texto en Otra_especialidad.
"""

import unicodedata


# ── Listas de especialidades por grupo de profesión ─────────────────────────

_ESPECIALIDADES_MEDICO = [
    "Anestesiología", "Cardiología", "Cirugía", "Dermatología",
    "Diagnóstico por Imágenes", "Emergentología", "Endocrinología",
    "Gastroenterología", "Generalista", "Geriatría y Gerontología",
    "Ginecología", "Hematología", "Infectología", "Nefrología",
    "Neonatología", "Neurología", "Nutrición", "Obstetricia",
    "Obstetricia y Ginecología", "Oftalmología", "Oncología", "Pediatría",
    "Psiquiatría", "Medicina reproductiva y fertilidad",
    "Traumatología y ortopedia", "Mastología", "Urología",
    "Medicina legal", "Medicina familiar y comunitaria", "Trasplante",
    "Flebología y linfología", "Medicina paliativa y dolor",
    "Medicina física y rehabilitación", "Medicina de la industria farmaceútica",
    "Neumonología", "Medicina intensiva", "Toxicología",
    "Medicina interna / clínica", "Auditoría y administración sanitaria",
    "Medicina del deporte", "Anatomía patológica", "Alergia e inmunología",
    "Hepatología", "Otorrinolaringología", "Reumatología",
    "Medicina del trabajo / ocupacional", "Coloproctología", "Diabetes",
    "Medicina estética", "Otra Especialidad",
]

_ESPECIALIDADES_ENFERMERIA = [
    "Enfermería quirúrgica", "Enfermería familiar y comunitaria",
    "Enfermería en emergencias y atención primaria", "Enfermería neonatal",
    "Enfermería en cuidados intensivos pediátricos y neonatales",
    "Enfermería en cardiología y UCO", "Enfermería en unidades de trasplantes",
    "Enfermería en reproducción asistida", "Enfermería en internación domiciliaria",
    "Enfermería hematológica", "Enfermería en salud mental",
    "Enfermería obstétrica y ginecológica", "Enfermería nefrológica y diálisis",
    "Enfermería oncológica", "Enfermería en internación general",
    "Enfermería en análisis clínicos", "Enfermería en cuidados paliativos y dolor",
    "Enfermería radiológica", "Enfermería en cuidados intensivos de adultos",
    "Enfermería en administración y gestión sanitaria", "Enfermería pediátrica",
    "Enfermería en investigación", "Enfermería escolar",
    "Enfermería en lactancia y puerperio", "Enfermería geriátrica y gerontológica",
    "Otra Especialidad",
]

_ESPECIALIDADES_TECNICO = [
    "Tecnicatura en laboratorio clínico",
    "Tecnicatura en radiología e imágenes diagnósticas",
    "Tecnicatura en atención de adicciones",
    "Tecnicatura en optometría",
    "Tecnicatura en hemoterapia e inmunohematología",
    "Tecnicatura en partería profesional con enfoque intercultural",
    "Tecnicatura en visita médica",
    "Tecnicatura en cuidados geriátricos",
    "Tecnicatura en tecnología en ciencias del esteticismo",
    "Tecnicatura en ciencia y tecnología de alimentos",
    "Tecnicatura en prácticas cardiológicas",
    "Tecnicatura en asistencia dental",
    "Tecnicatura en cosmetología",
    "Terapia ocupacional",
    "Tecnicatura en esterilización",
    "Otra Especialidad",
]

_ESPECIALIDADES_TEC_MEDICA = [
    "Hematología", "Oftalmología", "Otorrinolaringología",
    "Bioanálisis Clínico-molecular", "Medicina Transfusional",
    "Imagenología", "Radioterapia", "Física Médica", "Morfofisiopatología",
    "Citodiagnóstico", "Optometría", "Otra Especialidad",
]

_ESPECIALIDADES_LICENCIADO = [
    "Bioquímica", "Nutrición", "Obstetricia", "Odontología",
    "Producción de bioimágenes", "Farmacia", "Kinesiología y fisiatría",
    "Instrumentación quirúrgica", "Psicología", "Radiología",
    "Terapia ocupacional", "Osteopatía", "Podología", "Óptica",
    "Otra Especialidad",
]

_ESPECIALIDADES_FUERZA = [
    "Bombero", "Policía", "Paramédico", "Guardavidas / Rescatista",
    "Otra Especialidad",
]

# Mapa profesión Zoho → lista de especialidades válidas
ESPECIALIDADES_POR_PROFESION: dict[str, list[str]] = {
    "Personal médico":        _ESPECIALIDADES_MEDICO,
    "Residente":              _ESPECIALIDADES_MEDICO,
    "Estudiante":             _ESPECIALIDADES_MEDICO,
    "Personal de enfermería": _ESPECIALIDADES_ENFERMERIA,
    "Auxiliar de enfermería": _ESPECIALIDADES_ENFERMERIA,
    "Técnico universitario":  _ESPECIALIDADES_TECNICO,
    "Tecnología Médica":      _ESPECIALIDADES_TEC_MEDICA,
    "Licenciado de la salud": _ESPECIALIDADES_LICENCIADO,
    "Fuerza pública":         _ESPECIALIDADES_FUERZA,
    "Otra profesión":         ["Otra Especialidad"],
}

# ── Aliases de keywords → valor exacto de la picklist ───────────────────────
# Para los casos donde el substring match no alcanza.
# Clave: texto a buscar (en la cadena normalizada del usuario).
# Valor: el display_value exacto de Zoho.

_ALIASES_MEDICO: dict[str, str] = {
    "guardia": "Emergentología",
    "urgencias": "Emergentología",
    "emergencia": "Emergentología",
    "clínico": "Medicina interna / clínica",
    "clinico": "Medicina interna / clínica",
    "internista": "Medicina interna / clínica",
    "medicina interna": "Medicina interna / clínica",
    "uci": "Medicina intensiva",
    "uti": "Medicina intensiva",
    "terapia intensiva": "Medicina intensiva",
    "cuidados intensivos": "Medicina intensiva",
    "médico general": "Generalista",
    "medico general": "Generalista",
    "mgp": "Generalista",
    "mgi": "Generalista",
    "medicina general": "Generalista",
    "medicina de familia": "Medicina familiar y comunitaria",
    "médico de cabecera": "Medicina familiar y comunitaria",
    "atención primaria": "Medicina familiar y comunitaria",
    "paliativo": "Medicina paliativa y dolor",
    "paliativos": "Medicina paliativa y dolor",
    "rehab": "Medicina física y rehabilitación",
    "kinesiolog": "Medicina física y rehabilitación",
    "pulmon": "Neumonología",
    "respirator": "Neumonología",
    "pulmón": "Neumonología",
    "hepat": "Hepatología",
    "hígado": "Hepatología",
    "riñón": "Nefrología",
    "renal": "Nefrología",
    "nefrol": "Nefrología",
    "otorrino": "Otorrinolaringología",
    "oído": "Otorrinolaringología",
    "orl": "Otorrinolaringología",
    "reumatol": "Reumatología",
    "artritis": "Reumatología",
    "auditoría": "Auditoría y administración sanitaria",
    "auditoria": "Auditoría y administración sanitaria",
    "auditor ": "Auditoría y administración sanitaria",
    "laboratorio": "Auditoría y administración sanitaria",
    "colon": "Coloproctología",
    "recto": "Coloproctología",
    "proctol": "Coloproctología",
    "flebo": "Flebología y linfología",
    "várices": "Flebología y linfología",
    "venas": "Flebología y linfología",
    "masto": "Mastología",
    "mama": "Mastología",
    "forense": "Medicina legal",
    "legal": "Medicina legal",
    "trasplant": "Trasplante",
    "alergi": "Alergia e inmunología",
    "inmunol": "Alergia e inmunología",
    "estetic": "Medicina estética",
    "cosmetic": "Medicina estética",
    "deporte": "Medicina del deporte",
    "sport": "Medicina del deporte",
    "toxicol": "Toxicología",
    "farmac": "Medicina de la industria farmaceútica",
    "laboral": "Medicina del trabajo / ocupacional",
    "trabajo": "Medicina del trabajo / ocupacional",
    "ocupacional": "Medicina del trabajo / ocupacional",
    "anatomia patol": "Anatomía patológica",
    "patolog": "Anatomía patológica",
    "diabet": "Diabetes",
    "endoc": "Endocrinología",
    "tiroides": "Endocrinología",
    "neonat": "Neonatología",
    "infectol": "Infectología",
    "infecciosas": "Infectología",
    "hematol": "Hematología",
    "gastro": "Gastroenterología",
    "digestivo": "Gastroenterología",
    "gineol": "Ginecología",
    "ginecol": "Ginecología",
    "obstetr": "Obstetricia",
    "maternidad": "Obstetricia",
    "partos": "Obstetricia",
    "neurol": "Neurología",
    "cerebro": "Neurología",
    "neuroci": "Neurología",
    "dermato": "Dermatología",
    "piel": "Dermatología",
    "oncol": "Oncología",
    "cancer": "Oncología",
    "cáncer": "Oncología",
    "tumor": "Oncología",
    "oftalm": "Oftalmología",
    "ojo": "Oftalmología",
    "ojos": "Oftalmología",
    "psiquiatr": "Psiquiatría",
    "salud mental": "Psiquiatría",
    "traumatol": "Traumatología y ortopedia",
    "ortopedia": "Traumatología y ortopedia",
    "ortopedista": "Traumatología y ortopedia",
    "traumatolog": "Traumatología y ortopedia",
    "huesos": "Traumatología y ortopedia",
    "urol": "Urología",
    "próstata": "Urología",
    "cardiol": "Cardiología",
    "corazón": "Cardiología",
    "pediatr": "Pediatría",
    "niños": "Pediatría",
    "infanto": "Pediatría",
    "reproduc": "Medicina reproductiva y fertilidad",
    "fertilid": "Medicina reproductiva y fertilidad",
    "cirugía": "Cirugía",
    "cirugia": "Cirugía",
    "cirujano": "Cirugía",
    "anestes": "Anestesiología",
    "geriátr": "Geriatría y Gerontología",
    "geriatri": "Geriatría y Gerontología",
    "gerontol": "Geriatría y Gerontología",
    "imágenes": "Diagnóstico por Imágenes",
    "imagenes": "Diagnóstico por Imágenes",
    "radiolog": "Diagnóstico por Imágenes",
    "nutrici": "Nutrición",
}

_ALIASES_ENFERMERIA: dict[str, str] = {
    # UCI pediátrica ANTES del genérico "uci" para evitar falso match
    "ucip": "Enfermería en cuidados intensivos pediátricos y neonatales",
    "utin": "Enfermería en cuidados intensivos pediátricos y neonatales",
    "uci pediatr": "Enfermería en cuidados intensivos pediátricos y neonatales",
    "uci neonat": "Enfermería en cuidados intensivos pediátricos y neonatales",
    "intensivos pediatr": "Enfermería en cuidados intensivos pediátricos y neonatales",
    "cuidados intensivos pediatr": "Enfermería en cuidados intensivos pediátricos y neonatales",
    "uci": "Enfermería en cuidados intensivos de adultos",
    "uti": "Enfermería en cuidados intensivos de adultos",
    "terapia intensiva adultos": "Enfermería en cuidados intensivos de adultos",
    "cuidados intensivos adultos": "Enfermería en cuidados intensivos de adultos",
    "neonat": "Enfermería neonatal",
    "pediátr": "Enfermería pediátrica",
    "pediatr": "Enfermería pediátrica",
    "niños": "Enfermería pediátrica",
    "quirúrg": "Enfermería quirúrgica",
    "quirurg": "Enfermería quirúrgica",
    "quirófano": "Enfermería quirúrgica",
    "block quirúrgico": "Enfermería quirúrgica",
    "emergenci": "Enfermería en emergencias y atención primaria",
    "urgenci": "Enfermería en emergencias y atención primaria",
    "atención primaria": "Enfermería familiar y comunitaria",
    "familiar": "Enfermería familiar y comunitaria",
    "comunitaria": "Enfermería familiar y comunitaria",
    "oncol": "Enfermería oncológica",
    "cáncer": "Enfermería oncológica",
    "hematol": "Enfermería hematológica",
    "nefrol": "Enfermería nefrológica y diálisis",
    "diálisis": "Enfermería nefrológica y diálisis",
    "dialisis": "Enfermería nefrológica y diálisis",
    "salud mental": "Enfermería en salud mental",
    "psiquiatr": "Enfermería en salud mental",
    "paliativ": "Enfermería en cuidados paliativos y dolor",
    "dolor": "Enfermería en cuidados paliativos y dolor",
    "maternidad": "Enfermería obstétrica y ginecológica",
    "obstetr": "Enfermería obstétrica y ginecológica",
    "ginecol": "Enfermería obstétrica y ginecológica",
    "partos": "Enfermería obstétrica y ginecológica",
    "trasplant": "Enfermería en unidades de trasplantes",
    "reproducci": "Enfermería en reproducción asistida",
    "fertilid": "Enfermería en reproducción asistida",
    "domicili": "Enfermería en internación domiciliaria",
    "cardiol": "Enfermería en cardiología y UCO",
    "uco": "Enfermería en cardiología y UCO",
    "ucc": "Enfermería en cardiología y UCO",
    "radiolog": "Enfermería radiológica",
    "imágenes": "Enfermería radiológica",
    "laboratorio": "Enfermería en análisis clínicos",
    "análisis": "Enfermería en análisis clínicos",
    "analisis": "Enfermería en análisis clínicos",
    "geriátr": "Enfermería geriátrica y gerontológica",
    "geriatri": "Enfermería geriátrica y gerontológica",
    "gerontol": "Enfermería geriátrica y gerontológica",
    "mayores": "Enfermería geriátrica y gerontológica",
    "lactancia": "Enfermería en lactancia y puerperio",
    "puerperio": "Enfermería en lactancia y puerperio",
    "escolar": "Enfermería escolar",
    "administraci": "Enfermería en administración y gestión sanitaria",
    "gestión": "Enfermería en administración y gestión sanitaria",
    "gestion": "Enfermería en administración y gestión sanitaria",
    "investigaci": "Enfermería en investigación",
    "general": "Enfermería en internación general",
    "clínica": "Enfermería en internación general",
    "clinica": "Enfermería en internación general",
    "internación": "Enfermería en internación general",
}

_ALIASES_TECNICO: dict[str, str] = {
    "laboratorio": "Tecnicatura en laboratorio clínico",
    "lab clínico": "Tecnicatura en laboratorio clínico",
    "radiolog": "Tecnicatura en radiología e imágenes diagnósticas",
    "imágenes": "Tecnicatura en radiología e imágenes diagnósticas",
    "adiccion": "Tecnicatura en atención de adicciones",
    "optometr": "Tecnicatura en optometría",
    "hemoterapia": "Tecnicatura en hemoterapia e inmunohematología",
    "inmunohematolog": "Tecnicatura en hemoterapia e inmunohematología",
    "partería": "Tecnicatura en partería profesional con enfoque intercultural",
    "visita médica": "Tecnicatura en visita médica",
    "visitador": "Tecnicatura en visita médica",
    "geriátr": "Tecnicatura en cuidados geriátricos",
    "mayores": "Tecnicatura en cuidados geriátricos",
    "estetic": "Tecnicatura en tecnología en ciencias del esteticismo",
    "cosmetic": "Tecnicatura en cosmetología",
    "cosmetolog": "Tecnicatura en cosmetología",
    "alimentos": "Tecnicatura en ciencia y tecnología de alimentos",
    "nutrici": "Tecnicatura en ciencia y tecnología de alimentos",
    "cardiol": "Tecnicatura en prácticas cardiológicas",
    "dental": "Tecnicatura en asistencia dental",
    "odontolog": "Tecnicatura en asistencia dental",
    "esterilizaci": "Tecnicatura en esterilización",
    "terapia ocupacional": "Terapia ocupacional",
}

_ALIASES_TEC_MEDICA: dict[str, str] = {
    "hematol": "Hematología",
    "sangre": "Hematología",
    "oftalm": "Oftalmología",
    "ojo": "Oftalmología",
    "otorrino": "Otorrinolaringología",
    "orl": "Otorrinolaringología",
    "bioanálisis": "Bioanálisis Clínico-molecular",
    "bioanalisis": "Bioanálisis Clínico-molecular",
    "transfusi": "Medicina Transfusional",
    "hemoterapia": "Medicina Transfusional",
    "imagenolog": "Imagenología",
    "imágenes": "Imagenología",
    "radiolog": "Imagenología",
    "radioterapia": "Radioterapia",
    "física médica": "Física Médica",
    "fisica medica": "Física Médica",
    "morfofisio": "Morfofisiopatología",
    "citodiag": "Citodiagnóstico",
    "citolog": "Citodiagnóstico",
    "optometr": "Optometría",
}

_ALIASES_LICENCIADO: dict[str, str] = {
    "bioquím": "Bioquímica",
    "bioquim": "Bioquímica",
    "nutrici": "Nutrición",
    "nutricion": "Nutrición",
    "nutricionista": "Nutrición",
    "obstetr": "Obstetricia",
    "partera": "Obstetricia",
    "partos": "Obstetricia",
    "odontolog": "Odontología",
    "dentista": "Odontología",
    "bioimágen": "Producción de bioimágenes",
    "bioimagen": "Producción de bioimágenes",
    "farmac": "Farmacia",
    "kinesiol": "Kinesiología y fisiatría",
    "kinesiolog": "Kinesiología y fisiatría",
    "fisiatra": "Kinesiología y fisiatría",
    "fisioterapia": "Kinesiología y fisiatría",
    "fisioterapeuta": "Kinesiología y fisiatría",
    "instrumentaci": "Instrumentación quirúrgica",
    "psicolog": "Psicología",
    "psicologa": "Psicología",
    "radiolog": "Radiología",
    "terapia ocupacional": "Terapia ocupacional",
    "terapista ocupacional": "Terapia ocupacional",
    "osteop": "Osteopatía",
    "podolog": "Podología",
    "podólogo": "Podología",
    "pies": "Podología",
    "óptic": "Óptica",
    "optic": "Óptica",
    "optomet": "Óptica",
}

_ALIASES_FUERZA: dict[str, str] = {
    "bombero": "Bombero",
    "policía": "Policía",
    "policia": "Policía",
    "gendarm": "Policía",
    "paramédico": "Paramédico",
    "paramedico": "Paramédico",
    "emergenci": "Paramédico",
    "guardavida": "Guardavidas / Rescatista",
    "rescatista": "Guardavidas / Rescatista",
    "salvaví": "Guardavidas / Rescatista",
}

_ALIASES_POR_PROFESION: dict[str, dict[str, str]] = {
    "Personal médico":        _ALIASES_MEDICO,
    "Residente":              _ALIASES_MEDICO,
    "Estudiante":             _ALIASES_MEDICO,
    "Personal de enfermería": _ALIASES_ENFERMERIA,
    "Auxiliar de enfermería": _ALIASES_ENFERMERIA,
    "Técnico universitario":  _ALIASES_TECNICO,
    "Tecnología Médica":      _ALIASES_TEC_MEDICA,
    "Licenciado de la salud": _ALIASES_LICENCIADO,
    "Fuerza pública":         _ALIASES_FUERZA,
    "Otra profesión":         {},
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    """Lowercase + strip accents para matching robusto."""
    return "".join(
        c for c in unicodedata.normalize("NFD", (s or "").lower().strip())
        if unicodedata.category(c) != "Mn"
    )


def map_especialidad(text: str, profesion_zoho: str) -> tuple[str, str]:
    """
    Mapea texto libre de especialidad al valor de la picklist Zoho.

    Returns:
        (especialidad_picklist, otra_especialidad_text)
        - especialidad_picklist: valor exacto de la picklist Zoho
        - otra_especialidad_text: texto original si no matcheó (va a Otra_especialidad)
    """
    text = (text or "").strip()
    if not text:
        return "", ""

    options = ESPECIALIDADES_POR_PROFESION.get(profesion_zoho, ["Otra Especialidad"])
    aliases = _ALIASES_POR_PROFESION.get(profesion_zoho, {})
    text_norm = _norm(text)

    # 1. Aliases explícitos (mayor prioridad — mapean jerga / abreviaturas)
    for alias, value in aliases.items():
        if alias in text_norm:
            return value, ""

    # 2. Match exacto contra la picklist (normalizado)
    for opt in options:
        if opt == "Otra Especialidad":
            continue
        if _norm(opt) == text_norm:
            return opt, ""

    # 3. Substring: el valor de la picklist aparece en el texto del usuario
    for opt in options:
        if opt == "Otra Especialidad":
            continue
        if _norm(opt) in text_norm:
            return opt, ""

    # 4. Substring inverso: el texto del usuario (si es corto) aparece en la picklist
    if len(text_norm) > 4:
        for opt in options:
            if opt == "Otra Especialidad":
                continue
            if text_norm in _norm(opt):
                return opt, ""

    # 5. Sin match → Otra Especialidad + guardar texto original
    return "Otra Especialidad", text
