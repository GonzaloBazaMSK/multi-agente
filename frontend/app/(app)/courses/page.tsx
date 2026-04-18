"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Search, Edit3, Check, X, Sparkles, Loader2 } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";

type Course = {
  slug: string;
  title: string;
  categoria: string | null;
  currency: string | null;
  max_installments: number | null;
  price_installments: number | null;
  pitch_hook: string | null;
  pitch_by_profile: Record<string, string> | null;
  has_kb_ai: boolean;
};

const COUNTRIES = ["AR", "MX", "CL", "CO", "PE", "UY", "EC", "ES"];

export default function CoursesPage() {
  const [country, setCountry] = useState("AR");
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const qc = useQueryClient();

  const coursesQ = useQuery<Course[]>({
    queryKey: ["courses", country],
    queryFn: () => api.get(`/inbox/courses?country=${country}&limit=300`),
    staleTime: 60_000,
  });

  const updatePitch = useMutation({
    mutationFn: ({ slug, pitch_hook }: { slug: string; pitch_hook: string }) =>
      api.put(`/inbox/courses/${country}/${slug}/pitch-hook`, { pitch_hook }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["courses", country] });
      setEditing(null);
    },
  });

  const items = (coursesQ.data ?? []).filter((c) => {
    if (!search.trim()) return true;
    const q = search.toLowerCase();
    return (
      c.title.toLowerCase().includes(q) ||
      c.slug.toLowerCase().includes(q) ||
      (c.categoria || "").toLowerCase().includes(q)
    );
  });

  const stats = {
    total: coursesQ.data?.length ?? 0,
    withPitch: (coursesQ.data ?? []).filter((c) => !!c.pitch_hook).length,
    withKb: (coursesQ.data ?? []).filter((c) => c.has_kb_ai).length,
  };

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Header */}
      <div className="px-6 py-4 border-b border-border flex items-center justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold">Catálogo de cursos</h1>
          <p className="text-xs text-fg-dim mt-0.5">
            {stats.total} cursos · {stats.withPitch} con pitch · {stats.withKb} con kb_ai
          </p>
        </div>
        <div className="flex gap-2 items-center">
          <select
            value={country}
            onChange={(e) => setCountry(e.target.value)}
            className="bg-bg border border-border rounded-md px-3 py-1.5 text-sm focus:outline-none focus:border-accent"
          >
            {COUNTRIES.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
          <div className="relative">
            <Input
              className="pl-8 w-72"
              placeholder="Buscar curso, slug, categoría..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <Search className="w-3.5 h-3.5 absolute left-2.5 top-2.5 text-fg-dim pointer-events-none" />
          </div>
        </div>
      </div>

      {/* Grid */}
      <div className="flex-1 overflow-y-auto scroll-thin p-6">
        {coursesQ.isLoading ? (
          <div className="text-center text-fg-dim text-sm py-10">
            <Loader2 className="w-5 h-5 animate-spin mx-auto mb-2" />
            Cargando cursos...
          </div>
        ) : items.length === 0 ? (
          <div className="text-center text-fg-dim text-sm py-10">
            No se encontraron cursos.
          </div>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {items.map((c) => (
              <div
                key={c.slug}
                className="bg-card border border-border rounded-lg p-4 hover:border-accent/40 transition"
              >
                <div className="flex items-start justify-between mb-3 gap-2">
                  <div className="min-w-0">
                    <div className="text-[10px] text-fg-dim uppercase tracking-wider">
                      {c.categoria || "Sin categoría"}
                    </div>
                    <div className="text-sm font-semibold mt-0.5 truncate">{c.title}</div>
                    <div className="text-[10px] text-fg-dim mt-0.5 font-mono truncate">{c.slug}</div>
                  </div>
                  <div className="flex flex-col items-end gap-1 shrink-0">
                    {c.pitch_hook ? (
                      <Badge variant="success">pitch ✓</Badge>
                    ) : (
                      <Badge variant="muted">sin pitch</Badge>
                    )}
                    {c.has_kb_ai ? (
                      <Badge variant="info"><Sparkles className="w-2.5 h-2.5" /> kb_ai</Badge>
                    ) : null}
                  </div>
                </div>

                {editing === c.slug ? (
                  <div className="space-y-2">
                    <textarea
                      value={draft}
                      onChange={(e) => setDraft(e.target.value)}
                      rows={5}
                      className="w-full bg-bg border border-border rounded-md p-2 text-xs focus:outline-none focus:border-accent resize-none"
                    />
                    <div className="flex justify-between items-center">
                      <span className="text-[10px] text-fg-dim">{draft.length} chars</span>
                      <div className="flex gap-1.5">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setEditing(null)}
                        >
                          <X className="w-3 h-3" /> Cancelar
                        </Button>
                        <Button
                          size="sm"
                          disabled={updatePitch.isPending}
                          onClick={() => updatePitch.mutate({ slug: c.slug, pitch_hook: draft })}
                        >
                          {updatePitch.isPending ? (
                            <><Loader2 className="w-3 h-3 animate-spin" /> Guardando...</>
                          ) : (
                            <><Check className="w-3 h-3" /> Guardar</>
                          )}
                        </Button>
                      </div>
                    </div>
                  </div>
                ) : (
                  <>
                    <div className="text-xs text-fg-muted leading-relaxed mb-3 italic min-h-[3rem]">
                      {c.pitch_hook || "— Sin pitch generado —"}
                    </div>
                    <div className="flex items-center justify-between text-[11px] text-fg-dim border-t border-border pt-2">
                      <div>
                        {c.max_installments && c.price_installments
                          ? `${c.max_installments}x ${c.currency || ""} ${Math.round(c.price_installments).toLocaleString("es-AR")}`
                          : "Consultar precio"}
                      </div>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => {
                          setEditing(c.slug);
                          setDraft(c.pitch_hook || "");
                        }}
                      >
                        <Edit3 className="w-3 h-3" /> Editar pitch
                      </Button>
                    </div>
                  </>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
