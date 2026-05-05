---
name: Pending — mejoras al prompt de sales desde el script de Gino
description: Ítems acordados pero pendientes de luz verde para meter al sales/closer prompt, basados en la "Bajada Comercial Botmaker" de Gino
type: project
---
Pendiente de aprobación de Gonzalo. Surgió tras analizar los artefactos del equipo comercial el 2026-04-28:
1. Conversación real ideal (Corina-Silvana, Curso Superior de Neonatología, Botmaker la marcó como modelo).
2. "Bajada Comercial Botmaker" — script estructurado de Gino con 9 secciones.

**3 cambios concretos sin volver a re-analizar todo**:

1. **Banco de objeciones más completo** en `agents/sales/prompts.py` — estructurar respuestas pre-armadas para: online vs presencial, duración, evaluaciones, acompañamiento, cuándo empieza, recertificación, cupo limitado.

2. **Frases de cierre cálidas** estilo Corina ("¡Hermosa cursada!", "Bienvenida a MSK", "Confirmamos el proceso de inscripción") al cerrar venta.

3. **Sondeo mini opcional** (1 sola pregunta): *"¿qué te motivó a buscar capacitarte en X?"*. Riesgo medio: puede atrasar el cierre si se aplica mal. **Nota 2026-05-05**: parcialmente cubierto por la regla 0b (excavar dolor proactivamente). Re-evaluar si todavía hace falta como item separado.

**NO meter** (ya descartado): avales hardcoded "Tropos" / "150 puntos recertificación", "Inscripción 100% GRATIS" (no aplica), pedir CBU/DNI/foto de tarjeta (Regla #7), inventar cupos limitados.
