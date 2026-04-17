"use client";

import { useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import {
  Smile,
  Paperclip,
  Zap,
  Mic,
  StopCircle,
  Pause,
  SpellCheck,
  Loader2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dropdown, DropdownItem, DropdownLabel } from "@/components/ui/dropdown";
import { useCorrectSpelling } from "@/lib/api/inbox";

// Picker pesado, lazy load (no entra al bundle inicial)
const EmojiPicker = dynamic(() => import("emoji-picker-react"), { ssr: false });

const QUICK_TEMPLATES = [
  { label: "Saludo profesional", text: "¡Hola! Soy [tu nombre] del equipo de MSK. ¿En qué puedo ayudarte?" },
  { label: "Pedir nombre+email", text: "Para poder generarte el link de pago necesito tu nombre completo y email. ¿Me los pasás?" },
  { label: "Ofrecer cupón BOT20", text: "Te puedo armar un 20% off con el código BOT20. ¿Te sirve para arrancar hoy?" },
  { label: "Cierre + agradecer",  text: "¡Listo! Cualquier consulta escribime y te respondo. Que tengas buen día 🙌" },
];

interface Props {
  botPaused: boolean;
  onToggleBot: () => void;
}

export function Composer({ botPaused, onToggleBot }: Props) {
  const [draft, setDraft] = useState("");
  const [attachments, setAttachments] = useState<File[]>([]);
  const [recording, setRecording] = useState(false);
  const [recordSec, setRecordSec] = useState(0);
  const [showEmojis, setShowEmojis] = useState(false);
  const [correcting, setCorrecting] = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const audioStreamRef = useRef<MediaStream | null>(null);

  // ── Timer del grabador ────────────────────────────────────────────────
  useEffect(() => {
    if (!recording) {
      setRecordSec(0);
      return;
    }
    const t = setInterval(() => setRecordSec((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, [recording]);

  // ── Limpieza si se desmonta mientras está grabando ────────────────────
  useEffect(() => {
    return () => {
      stopMediaStream();
    };
  }, []);

  function stopMediaStream() {
    audioStreamRef.current?.getTracks().forEach((t) => t.stop());
    audioStreamRef.current = null;
    mediaRecorderRef.current = null;
    audioChunksRef.current = [];
  }

  // ── Adjuntar archivos ─────────────────────────────────────────────────
  const onPickFiles = () => fileInputRef.current?.click();
  const onFilesSelected = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []);
    if (files.length) setAttachments((a) => [...a, ...files]);
    e.target.value = "";
  };
  const removeAttachment = (i: number) =>
    setAttachments((a) => a.filter((_, idx) => idx !== i));

  // ── Emoji picker ──────────────────────────────────────────────────────
  const insertEmoji = (emoji: string) => {
    setDraft((d) => d + emoji);
  };

  // ── Plantillas ────────────────────────────────────────────────────────
  const useTemplate = (text: string) => setDraft(text);

  // ── Corrección ortográfica REAL via OpenAI ──────────────────────────
  const correctSpellingMut = useCorrectSpelling();
  const correctSpelling = async () => {
    if (!draft.trim() || correctSpellingMut.isPending) return;
    setCorrecting(true);
    try {
      const res = await correctSpellingMut.mutateAsync({ text: draft });
      if (res.changed) {
        setDraft(res.corrected);
      } else {
        // Sin cambios — pequeño feedback visual
        const ta = document.activeElement as HTMLElement;
        ta?.blur();
        setTimeout(() => ta?.focus(), 50);
      }
    } catch (err) {
      console.error("[corrector] LLM error", err);
      alert("No se pudo corregir el texto: " + (err as Error).message);
    } finally {
      setCorrecting(false);
    }
  };

  // ── Grabación de audio (real, MediaRecorder API) ──────────────────────
  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      audioStreamRef.current = stream;
      audioChunksRef.current = [];

      const mr = new MediaRecorder(stream);
      mr.ondataavailable = (e) => {
        if (e.data.size > 0) audioChunksRef.current.push(e.data);
      };
      mr.onstop = () => {
        const blob = new Blob(audioChunksRef.current, { type: "audio/webm" });
        const file = new File([blob], `audio-${Date.now()}.webm`, { type: "audio/webm" });
        setAttachments((a) => [...a, file]);
        stopMediaStream();
      };
      mr.start();
      mediaRecorderRef.current = mr;
      setRecording(true);
    } catch (err) {
      console.error("No se pudo acceder al micrófono:", err);
      alert("No se pudo acceder al micrófono. Asegurate de dar permisos en el browser.");
    }
  };

  const cancelRecording = () => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== "inactive") {
      // Override del onstop para que NO agregue como adjunto
      mediaRecorderRef.current.onstop = () => stopMediaStream();
      mediaRecorderRef.current.stop();
    } else {
      stopMediaStream();
    }
    setRecording(false);
  };

  const stopAndAttachRecording = () => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== "inactive") {
      mediaRecorderRef.current.stop();
    }
    setRecording(false);
  };

  // ── Enviar ────────────────────────────────────────────────────────────
  const handleSend = () => {
    if (!draft.trim() && attachments.length === 0) return;
    console.log("[mock send]", { text: draft, attachments: attachments.map((f) => f.name) });
    setDraft("");
    setAttachments([]);
  };

  // ─────────────────────────────────────────────────────────────────────
  return (
    <div className="border-t border-border bg-panel p-4">
      {/* Estado del bot */}
      <div className="flex items-center gap-2 mb-2">
        {botPaused ? (
          <span className="text-[10px] text-warn flex items-center gap-1.5">
            <Pause className="w-3 h-3" />
            Bot pausado · respondés vos como humano
          </span>
        ) : (
          <span className="text-[10px] text-fg-dim flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-success animate-pulse" />
            Bot activo · respondiendo automáticamente
          </span>
        )}
        <button
          onClick={onToggleBot}
          className={`ml-auto text-[10px] hover:underline ${botPaused ? "text-success" : "text-warn"}`}
        >
          {botPaused ? "Reactivar bot" : "Pausar bot"}
        </button>
      </div>

      {/* Input file oculto */}
      <input
        ref={fileInputRef}
        type="file"
        multiple
        className="hidden"
        onChange={onFilesSelected}
      />

      {recording ? (
        <div className="bg-danger/10 border border-danger/40 rounded-md p-3 flex items-center gap-3">
          <span className="w-2.5 h-2.5 rounded-full bg-danger animate-pulse" />
          <span className="text-sm text-danger font-mono tabular-nums">
            {Math.floor(recordSec / 60).toString().padStart(2, "0")}:
            {(recordSec % 60).toString().padStart(2, "0")}
          </span>
          <span className="text-[11px] text-fg-muted">Grabando audio (real, MediaRecorder)…</span>
          <div className="ml-auto flex gap-1">
            <Button variant="ghost" size="sm" onClick={cancelRecording}>
              Cancelar
            </Button>
            <Button variant="default" size="sm" onClick={stopAndAttachRecording}>
              <StopCircle className="w-3.5 h-3.5" /> Detener y adjuntar
            </Button>
          </div>
        </div>
      ) : (
        <div className="bg-bg border border-border rounded-md p-2">
          {/* Adjuntos pendientes */}
          {attachments.length > 0 && (
            <div className="flex flex-wrap gap-1 mb-2 pb-2 border-b border-border">
              {attachments.map((f, i) => {
                const isAudio = f.type.startsWith("audio/");
                return (
                  <span
                    key={`${f.name}-${i}`}
                    className="text-[11px] bg-hover text-fg-muted rounded px-2 py-0.5 flex items-center gap-1.5"
                  >
                    {isAudio ? <Mic className="w-3 h-3 text-accent" /> : <Paperclip className="w-3 h-3" />}
                    <span className="max-w-[180px] truncate" title={f.name}>{f.name}</span>
                    <span className="text-fg-dim">{(f.size / 1024).toFixed(0)}KB</span>
                    <button
                      onClick={() => removeAttachment(i)}
                      className="text-fg-dim hover:text-danger ml-1"
                      title="Quitar"
                    >×</button>
                  </span>
                );
              })}
            </div>
          )}

          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            disabled={correcting}
            className="w-full bg-transparent text-sm placeholder-fg-dim focus:outline-none resize-none disabled:opacity-50"
            rows={2}
            placeholder={
              botPaused
                ? "Escribí tu mensaje (vas a responder vos como humano)..."
                : "Si escribís acá vas a tomar control de la conversación (pausa el bot)..."
            }
          />

          <div className="flex items-center gap-1 mt-1 pt-1 border-t border-border">
            {/* Emoji picker WA-style (lazy) */}
            <div className="relative">
              <Button
                variant="ghost"
                size="icon-sm"
                title="Emojis"
                onClick={() => setShowEmojis((s) => !s)}
              >
                <Smile className="w-4 h-4" />
              </Button>
              {showEmojis && (
                <>
                  {/* Backdrop para cerrar al click fuera */}
                  <div
                    className="fixed inset-0 z-20"
                    onClick={() => setShowEmojis(false)}
                  />
                  <div
                    className="absolute bottom-full left-0 mb-1 z-30"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <EmojiPicker
                      onEmojiClick={(d) => {
                        insertEmoji(d.emoji);
                      }}
                      theme={"dark" as never}
                      width={340}
                      height={400}
                      lazyLoadEmojis
                      searchPlaceHolder="Buscar emoji..."
                      previewConfig={{ showPreview: false }}
                    />
                  </div>
                </>
              )}
            </div>

            {/* Adjuntar */}
            <Button variant="ghost" size="icon-sm" onClick={onPickFiles} title="Adjuntar archivo">
              <Paperclip className="w-4 h-4" />
            </Button>

            {/* Plantillas */}
            <Dropdown
              align="left"
              side="up"
              trigger={
                <Button variant="ghost" size="icon-sm" title="Plantillas rápidas">
                  <Zap className="w-4 h-4" />
                </Button>
              }
            >
              {(close) => (
                <>
                  <DropdownLabel>Plantillas rápidas</DropdownLabel>
                  {QUICK_TEMPLATES.map((t) => (
                    <DropdownItem
                      key={t.label}
                      onClick={() => { useTemplate(t.text); close(); }}
                    >
                      <div className="flex flex-col items-start gap-0.5">
                        <span className="font-medium">{t.label}</span>
                        <span className="text-[10px] text-fg-dim line-clamp-1">{t.text}</span>
                      </div>
                    </DropdownItem>
                  ))}
                </>
              )}
            </Dropdown>

            {/* Grabar voz REAL */}
            <Button variant="ghost" size="icon-sm" onClick={startRecording} title="Grabar mensaje de voz">
              <Mic className="w-4 h-4" />
            </Button>

            {/* Corregir ortografía — OpenAI gpt-4o-mini real */}
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={correctSpelling}
              disabled={!draft.trim() || correcting}
              title="Corregir ortografía con IA"
            >
              {correcting ? (
                <Loader2 className="w-4 h-4 animate-spin text-accent" />
              ) : (
                <SpellCheck className="w-4 h-4" />
              )}
            </Button>

            <div className="ml-auto flex items-center gap-2">
              <span className="text-[10px] text-fg-dim">
                {draft.length} chars
                {attachments.length > 0 && ` · ${attachments.length} adj`}
              </span>
              <Button
                disabled={(!draft.trim() && attachments.length === 0) || correcting}
                onClick={handleSend}
              >
                Enviar
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
