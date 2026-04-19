import { redirect } from "next/navigation";

/**
 * Histórico: /users era la CRUD del equipo humano. Lo mudamos a
 * /settings/agents dentro del refactor de Configuración (Botmaker-style
 * sub-nav). Dejamos este redirect para que cualquier link o bookmark viejo
 * siga funcionando.
 */
export default function UsersRedirect() {
  redirect("/settings/agents");
}
