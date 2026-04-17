"use client";

import { useMemo, useState } from "react";
import { ConversationList } from "@/components/inbox/conversation-list";
import { ConversationDetail } from "@/components/inbox/conversation-detail";
import { ContactPanel } from "@/components/inbox/contact-panel";
import {
  type LifecycleStage,
  type Channel,
  type Queue,
  type InboxView,
  type ConversationListItem,
} from "@/lib/mock-data";
import {
  useConversations,
  useMessages,
  useContact,
  useAssign,
  useSnooze,
  useClassify,
  useToggleBot,
  useTakeover,
  useSetStatus,
  useBulkAssign,
  useBulkResolve,
  useBulkSnooze,
} from "@/lib/api/inbox";

const ME_ID = "u-gbaza"; // TODO: leer del auth

export default function InboxPage() {
  const [selectedId, setSelectedId] = useState<string>("");
  const [showContactPanel, setShowContactPanel] = useState(true);

  const [view, setView] = useState<InboxView>("all");
  const [lifecycle, setLifecycle] = useState<LifecycleStage | null>(null);
  const [channel, setChannel] = useState<Channel | null>(null);
  const [queue, setQueue] = useState<Queue | null>(null);
  const [search, setSearch] = useState("");
  const [bulkSelected, setBulkSelected] = useState<Set<string>>(new Set());

  // ── Queries ────────────────────────────────────────────────────────────
  const convsQ = useConversations({ view, lifecycle, channel, queue, search });
  const items = convsQ.data ?? [];

  // Auto-seleccionar la primera cuando carga
  const effectiveSelectedId =
    selectedId && items.find((c) => c.id === selectedId) ? selectedId : items[0]?.id ?? "";

  const selected = items.find((c) => c.id === effectiveSelectedId) ?? null;
  const messagesQ = useMessages(effectiveSelectedId || null);
  const contactQ = useContact(selected?.contact.email ?? null);

  // ── Counts (calculados client-side sobre la lista filtrada) ──────────
  const counts = useMemo(() => {
    const byLifecycle: Record<LifecycleStage, number> = { new: 0, hot: 0, customer: 0, cold: 0 };
    const byChannel:   Record<Channel, number> = { whatsapp: 0, widget: 0 };
    const byQueue:     Record<Queue, number> = { sales: 0, billing: 0, "post-sales": 0 };
    let unread = 0, mine = 0, qcount = 0, humanAttn = 0, withBot = 0, snoozed = 0, resolved = 0;
    for (const c of items) {
      byLifecycle[c.lifecycle]++;
      byChannel[c.channel]++;
      byQueue[c.queue]++;
      if (c.unread) unread++;
      if (c.assignedTo === ME_ID) mine++;
      if (c.assignedTo === null && c.needsHuman) qcount++;
      if (c.botPaused || c.assignedTo !== null) humanAttn++;
      if (!c.botPaused && !c.needsHuman) withBot++;
      if (c.snoozedUntil) snoozed++;
      if (c.status === "resolved") resolved++;
    }
    return {
      total: items.length,
      unread, mine, queue: qcount, humanAttn, withBot, snoozed, resolved,
      byLifecycle, byChannel, byQueue,
    };
  }, [items]);

  // ── Mutations ──────────────────────────────────────────────────────────
  const assignM    = useAssign();
  const snoozeM    = useSnooze();
  const classifyM  = useClassify();
  const toggleBotM = useToggleBot();
  const takeoverM  = useTakeover();
  const statusM    = useSetStatus();
  const bulkAssignM  = useBulkAssign();
  const bulkResolveM = useBulkResolve();
  const bulkSnoozeM  = useBulkSnooze();

  // Por conversación
  const handleAssign     = (agentId: string | null) => effectiveSelectedId && assignM.mutate({ id: effectiveSelectedId, agentId });
  const handleSnooze     = (until: string | null)   => effectiveSelectedId && snoozeM.mutate({ id: effectiveSelectedId, untilIso: until ?? undefined });
  const handleClassify   = (stage: LifecycleStage)  => effectiveSelectedId && classifyM.mutate({ id: effectiveSelectedId, lifecycle: stage });
  const handleToggleBot  = ()                        => selected && toggleBotM.mutate({ id: selected.id, paused: !selected.botPaused });
  const handleTakeover   = ()                        => effectiveSelectedId && takeoverM.mutate({ id: effectiveSelectedId, agentId: ME_ID });

  // Bulk
  const handleBulkToggle = (id: string) => {
    setBulkSelected((curr) => {
      const next = new Set(curr);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };
  const handleBulkSelectAll = () => {
    const visibleIds = items.map((c) => c.id);
    const allSel = visibleIds.every((id) => bulkSelected.has(id));
    setBulkSelected(allSel ? new Set() : new Set(visibleIds));
  };
  const handleBulkClear   = () => setBulkSelected(new Set());
  const handleBulkAssign  = (agentId: string | null) => {
    bulkAssignM.mutate({ ids: [...bulkSelected], agentId });
    setBulkSelected(new Set());
  };
  const handleBulkResolve = () => {
    bulkResolveM.mutate({ ids: [...bulkSelected] });
    setBulkSelected(new Set());
  };
  const handleBulkSnooze = (until: string) => {
    // until viene como "en 1 hora" del UI; mapear a duration backend
    const durationMap: Record<string, string> = {
      "en 1 hora": "1h",
      "en 4 horas": "4h",
      "mañana 09:00": "tomorrow",
      "la próxima semana": "next-week",
    };
    const duration = durationMap[until] || "1h";
    bulkSnoozeM.mutate({ ids: [...bulkSelected], duration });
    setBulkSelected(new Set());
  };

  return (
    <>
      <ConversationList
        items={items}
        selectedId={effectiveSelectedId}
        onSelect={setSelectedId}
        bulkSelected={bulkSelected}
        onBulkToggle={handleBulkToggle}
        onBulkSelectAll={handleBulkSelectAll}
        onBulkClear={handleBulkClear}
        onBulkAssign={handleBulkAssign}
        onBulkResolve={handleBulkResolve}
        onBulkSnooze={handleBulkSnooze}
        view={view}
        onViewChange={setView}
        lifecycle={lifecycle}
        onLifecycleChange={setLifecycle}
        channel={channel}
        onChannelChange={setChannel}
        queue={queue}
        onQueueChange={setQueue}
        search={search}
        onSearchChange={setSearch}
        counts={counts}
      />
      <ConversationDetail
        contact={contactQ.data ?? null}
        conversation={selected}
        messages={messagesQ.data ?? []}
        onToggleContactPanel={() => setShowContactPanel((s) => !s)}
        onAssign={handleAssign}
        onSnooze={handleSnooze}
        onTakeoverHuman={handleTakeover}
        onToggleBot={handleToggleBot}
        onClassify={handleClassify}
      />
      {showContactPanel && <ContactPanel contact={contactQ.data ?? null} />}
    </>
  );
}
