"use client";

/**
 * Right rail: the recall column. Today's agenda on top, the live conversation
 * thread underneath, so the owner can scroll back and re-ask about anything.
 * Clicking any turn re-asks Z.E.R.O to expand on that moment.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { RailHeader, fmtTime } from "./HudLeftRail";

type Event = { id: string; title: string; start: string | null; location?: string; link?: string; _acct: string };
type Turn = { ts: string; method: "voice" | "text"; transcript?: string; reply: string };

export default function HudRightRail({
  voiceActive,
  liveReply,
  liveTranscript,
  onAsk,
}: {
  voiceActive: boolean;
  liveReply: string;
  liveTranscript: string;
  onAsk: (text: string) => void;
}) {
  const [events, setEvents] = useState<Event[] | null>(null);
  const [calErr, setCalErr] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [pending, setPending] = useState<Turn | null>(null);
  const threadRef = useRef<HTMLDivElement>(null);

  const loadCal = useCallback(async () => {
    try {
      const res = await fetch("/api/jarvis/calendar?hours=48", { cache: "no-store" });
      const data = await res.json();
      if (!res.ok || data?.error) { setCalErr(data?.error || "unavailable"); setEvents([]); return; }
      const all: Event[] = [];
      for (const [acct, evts] of Object.entries(data || {})) {
        if (Array.isArray(evts)) for (const e of evts as Omit<Event, "_acct">[]) all.push({ ...e, _acct: acct });
      }
      all.sort((a, b) => (a.start || "").localeCompare(b.start || ""));
      setCalErr("");
      setEvents(all.slice(0, 5));
    } catch {
      setCalErr("unavailable");
    }
  }, []);

  const loadTurns = useCallback(async () => {
    try {
      const res = await fetch("/api/jarvis/conversations?limit=20", { cache: "no-store" });
      const data = await res.json();
      if (Array.isArray(data?.turns)) setTurns(data.turns);
    } catch {
      /* keep whatever we have */
    }
  }, []);

  useEffect(() => { loadCal(); loadTurns(); }, [loadCal, loadTurns]);
  useEffect(() => {
    const a = setInterval(loadCal, 300000);
    const b = setInterval(loadTurns, 30000);
    return () => { clearInterval(a); clearInterval(b); };
  }, [loadCal, loadTurns]);

  // A finished voice/text turn: show it, then reconcile with the server log.
  useEffect(() => {
    if (voiceActive && (liveReply || liveTranscript)) {
      setPending({
        ts: new Date().toISOString(),
        method: "voice",
        transcript: liveTranscript,
        reply: liveReply,
      });
    }
    if (!voiceActive) {
      const id = setTimeout(() => { setPending(null); loadTurns(); }, 1200);
      return () => clearTimeout(id);
    }
  }, [voiceActive, liveReply, liveTranscript, loadTurns]);

  const shown = pending ? [...turns, pending] : turns;

  useEffect(() => {
    const el = threadRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [shown.length, pending?.reply]);

  return (
    <aside className="hud-rail hud-rail-right" aria-label="Agenda and conversation">
      <section className="hud-rail-section">
        <RailHeader title="Agenda" badge={events?.length ? String(events.length) : undefined} onRefresh={loadCal} />
        <div className="hud-rail-body hud-rail-body-short">
          {events === null && <div className="hud-empty">syncing…</div>}
          {calErr && <div className="hud-empty" title={calErr}>{calErr === "Calendar not configured." ? "calendar offline" : calErr}</div>}
          {events && events.length === 0 && !calErr && <div className="hud-empty">clear for the next 48h</div>}
          {events?.map((e) => (
            <div key={e.id} className="hud-event">
              <span className="hud-event-time">{fmtEventStart(e.start)}</span>
              <span className="hud-event-title">{e.title}</span>
            </div>
          ))}
        </div>
      </section>

      <section className="hud-rail-section hud-rail-section-grow">
        <RailHeader title="Thread" badge={turns.length ? String(turns.length) : undefined} onRefresh={loadTurns} />
        <div className="hud-rail-body hud-thread" ref={threadRef}>
          {shown.length === 0 && <div className="hud-empty">nothing said yet today</div>}
          {shown.map((t, i) => (
            <div key={`${t.ts}-${i}`} className="hud-turn">
              <div className="hud-turn-meta">
                <span>{fmtTime(t.ts)}</span>
                <span className="hud-turn-dot" aria-hidden="true" />
                <span>{t.method}</span>
              </div>
              {t.transcript && (
                <button
                  type="button"
                  className="hud-turn-you"
                  onClick={() => onAsk(`You said "${(t.transcript || "").slice(0, 160)}". Tell me more about that.`)}
                  title="Ask Z.E.R.O to expand on this"
                >
                  {t.transcript.slice(0, 140)}
                </button>
              )}
              <div className="hud-turn-zero">{t.reply}</div>
            </div>
          ))}
          {pending && <div className="hud-live-dot" aria-hidden="true" />}
        </div>
      </section>
    </aside>
  );
}

function fmtEventStart(iso: string | null) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "—";
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return sameDay ? time : `${d.toLocaleDateString([], { weekday: "short" })} ${time}`;
}
