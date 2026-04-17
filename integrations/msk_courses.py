"""
Sincronización del catálogo de cursos desde el WP headless de MSK Latam.

Endpoint público:
    https://cms1.msklatam.com/wp-json/msk/v1/products-full?lang={lang}&resource=course

El JSON incluye todos los cursos de un país. Extraemos:
  - Hot columns (precio, título, cedente, etc.) para filtrar rápido
  - raw (JSONB) para drill-down on-demand
  - brief_md (Markdown ~800-1200 tokens) para inyectar en el prompt del
    agente de ventas cuando el usuario está viendo un curso.

El brief_md se arma desde `sections` y `kb_ai` — los perfiles_dirigidos son
la GOLD MINE para ventas: dan dolor del cliente + beneficio del curso por
perfil profesional.

Testimonios (sections.reviews) se EXCLUYEN intencionalmente del brief.
"""
from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any, Optional

import httpx
import structlog

from memory import postgres_store

logger = structlog.get_logger(__name__)


# ── Mapeo país ISO-2 → lang del WP ──────────────────────────────────────────
LANG_BY_COUNTRY: dict[str, str] = {
    "ar": "arg",
    "bo": "bol",
    "cl": "chi",
    "co": "col",
    "cr": "cos",
    "ec": "ecu",
    "es": "esp",
    "gt": "gua",
    "hn": "hon",
    "mx": "mex",
    "ni": "nic",
    "pa": "pan",
    "py": "par",
    "pe": "per",
    "sv": "sal",
    "uy": "uru",
    "ve": "ven",
}

COUNTRY_LABEL: dict[str, str] = {
    "ar": "Argentina", "bo": "Bolivia", "cl": "Chile", "co": "Colombia",
    "cr": "Costa Rica", "ec": "Ecuador", "es": "España", "gt": "Guatemala",
    "hn": "Honduras", "mx": "México", "ni": "Nicaragua", "pa": "Panamá",
    "py": "Paraguay", "pe": "Perú", "sv": "El Salvador", "uy": "Uruguay",
    "ve": "Venezuela",
}

BASE_URL = "https://cms1.msklatam.com/wp-json/msk/v1/products-full"


# ── Helpers de limpieza ─────────────────────────────────────────────────────

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")
_NL_RE = re.compile(r"\n{3,}")


def html_to_text(s: Any) -> str:
    """Limpia HTML → texto plano. Preserva saltos para <p>/<br>/<li>."""
    if not s:
        return ""
    if not isinstance(s, str):
        s = str(s)
    s = s.replace("</p>", "\n").replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    s = s.replace("</li>", "\n").replace("<li>", "• ")
    s = _TAG_RE.sub("", s)
    s = html.unescape(s)
    s = _WS_RE.sub(" ", s)
    s = _NL_RE.sub("\n\n", s)
    return s.strip()


def _to_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int(v: Any) -> Optional[int]:
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _primary_category(item: dict) -> Optional[str]:
    cats = (item.get("sections", {}) or {}).get("header", {}).get("categories", []) or []
    primary = next((c for c in cats if c.get("is_primary")), None)
    if primary:
        return primary.get("name")
    return cats[0].get("name") if cats else None


# ── Fetch ───────────────────────────────────────────────────────────────────

async def fetch_country(country: str, timeout: float = 60.0) -> list[dict]:
    """Descarga todos los productos `resource=course` de un país."""
    lang = LANG_BY_COUNTRY.get(country.lower())
    if not lang:
        raise ValueError(f"Unknown country: {country}")

    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.get(BASE_URL, params={"lang": lang, "resource": "course"})
        r.raise_for_status()
        data = r.json()

    # Algunos WP devuelven {"data": [...]} o directamente [...]
    if isinstance(data, dict):
        data = data.get("data") or data.get("products") or data.get("items") or []
    if not isinstance(data, list):
        logger.warning("msk_courses_unexpected_format", country=country, type=type(data).__name__)
        return []

    # El query param `resource=course` ya filtra del lado del WP. Acá solo
    # validamos que venga con slug+title (el campo `resource` en el payload
    # es inconsistente entre versiones del plugin MSK-API).
    courses = [d for d in data if d.get("slug") and d.get("title")]
    resources_seen = set(d.get("resource") for d in data if d.get("resource") is not None)
    logger.info(
        "msk_courses_fetched",
        country=country,
        total=len(data),
        courses=len(courses),
        resources_seen=list(resources_seen)[:5],
    )
    return courses


# ── Brief Markdown (SIN testimonios) ────────────────────────────────────────

def build_brief_md(item: dict, country: str) -> str:
    """
    Arma un Markdown compacto orientado a VENDER, no a informar.
    Pensado para inyectar en el system-prompt del agente de ventas.

    Estructura (orden = prioridad de lectura del LLM):
      1. Header: título, cedente, precio en cuotas, slug
      2. Datos técnicos (kb_ai.datos_tecnicos): modalidad, evaluación, acceso
      3. Perfiles objetivo con dolor/beneficio (kb_ai.perfiles_dirigidos)
      4. Descripción y problemática (kb_ai.descripcion_y_problematica)
      5. Objetivos de aprendizaje (kb_ai.objetivos_de_aprendizaje)
      6. Habilidades (sections.habilities)
      7. Módulos: solo títulos (drill-down con tool get_course_deep)
      8. Certificaciones reales del curso
      9. Equipo docente (top 5)

    Fallbacks para cursos SIN kb_ai:
      - Descripción: sections.content.content (WP genérico)
      - A quién está dirigido: sections.formacion_dirigida
      - Qué vas a aprender: sections.learning

    NO incluye: testimonios, "qué incluye" (redundante con datos técnicos).
    """
    lines: list[str] = []

    title = item.get("title") or "(sin título)"
    slug = item.get("slug") or ""
    cedente = (item.get("cedente") or {}).get("title") or (item.get("cedente") or {}).get("name") or ""

    lines.append(f"# {title}")
    if cedente:
        lines.append(f"**Cedente:** {cedente}")
    lines.append(f"**País:** {COUNTRY_LABEL.get(country.lower(), country.upper())}  ·  **Slug:** `{slug}`")

    # Precio — REGLA DE MARKETING: siempre hablamos en CUOTAS, nunca total.
    prices = item.get("prices") or {}
    currency = prices.get("currency") or ""
    total = _to_float(prices.get("total_price")) or _to_float(prices.get("regular_price"))
    max_inst = _to_int(prices.get("max_installments"))
    inst_val = _to_float(prices.get("price_installments"))
    if max_inst and inst_val:
        lines.append(f"**Precio (comunicar SIEMPRE en cuotas):** {max_inst} cuotas de {currency} {inst_val:,.2f}")
    elif total:
        lines.append(f"**Precio:** {currency} {total:,.0f} (pago único — no hay cuotas disponibles)")

    # Línea compacta: duración + módulos + categoría
    duration = item.get("duration")
    modules_count = item.get("modules")
    categoria = _primary_category(item) or ""
    tech_bits = []
    if duration:
        tech_bits.append(f"{duration} horas")
    if modules_count:
        tech_bits.append(f"{modules_count} módulos")
    if categoria:
        tech_bits.append(f"Categoría: {categoria}")
    if tech_bits:
        lines.append("**Datos:** " + "  ·  ".join(tech_bits))

    lines.append("")

    # ── Datos técnicos del kb_ai (modalidad, evaluación, acceso, materiales)
    kb_ai = item.get("kb_ai") or {}
    datos_tec_html = kb_ai.get("datos_tecnicos") or ""
    if datos_tec_html:
        lines.append("## Datos técnicos")
        # Parsear tabla HTML <tr><td>key</td><td>val</td></tr>
        import re as _re
        rows_html = _re.findall(
            r"<tr[^>]*>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>\s*</tr>",
            datos_tec_html,
            _re.DOTALL | _re.IGNORECASE,
        )
        if rows_html:
            for key_html, val_html in rows_html:
                key = html_to_text(key_html).strip()
                val = html_to_text(val_html).strip()
                if key and val:
                    # Omitir duración (ya está en el header)
                    if key.lower().startswith("duraci"):
                        continue
                    lines.append(f"- **{key}:** {val}")
        else:
            lines.append(html_to_text(datos_tec_html))
        lines.append("")

    # ── Perfiles objetivo (pain + gain) — GOLD para ventas
    perfiles = kb_ai.get("perfiles_dirigidos") or []
    if perfiles:
        lines.append("## Perfiles objetivo — dolor y beneficio (usalo para vender)")
        for p in perfiles:
            perfil = (p.get("perfil") or "").strip()
            problema = html_to_text(p.get("problema_actual__necesidad") or p.get("problema_actual_necesidad") or "")
            obtiene = html_to_text(p.get("que_obtiene") or "")
            if perfil:
                lines.append(f"### {perfil}")
            if problema:
                lines.append(f"- **Dolor / necesidad:** {problema}")
            if obtiene:
                lines.append(f"- **Qué obtiene con este curso:** {obtiene}")
            lines.append("")

    # ── Descripción y problemática (kb_ai prioritario, WP como fallback)
    desc = html_to_text(kb_ai.get("descripcion_y_problematica") or "")
    if not desc:
        desc = html_to_text(
            (item.get("sections", {}) or {}).get("content", {}).get("content", "")
        )
    if desc:
        lines.append("## De qué trata el curso")
        lines.append(desc)
        lines.append("")

    # ── Objetivos de aprendizaje (kb_ai)
    objetivos = html_to_text(kb_ai.get("objetivos_de_aprendizaje") or "")
    if objetivos:
        lines.append("## Objetivos de aprendizaje")
        if len(objetivos) > 1500:
            objetivos = objetivos[:1500].rsplit(" ", 1)[0] + "…"
        lines.append(objetivos)
        lines.append("")

    # ── Habilidades
    habs = (item.get("sections") or {}).get("habilities") or []
    if habs:
        names = [h.get("name", "") for h in habs if h.get("name")]
        if names:
            lines.append("## Habilidades que desarrolla")
            lines.append(", ".join(names))
            lines.append("")

    # ── Fallbacks para cursos SIN kb_ai
    has_kb = bool(kb_ai and (perfiles or objetivos))
    if not has_kb:
        # A quién está dirigido (WP genérico — solo sin kb_ai)
        dirigida = (item.get("sections") or {}).get("formacion_dirigida") or []
        if dirigida:
            lines.append("## A quién está dirigido")
            for d in dirigida:
                step = html_to_text(d.get("step", "")) if isinstance(d, dict) else html_to_text(str(d))
                if step:
                    lines.append(f"- {step}")
            lines.append("")

        # Qué vas a aprender (WP genérico — solo sin kb_ai)
        learning = (item.get("sections") or {}).get("learning") or []
        if learning:
            lines.append("## Qué vas a aprender")
            for l in learning:
                txt = html_to_text(l.get("msk_learning_content", "")) if isinstance(l, dict) else html_to_text(str(l))
                if txt:
                    lines.append(f"- {txt}")
            lines.append("")

    # ── Módulos (solo títulos — contenido detallado via tool get_course_deep)
    study_plan = (item.get("sections") or {}).get("study_plan") or {}
    modules = study_plan.get("modules") or []
    if modules:
        lines.append("## Plan de estudios")
        for i, m in enumerate(modules, 1):
            mtitle = (m.get("title") or "").strip()
            lines.append(f"**Módulo {i} — {mtitle}**")
        # URL del temario PDF si existe
        spf = study_plan.get("study_plan_file") or ""
        if spf:
            lines.append(f"\n📄 [Descargar temario completo (PDF)]({spf})")
        lines.append("")

    # ── Instituciones avalantes / Certificaciones
    # El WP publica avales en DOS campos que pueden estar poblados o vacíos
    # según la versión del plugin:
    #   - `sections.institutions` → lista con {title, description}
    #   - `certificacion_relacionada` → lista con {title, total_price, currency}
    # Unificamos ambas fuentes y clasificamos por heurística en 3 grupos:
    #   1) Aval principal (UDIMA / internacional)          → aplica a TODOS
    #   2) Jurisdiccionales AR (colegios/consejos médicos) → solo si está matriculado
    #   3) Otros países / convenios locales                → informativo
    insts_src = (item.get("sections") or {}).get("institutions") or []
    certs_src = item.get("certificacion_relacionada") or []

    # Normalizamos ambas fuentes a {title, description, price_str} y
    # deduplicamos por título normalizado.
    merged: list[dict] = []
    seen_titles: set[str] = set()

    def _norm_title(s: str) -> str:
        return re.sub(r"\s+", " ", (s or "").strip().lower())

    for inst in insts_src:
        if not isinstance(inst, dict):
            continue
        t = html.unescape(inst.get("title", "") or "")
        key = _norm_title(t)
        if not t or key in seen_titles:
            continue
        seen_titles.add(key)
        merged.append({
            "title": t,
            "description": html_to_text(inst.get("description", "")),
            "price_str": "",
        })

    for c in certs_src:
        if not isinstance(c, dict):
            continue
        t = html.unescape(c.get("title", "") or "")
        key = _norm_title(t)
        if not t or key in seen_titles:
            continue
        seen_titles.add(key)
        price_str = ""
        p = _to_float(c.get("total_price")) or _to_float(c.get("regular_price"))
        if p and p > 0:
            price_str = f"{c.get('currency', '')} {p:,.0f}".strip()
        merged.append({
            "title": t,
            "description": "",
            "price_str": price_str,
        })

    if merged:
        is_ar = country.lower() == "ar"
        jurisdiccionales_ar: list[dict] = []
        aval_principal: list[dict] = []
        otros: list[dict] = []

        # Heurísticas sobre título + descripción combinados.
        # Jurisdiccional AR = colegio/consejo médico provincial argentino
        # (COLEMEMI, COLMEDCAT, CSMLP, CMSC, CMSF, o texto explícito).
        for m in merged:
            tl = m["title"].lower()
            dl = m["description"].lower()
            combined = f"{tl} {dl}"
            if any(x in combined for x in [
                "colegio de médicos", "colegio médico",
                "consejo médico", "consejo superior médico",
                "colmedcat", "colememi", "csmlp", "cmsc", "cmsf",
                "misiones", "catamarca", "la pampa", "santa cruz", "santa fe",
            ]) or ("matricul" in combined and "argentina" in combined):
                jurisdiccionales_ar.append(m)
            elif "udima" in tl or "internacional" in dl or "amir" in tl:
                aval_principal.append(m)
            else:
                otros.append(m)

        lines.append("## Certificaciones disponibles")

        # ── Certificación MSK Digital — constante del negocio, NO viene del JSON.
        # Todos los cursos PAGOS incluyen MSK Digital bonificada (sin costo adicional).
        # Para cursos gratuitos (is_free=True) NO aplica.
        is_free_course = bool(prices.get("is_free", False))
        if not is_free_course:
            lines.append("### Certificación MSK Digital (incluida — bonificada con tu inscripción)")
            lines.append(
                "- **Certificado MSK Digital** — viene incluido sin costo adicional "
                "con la inscripción al curso. Es el certificado base que acredita "
                "la formación. **Mencionalo como valor de entrada en el pitch.**"
            )
            lines.append("")

        # Separar avales principales en:
        #   - INCLUIDOS (sin precio) → aval conceptual, se puede mencionar como valor
        #   - OPCIONALES CON COSTO (precio > 0, ej. UDIMA) → NO mencionar de entrada
        aval_incluidos = [m for m in aval_principal if not m["price_str"]]
        aval_opcionales_pagos = [m for m in aval_principal if m["price_str"]]

        if aval_incluidos:
            lines.append("### Aval académico principal (incluido)")
            for m in aval_incluidos:
                line = f"- **{m['title']}**"
                if m["description"]:
                    line += f" — {m['description']}"
                lines.append(line)
            lines.append("")

        if aval_opcionales_pagos:
            lines.append("### Certificación universitaria opcional (APARTE del precio del curso)")
            lines.append(
                "> **IMPORTANTE:** Estas certificaciones son **opcionales** y tienen "
                "**costo adicional aparte del precio del curso**. "
                "**NO las menciones en el primer pitch ni al listar cursos.** "
                "Solo comunicalas si el usuario pregunta puntualmente por "
                "certificación universitaria, UDIMA, o validez académica extendida. "
                "Al comunicarlas, aclará siempre: *'es opcional y se paga aparte'*."
            )
            for m in aval_opcionales_pagos:
                line = f"- **{m['title']}**"
                if m["description"]:
                    line += f" — {m['description']}"
                if m["price_str"]:
                    line += f" — **costo adicional:** {m['price_str']}"
                lines.append(line)
            lines.append("")

        if is_ar and jurisdiccionales_ar:
            lines.append("### Certificaciones de alcance jurisdiccional en Argentina")
            lines.append(
                "> **Solo aplican si el profesional está matriculado** en cada "
                "institución. No son obligatorias — son avales adicionales "
                "disponibles para quienes ya tienen matrícula. No las ofrezcas "
                "por defecto, solo si el usuario pregunta por avales locales, "
                "menciona su provincia o confirma que está matriculado."
            )
            for m in jurisdiccionales_ar:
                line = f"- **{m['title']}**"
                if m["description"]:
                    line += f" — {m['description']}"
                lines.append(line)
            lines.append("")
            lines.append(
                "**Cómo comunicarlo**: En el primer pitch SOLO mencionás el "
                "certificado MSK Digital (incluido/bonificado). "
                "Si el usuario está logueado y su ficha Zoho indica matrícula "
                "en alguno de estos colegios/consejos, podés mencionarlo "
                "proactivamente como beneficio adicional sin costo. "
                "Si el usuario pregunta por avales locales AR o menciona "
                "matrícula provincial, aclarale que puede sumar la certificación "
                "de su colegio/consejo **sin costo, si está matriculado ahí**."
            )
            lines.append("")
        elif is_ar:
            # No vinieron jurisdiccionales en el payload, pero el agente
            # igual tiene que manejar la pregunta si surge.
            lines.append("### Avales jurisdiccionales en Argentina")
            lines.append(
                "> Si el usuario pregunta por avales locales AR, aclarale que "
                "MSK trabaja con colegios/consejos médicos provinciales "
                "(COLEMEMI — Misiones, COLMEDCAT — Catamarca, CSMLP — La Pampa, "
                "CMSC — Santa Cruz, CMSF1 — Santa Fe), pero **estas "
                "certificaciones solo aplican si el profesional está "
                "matriculado** en esas instituciones. No son obligatorias."
            )
            lines.append("")

        if otros:
            lines.append("### Otros avales / convenios")
            for m in otros[:10]:
                line = f"- {m['title']}"
                if m["description"]:
                    line += f" — {m['description']}"
                if m["price_str"]:
                    line += f" ({m['price_str']})"
                lines.append(line)
            lines.append("")

    # ── Equipo docente (top 5 — coordinadores + autores destacados)
    team = (item.get("sections") or {}).get("teaching_team") or []
    if team:
        # Priorizar coordinadores
        coord = [t for t in team if "coordin" in (t.get("description") or "").lower()]
        autors = [t for t in team if t not in coord]
        top = (coord + autors)[:5]
        if top:
            lines.append("## Equipo docente destacado")
            for t in top:
                name = t.get("name", "")
                role = t.get("description", "") or ""
                spec = t.get("specialty", "") or ""
                bits = [name]
                if role:
                    bits.append(role)
                if spec:
                    bits.append(spec)
                lines.append("- " + " — ".join([b for b in bits if b]))
            lines.append("")

    return "\n".join(lines).strip()


# ── Transform: JSON → row para Postgres ─────────────────────────────────────

def to_row(item: dict, country: str) -> dict:
    prices = item.get("prices") or {}
    images = item.get("featured_images") or {}
    cedente = (item.get("cedente") or {}).get("title") or (item.get("cedente") or {}).get("name") or None

    url = item.get("link") or ""
    if url and not url.startswith("http"):
        url = f"https://msklatam.com/{url.lstrip('/')}"

    raw_date = item.get("date")
    source_updated_at: Optional[datetime] = None
    if raw_date:
        try:
            source_updated_at = datetime.fromisoformat(raw_date)
        except ValueError:
            source_updated_at = None

    brief = build_brief_md(item, country)

    return {
        "country": country.lower(),
        "slug": item.get("slug"),
        "product_id": _to_int(item.get("id")),
        "title": item.get("title") or "(sin título)",
        "categoria": _primary_category(item),
        "cedente": cedente,
        "duration_hours": _to_int(item.get("duration")),
        "modules_count": _to_int(item.get("modules")),
        "currency": prices.get("currency"),
        "regular_price": _to_float(prices.get("regular_price")),
        "sale_price": _to_float(prices.get("sale_price")),
        "total_price": _to_float(prices.get("total_price")),
        "max_installments": _to_int(prices.get("max_installments")),
        "price_installments": _to_float(prices.get("price_installments")),
        "is_free": bool(prices.get("is_free", False)),
        "url": url,
        "image_url": images.get("high") or images.get("medium") or images.get("low"),
        "excerpt": html_to_text(item.get("excerpt") or ""),
        "brief_md": brief,
        "raw": item,
        "source_cache": item.get("cache"),
        "source_updated_at": source_updated_at,
    }


# ── Sync ────────────────────────────────────────────────────────────────────

async def sync_country(country: str, prune: bool = True) -> dict:
    """
    Sincroniza todos los cursos de un país:
      1. Fetch del WP
      2. Upsert en public.courses
      3. (Opcional) delete de slugs que ya no vienen
    """
    country = country.lower()
    items = await fetch_country(country)

    upserted = 0
    errors: list[str] = []
    seen_slugs: list[str] = []

    for item in items:
        try:
            row = to_row(item, country)
            if not row["slug"]:
                continue
            seen_slugs.append(row["slug"])
            await postgres_store.upsert_course(row)
            upserted += 1
        except Exception as e:
            errors.append(f"{item.get('slug')}: {e}")
            logger.exception("course_upsert_failed", slug=item.get("slug"), country=country)

    # Invalidar cache Redis de los cursos actualizados
    try:
        from integrations import courses_cache
        await courses_cache.invalidate_country(country, seen_slugs)
    except Exception as e:
        logger.warning("course_cache_invalidate_failed", error=str(e))

    deleted = 0
    if prune and seen_slugs:
        deleted = await postgres_store.delete_missing_courses(country, seen_slugs)

    result = {
        "country": country,
        "fetched": len(items),
        "upserted": upserted,
        "deleted": deleted,
        "errors": errors[:10],  # truncar para logs
    }
    logger.info("msk_courses_sync_done", **result)
    return result
