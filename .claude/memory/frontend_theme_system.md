---
name: Theme system claro/oscuro del frontend (CSS vars + toggle)
description: Cómo funciona el toggle light/dark en la consola Next.js — vars RGB para Tailwind, script anti-flash, useTheme hook
type: project
---
La consola Next.js (`frontend/`) soporta light/dark theme desde el commit `2608a11` (2026-05-05).

**Arquitectura**:

1. **Tailwind colors → CSS variables RGB** (`frontend/tailwind.config.ts`):
   - Los neutrales (`bg`, `panel`, `card`, `hover`, `border`, `border-2`, `fg`, `fg-muted`, `fg-dim`) usan `rgb(var(--c-X) / <alpha-value>)` para que `bg-accent/18` y similares funcionen.
   - Los brand colors (`accent` violeta, `success`, `warn`, `danger`, `info`) son hex directos — NO cambian entre temas.

2. **Variables en `globals.css`**:
   - `:root, .dark` → paleta oscura (default actual): bg `#0a0a0a`, panel `#111111`, card `#171717`, fg `#fafafa`.
   - `.light` → paleta clara: bg `#fafafa`, panel `#f5f5f5`, card `#ffffff`, fg `#0a0a0a`.

3. **Script anti-flash en `layout.tsx`**:
   - `<script>` inline en `<head>` que corre **sincrónico** antes del primer paint.
   - Lee `localStorage.getItem('msk-theme')`. Si es `'light'`, reemplaza `class="dark"` por `class="light"` en `<html>` antes de que React hidrate.
   - Sin esto, el user vería el dark un frame antes de que el cliente aplique light → flash desagradable.

4. **Hook `frontend/lib/use-theme.ts`**:
   - `useTheme()` devuelve `{ theme, setTheme, toggleTheme }`.
   - `toggleTheme()` reemplaza la clase del `<html>` y persiste a `localStorage`.

5. **Botón en el rail** (`frontend/components/layout/rail.tsx`):
   - Sun/Moon icon arriba de Settings. Tooltip explícito.

**Why:** La consola era 100% dark hardcoded — algunos users (Gonzalo) lo encontraban "chocante" para usarlo todo el día.

**How to apply:** Si vas a agregar componentes nuevos, usar las clases semánticas (`bg-bg`, `bg-panel`, `bg-card`, `text-fg`, `text-fg-muted`) y van a flippar solo. **NO uses** `bg-zinc-900`, `bg-black`, `text-white` o hex directos para neutrales — esos NO flippean. Para textos sobre fondos brand (accent), `text-white` está OK porque el accent es violeta sólido en ambos temas.

**Verificación**: si dudás si un cambio rompe light, grep `(bg|text|border|fill|stroke)-\[#` y `bg-(black|white)\b` en frontend.
