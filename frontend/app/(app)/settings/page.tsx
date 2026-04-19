import { redirect } from "next/navigation";

// Entry point de /settings — llevamos directo a la sub-página por default.
// El layout (layout.tsx) renderiza la sub-nav lateral con todas las opciones.
export default function SettingsIndex() {
  redirect("/settings/agents");
}
