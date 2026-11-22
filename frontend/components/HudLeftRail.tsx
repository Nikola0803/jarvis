"use client";

/**
 * Left rail: the inbox glance. Unread mail from every connected account, newest
 * and highest-priority first. Click a row to read it via Z.E.R.O, or reply.
 * Polls the backend every 90s and refreshes immediately when a voice turn ends
 * (voiceActive flips back to false).
 */

import { useCallback, useEffect, useState } from "react";

type Mail = {
  id: string;
  from: string;
  subject: string;
  date: string | null;
  snippet: string;
  unread: boolean;
  priority: string;
  _acct: string;
};

export default function HudLeftRail({
  voiceActive,
  onAsk,
}: {
  voiceActive: boolean;
  onAsk: (text: string) => void;
}) {
  const [rows, setRows] = useState<Mail[] | null>(null);
  const [err, setErr] = useState<string>("");

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/jarvis/emails?account=all&limit=6&unread_only=true", { cache: "no-store" });
      if (!res.ok) { setErr("unavailable"); return; }
      const data = await res.json();
      if (data && data.error) { setErr(data.error); setRows([]); return; }
      const all: Mail[] = [];
      for (const [acct, msgs] of Object.entries(data || {})) {
        if (Array.isArray(msgs)) {
          for (const m of msgs as Omit<Mail, "_acct">[]) all.push({ ...m, _acct: acct });
        }
      }
      const prio = (p: string) => ({ high: 0, medium: 1, low: 2 } as Record<string, number>)[p] ?? 3;
      all.sort((a, b) => {
        const pt = prio(a.priority) - prio(b.priority);
        if (pt !== 0) return pt;
        return (b.date || "").localeCompare(a.date || "");
      });
      setErr("");
      setRows(all.slice(0, 7));
    } catch {
      setErr("unavailable");
    }
  }, []);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const id = setInterval(load, 90000);
    return () => clearInterval(id);
  }, [load]);
  // A finished voice turn usually means something changed — pull fresh mail.
  useEffect(() => { if (!voiceActive) load(); }, [voiceActive, load]);

  const unreadCount = rows?.length ?? 0;

  return (
    <aside className="hud-rail hud-rail-left" aria-label="Unread mail">
      <RailHeader title="Inbox" badge={rows ? String(unreadCount) : undefined} onRefresh={load} />

      <div className="hud-rail-body">
        {rows === null && <div className="hud-empty">syncing…</div>}
        {err && <div className="hud-empty">{err}</div>}
        {rows && rows.length === 0 && !err && <div className="hud-empty">no unread mail</div>}

        {rows?.map((m) => (
          <div
            key={m.id}
            className={`hud-mail ${m.priority === "high" ? "is-hot" : ""}`}
            role="button"
            tabIndex={0}
            onClick={() => onAsk(`/mail read ${m.id}`)}
            onKeyDown={(e) => { if (e.key === "Enter") onAsk(`/mail read ${m.id}`); }}
            title="Click to have Z.E.R.O read it, shift-click to reply"
            onAuxClick={() => onAsk(`/mail reply ${m.id} saying `)}
          >
            <div className="hud-mail-top">
              <span className="hud-mail-from">{(m.from || "").split("<")[0].trim() || m.from}</span>
              <span className="hud-mail-time">{fmtTime(m.date)}</span>
            </div>
            <div className="hud-mail-subject">{m.subject || "(no subject)"}</div>
            <div className="hud-mail-snippet">{cleanSnippet(m.snippet)}</div>
          </div>
        ))}
      </div>
    </aside>
  );
}

export function RailHeader({ title, badge, onRefresh }: { title: string; badge?: string; onRefresh?: () => void }) {
  return (
    <div className="hud-rail-head">
      <span className="hud-rail-title">{title}</span>
      {badge !== undefined && <span className="hud-rail-badge">{badge}</span>}
      <span style={{ flex: 1 }} />
      {onRefresh && (
        <button type="button" className="hud-rail-refresh" onClick={onRefresh} aria-label={`Refresh ${title}`}>↻</button>
      )}
    </div>
  );
}

export function fmtTime(iso: string | null) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  return sameDay
    ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString([], { month: "short", day: "numeric" });
}

export function cleanSnippet(s: string) {
  return (s || "")
    .replace(/&#39;/g, "'")
    .replace(/&amp;/g, "&")
    .replace(/&quot;/g, '"')
    .replace(/[\u034f\u200b-\u200f\ufeff]/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 110);
}
