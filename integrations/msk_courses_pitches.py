"""
Generacion de pitch_hook + pitch_by_profile a NIVEL SLUG.

El kb_ai de cada curso es el MISMO entre paises (viene del producto padre del
WP), asi que el pitch tambien es el mismo. Una llamada al LLM por slug, y
replica el resultado a TODAS las filas (country, slug) en la DB.

Comparte el prompt y la logica con `scripts/gen_pitch_hooks.py` — el script se
mantiene para uso manual desde CLI, y el endpoint admin-ui usa la funcion
`generate_pitches()` de aca.
"""

from __future__ import annotations

import json
import os
from typing import Awaitable, Callable, Optional

import asyncpg
import structlog
from openai import AsyncOpenAI

logger = structlog.get_logger(__name__)


CLAVES_CANONICAS_DOC = """
8 CLAVES CANONICAS (usalas EXACTAMENTE, no inventes otras):

1. `medico` — Personal medico + Cargo operativo o sin Cargo. Medico general,
   clinico, especialista en ejercicio. Registro: clinico profesional.

2. `medico_jefe` — Personal medico + Cargo Direccion/Gerencia/Coordinacion/
   Jefatura/Supervision. Registro: lenguaje de pares, supervision.

3. `residente` — Profesion = "Residente". Registro: accesible pero tecnico,
   foco en consolidar bases.

4. `estudiante` — Profesion = "Estudiante". Registro: didactico.

5. `enfermeria` — Personal de enfermeria o Auxiliar. Registro: respeto
   profesional del rol, lenguaje del cuidado, NO lenguaje medico.

6. `tecnico_salud` — Tecnico universitario o Tecnologia Medica (imagenes, lab,
   radioterapia, etc). Registro: tecnico especifico del area.

7. `licenciado_salud` — Licenciado de la salud (kinesio, nutricion, psico,
   fonoaudio, terapista ocupacional, farmaceutico, bioquimico, obstetra).
   Registro: tecnico del area + abordaje interdisciplinario.

8. `otros` — Fuerza publica, Otra profesion. Registro: profesional generico.
"""

SYSTEM_PROMPT = f"""Eres un copy writer medico especializado en marketing de cursos de formacion profesional. Te paso el brief COMPLETO de un curso y debes generar DOS piezas de copy para que un agente vendedor las use.

Devolve un JSON con EXACTAMENTE esta estructura:
{{{{
  "pitch_hook": "<string de 250-500 caracteres>",
  "pitch_by_profile": {{{{
    "medico": "<string 180-320 chars>",
    "medico_jefe": "<string 180-320 chars>",
    "residente": "<string 180-320 chars>",
    "estudiante": "<string 180-320 chars>",
    "enfermeria": "<string 180-320 chars>",
    "tecnico_salud": "<string 180-320 chars>",
    "licenciado_salud": "<string 180-320 chars>",
    "otros": "<string 180-320 chars>"
  }}}}
}}}}

================================================================================
CAMPO 1: pitch_hook (250-500 chars)
================================================================================

Es el gancho que aparece cuando el curso se muestra en un LISTADO del catalogo.

Estructura obligatoria (2-3 oraciones):

1. Que vas a DOMINAR — verbo de accion fuerte 2da persona TUTEO NEUTRO (Recorres /
   Dominas / Decides / Manejas / Interpretas / Diagnosticas / Resuelves /
   Consolidas / Integras) + 3-6 ejes tematicos ESPECIFICOS del curso.
   NO uses formas voseantes (Decidís, Resolvés, Dominás, Consolidás, Integrás).

2. Diferencial editorial / metodologico — si el kb_ai menciona N de especialistas,
   hospitales universitarios, evidencia fechada, INCLUILO. Si no lo tiene, saltala.

3. Rango de perfiles — frase de "Te sirve desde X hasta Y".

PROHIBIDO:
- "curso", "este curso", "el curso", "programa", "recorrido formativo",
  "experiencia formativa", "itinerario"
- aval, certificacion, diploma, MSK, universidad
- horas, modulos, duracion, modalidad online, tutorias
- precio, pagos, cuotas (cualquier referencia a costo)
- palabras vacias: confianza, integral, completo, moderno, excelente,
  calidad, premium, innovador, vanguardia, enfoque integral
- promesas vagas tipo "mejoraras tu practica"

================================================================================
CAMPO 2: pitch_by_profile — GENERA LAS 8 CLAVES SIEMPRE
================================================================================

{CLAVES_CANONICAS_DOC}

Genera las 8 claves SIEMPRE. La idea es que cualquier usuario que entre — sea
cual sea su profesion — encuentre un angulo por el cual el curso le suma. MSK
vende a todo profesional de la salud; no restringimos la venta a los
perfiles_dirigidos del kb_ai.

Para perfiles no listados en perfiles_dirigidos, pensa un ANGULO LATERAL real:

- enfermeria en curso clinico medico: entendes mejor los cuadros para cuidar
  con criterio y anticipar complicaciones.
- tecnico_salud: correlacionas mejor los estudios que realizas con el contexto
  clinico, mejoras comunicacion con el medico.
- licenciado_salud: ganas el marco clinico para integrar tu practica
  (nutricional/kinesica/psicologica/farmaceutica) en el plan terapeutico global.
- estudiante: llegas a la rotacion o residencia con bases solidas.
- otros: entendes mejor el cuadro y coordinas mejor con el equipo medico.

REGLAS DE REDACCION (cada string del pitch_by_profile):
- Verbo de accion en 2da persona presente TUTEO NEUTRO (tu tienes, tu puedes, tu consolidas, tu dominas).
- 180-320 caracteres.
- Especifico al perfil: nombrar decisiones, tecnicas o patologias concretas
  desde SU rol.
- enfermeria/tecnico/licenciado: NO les hables como si diagnosticaran o
  prescribieran. Hablales desde SU rol.

TONO (CRITICO):
- ESPAÑOL NEUTRO PROFESIONAL. PROHIBIDO EL VOSEO (vos, tenes, podes, sos, dale, che, mira, contame).
  Los usuarios son medicos de todo el mundo hispano (LATAM + España), no solo Argentina.
- Forma correcta: "dominas, consolidas, refuerzas, integras, puedes, tienes, eres".
  Forma PROHIBIDA: "dominás, consolidás, reforzás, integrás, podés, tenés, sos".
- Sin emojis, sin signos de admiracion, sin comillas decorativas.

DEVUELVE SOLO EL JSON. NADA MAS."""


async def _generate_for_brief(client: AsyncOpenAI, title: str, brief: str) -> dict:
    brief_short = brief[:7000]
    user_msg = f"Curso: {title}\n\nBRIEF COMPLETO:\n\n{brief_short}"
    resp = await client.chat.completions.create(
        model="gpt-4o",
        temperature=0.4,
        max_tokens=2000,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
    )
    return json.loads(resp.choices[0].message.content)


# on_progress recibe un dict con {current, total, slug, status, ok, err}
# y debe ser awaitable (para poder persistir en Redis async).
ProgressCb = Callable[[dict], Awaitable[None]]


async def generate_pitches(
    *,
    force: bool = False,
    only_slugs: Optional[list[str]] = None,
    on_progress: Optional[ProgressCb] = None,
) -> dict:
    """
    Genera pitch_hook + pitch_by_profile para los slugs que tienen kb_ai.

    force=False (default): solo slugs donde pitch_hook IS NULL.
    force=True: regenera TODOS los slugs con kb_ai (pisa los existentes).
    only_slugs: lista opcional para limitar a slugs puntuales.

    Actualiza todas las filas (country, slug) con el mismo pitch, porque el
    kb_ai es el mismo entre paises (no depende del pais).

    Retorna dict con conteo total: {total, ok, err, rows_updated}.
    """
    client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    conn = await asyncpg.connect(os.environ["DATABASE_URL"], statement_cache_size=0)

    try:
        # 1) Encontrar slugs UNICOS con kb_ai (DISTINCT ON por slug — toma una fila
        #    de cualquier pais, el kb_ai y el brief_md son iguales entre paises).
        where_pitch = "" if force else " and pitch_hook is null"
        where_slug_filter = ""
        params: list = []
        if only_slugs:
            where_slug_filter = " and slug = any($1::text[])"
            params.append(only_slugs)

        sql = f"""
            select distinct on (slug) slug, title, brief_md
            from public.courses
            where (raw->'kb_ai') is not null{where_pitch}{where_slug_filter}
            order by slug, country
        """
        rows = await conn.fetch(sql, *params)

        total = len(rows)
        ok = 0
        err = 0
        total_rows_updated = 0

        if on_progress:
            await on_progress({
                "phase": "start",
                "total": total,
                "current": 0,
                "ok": 0,
                "err": 0,
            })

        for i, r in enumerate(rows, 1):
            slug = r["slug"]
            title = r["title"] or slug
            brief = r["brief_md"] or ""

            if not brief.strip():
                err += 1
                logger.warning("pitch_skip_no_brief", slug=slug)
                if on_progress:
                    await on_progress({
                        "phase": "progress",
                        "total": total,
                        "current": i,
                        "slug": slug,
                        "status": "skip",
                        "ok": ok,
                        "err": err,
                    })
                continue

            try:
                data = await _generate_for_brief(client, title, brief)
                hook = (data.get("pitch_hook") or "").strip()
                by_prof = data.get("pitch_by_profile") or {}

                result = await conn.execute(
                    "update public.courses set pitch_hook=$1, pitch_by_profile=$2 where slug=$3",
                    hook, json.dumps(by_prof, ensure_ascii=False), slug,
                )
                try:
                    n_updated = int(result.split()[-1])
                except Exception:
                    n_updated = 0
                total_rows_updated += n_updated
                ok += 1
                logger.info(
                    "pitch_generated",
                    slug=slug, rows=n_updated, hook_chars=len(hook),
                    profiles=len(by_prof),
                )
                if on_progress:
                    await on_progress({
                        "phase": "progress",
                        "total": total,
                        "current": i,
                        "slug": slug,
                        "status": "ok",
                        "hook_chars": len(hook),
                        "profiles": len(by_prof),
                        "rows": n_updated,
                        "ok": ok,
                        "err": err,
                    })
            except Exception as e:
                err += 1
                logger.exception("pitch_failed", slug=slug)
                if on_progress:
                    await on_progress({
                        "phase": "progress",
                        "total": total,
                        "current": i,
                        "slug": slug,
                        "status": "err",
                        "error": str(e),
                        "ok": ok,
                        "err": err,
                    })

        # Invalidar cache Redis de TODOS los paises — los hooks pueden afectar 17 paises.
        try:
            from integrations import courses_cache

            countries = await conn.fetch("select distinct country from public.courses")
            for c in countries:
                await courses_cache.invalidate_country(c["country"])
        except Exception as e:
            logger.warning("pitch_cache_invalidate_failed", error=str(e))

        return {
            "total": total,
            "ok": ok,
            "err": err,
            "rows_updated": total_rows_updated,
        }
    finally:
        await conn.close()
