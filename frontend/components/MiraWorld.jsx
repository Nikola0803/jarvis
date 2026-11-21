"use client";

import { useEffect, useRef, useState } from "react";
import ShaderBackground from "./ShaderBackground";
import MiraField from "./MiraField";
import MiraRing from "./MiraRing";
import MiraCore3D from "./MiraCore3D";
import MiraStatusBar from "./MiraStatusBar";
import MiraDial from "./MiraDial";
import { enterMira, tickMiraVoice } from "../lib/miraStage";
import { setVoiceEnergy, getVoiceEnergy } from "../lib/voiceEnergy";
import { stopMic, micLevel } from "../lib/micLevel";
import "./mira-ring.css";

const LABEL = { idle: "standby", listening: "listening", thinking: "processing", speaking: "speaking" };

function fakeVoice(t, seed) {
  const phrase = Math.max(0, Math.sin(t * 0.9 + seed) * 0.6 + 0.55);
  const syll = Math.max(0, Math.sin(t * 11.3 + seed * 3) * Math.sin(t * 7.1 + 1.7));
  const jitter = (Math.sin(t * 37.0 + seed) + 1) * 0.08;
  return Math.min(1, phrase * (0.25 + syll * 0.9) + jitter * phrase);
}

export default function MiraWorld() {
  const [state, setState] = useState("idle");
  const [reducedMotion, setReducedMotion] = useState(false);
  const [dialOpen, setDialOpen] = useState(false);
  const [textOpen, setTextOpen] = useState(false);
  const [infoOpen, setInfoOpen] = useState(false);
  const [textInput, setTextInput] = useState("");
  const [replyText, setReplyText] = useState("");
  const [emails, setEmails] = useState(null);
  const [calendar, setCalendar] = useState(null);
  const [infoTab, setInfoTab] = useState("mail"); // "mail" | "cal"
  const stateRef = useRef(state);
  const isRealAudio = useRef(false);
  const replyTimer = useRef(null);
  const audioCtxRef = useRef(null);
  const textInputRef = useRef(null);
  const vadRef = useRef(null);
  const sendVoiceAudioRef = useRef(null);
  const isBusyRef = useRef(false);           // atomic flag: prevents concurrent voice requests
  const speakCooldownRef = useRef(false);    // 1s cooldown after speaking: blocks echo re-trigger
  const alertTimerRef = useRef(null);        // 30s alert mode timer
  const interruptRef = useRef(null);         // fn() to stop current audio + resolve promise
  const interruptedRef = useRef(false);      // set when interrupt fires; prevents idle reset
  const [alertMode, setAlertMode] = useState(false);
  stateRef.current = state;

  async function fetchEmails() {
    try {
      const res = await fetch("/api/jarvis/emails?account=all&limit=8&unread_only=true");
      if (res.ok) setEmails(await res.json());
    } catch (_) {}
  }

  async function fetchCalendar() {
    try {
      const res = await fetch("/api/jarvis/calendar?hours=24");
      if (res.ok) setCalendar(await res.json());
    } catch (_) {}
  }

  function openInfo(tab) {
    setInfoTab(tab);
    setInfoOpen(true);
    if (tab === "mail") fetchEmails();
    else fetchCalendar();
  }

  function unlockAudio() {
    try {
      if (!audioCtxRef.current) {
        audioCtxRef.current = new (window.AudioContext || window.webkitAudioContext)();
      }
      if (audioCtxRef.current.state === "suspended") {
        audioCtxRef.current.resume().catch(() => {});
      }
    } catch (_) {}
  }

  // Unlock audio on first user interaction so playback works without a tap
  useEffect(() => {
    const once = () => { unlockAudio(); document.removeEventListener("click", once); document.removeEventListener("keydown", once); };
    document.addEventListener("click", once);
    document.addEventListener("keydown", once);
    return () => { document.removeEventListener("click", once); document.removeEventListener("keydown", once); };
  }, []);

  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const apply = () => setReducedMotion(mq.matches);
    apply();
    mq.addEventListener("change", apply);
    return () => mq.removeEventListener("change", apply);
  }, []);

  useEffect(() => { enterMira({ instant: true }); }, []);

  // Always-on VAD: loads Silero VAD model, listens continuously
  useEffect(() => {
    let vad = null;

    function float32ToWav(samples, rate = 16000) {
      const len = samples.length * 2;
      const buf = new ArrayBuffer(44 + len);
      const v = new DataView(buf);
      const w = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
      w(0, "RIFF"); v.setUint32(4, 36 + len, true);
      w(8, "WAVE"); w(12, "fmt ");
      v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
      v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true);
      v.setUint16(32, 2, true); v.setUint16(34, 16, true);
      w(36, "data"); v.setUint32(40, len, true);
      let o = 44;
      for (let i = 0; i < samples.length; i++) {
        const s = Math.max(-1, Math.min(1, samples[i]));
        v.setInt16(o, s < 0 ? s * 0x8000 : s * 0x7FFF, true); o += 2;
      }
      return new Blob([buf], { type: "audio/wav" });
    }

    import("@ricky0123/vad-web").then(async (vadMod) => {
      const { MicVAD } = vadMod;
      // ort may not be re-exported in all vad-web versions; guard before use
      try {
        const ort = vadMod.ort;
        if (ort?.env?.wasm) {
          ort.env.wasm.numThreads = 1;
          ort.env.wasm.wasmPaths = "https://cdn.jsdelivr.net/npm/onnxruntime-web@1.31.1/dist/";
        }
      } catch (_) {}

      vad = await MicVAD.new({
        baseAssetPath: "/",
        onnxWASMBasePath: "https://cdn.jsdelivr.net/npm/onnxruntime-web@1.31.1/dist/",
        model: "v5",
        startOnLoad: true,
        onSpeechStart: () => {
          if (stateRef.current === "speaking") {
            // Voice interrupt: stop Z.E.R.O mid-sentence and start listening
            interruptedRef.current = true;
            interruptRef.current?.();
            isBusyRef.current = true;
            clearTimeout(alertTimerRef.current);
            setState("listening");
            return;
          }
          if (stateRef.current !== "idle" || isBusyRef.current || speakCooldownRef.current) return;
          isBusyRef.current = true;
          clearTimeout(alertTimerRef.current);
          unlockAudio();
          setState("listening");
        },
        onSpeechEnd: (audio) => {
          if (stateRef.current !== "listening") return;
          setState("thinking");
          if (sendVoiceAudioRef.current) sendVoiceAudioRef.current(float32ToWav(audio));
        },
        onVADMisfire: () => {
          if (stateRef.current === "listening") {
            isBusyRef.current = false;
            setState("idle");
          }
        },
      });
      vadRef.current = vad;
    }).catch((err) => {
      console.error("VAD init failed:", err);
    });

    return () => { vad?.destroy(); };
  }, []);

  // Animation loop: drives fake voice when no real audio is playing
  useEffect(() => {
    let raf = 0;
    let last = performance.now();
    const tick = () => {
      const now = performance.now();
      const dt = (now - last) / 1000;
      last = now;
      const t = now / 1000;
      const s = stateRef.current;
      if (!isRealAudio.current) {
        setVoiceEnergy("output", s === "speaking" ? fakeVoice(t, 0.3) : 0);
      }
      const mic = s === "listening" ? micLevel() : null;
      setVoiceEnergy("input", s !== "listening" ? 0 : mic != null ? mic : fakeVoice(t * 0.8, 2.1) * 0.7);
      tickMiraVoice(dt, { energy: getVoiceEnergy("output"), speaking: s === "speaking", listening: s === "listening" });
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);

  useEffect(() => () => stopMic(), []);

  async function playAudio(blob) {
    return new Promise((resolve) => {
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);

      // Play directly without routing through WebAudio -- MediaElementSource causes
      // mobile cutoffs. Visualization falls back to the fake-voice animation loop.
      audio.onplay = () => {
        isRealAudio.current = true;
        setState("speaking");
      };

      const done = () => {
        interruptRef.current = null;
        isRealAudio.current = false;
        setVoiceEnergy("output", 0);
        URL.revokeObjectURL(url);
        audioCtxRef.current?.close().catch(() => {});
        audioCtxRef.current = null;
        resolve();
      };
      interruptRef.current = () => { audio.pause(); done(); };
      audio.onended = done;
      audio.onerror = done;
      audio.play().catch(done);
    });
  }

  // Streaming playback via MediaSource Extensions -- starts playing as soon as first
  // MP3 chunks arrive, before the full TTS response is complete.
  async function playStreamingAudio(res) {
    return new Promise((resolve) => {
      const ms = new MediaSource();
      const url = URL.createObjectURL(ms);
      const audio = new Audio(url);

      const cleanup = () => {
        interruptRef.current = null;
        isRealAudio.current = false;
        setVoiceEnergy("output", 0);
        URL.revokeObjectURL(url);
        resolve();
      };
      interruptRef.current = () => {
        audio.pause();
        try { ms.endOfStream(); } catch (_) {}
        cleanup();
      };

      audio.onplay = () => {
        isRealAudio.current = true;
        setState("speaking");
      };
      audio.onended = cleanup;
      audio.onerror = cleanup;

      ms.addEventListener("sourceopen", async () => {
        let sb;
        try {
          sb = ms.addSourceBuffer("audio/mpeg");
          sb.mode = "sequence";
        } catch (_) {
          cleanup();
          return;
        }

        const appendChunk = (chunk) =>
          new Promise((res) => {
            if (sb.updating) {
              sb.addEventListener("updateend", res, { once: true });
            } else {
              res();
            }
          }).then(() => {
            sb.appendBuffer(chunk);
            return new Promise((r) => sb.addEventListener("updateend", r, { once: true }));
          });

        audio.play().catch(cleanup);

        try {
          const reader = res.body.getReader();
          while (true) {
            const { done, value } = await reader.read();
            if (done) {
              if (!sb.updating) {
                try { ms.endOfStream(); } catch (_) {}
              } else {
                sb.addEventListener("updateend", () => { try { ms.endOfStream(); } catch (_) {} }, { once: true });
              }
              break;
            }
            await appendChunk(value);
          }
        } catch (_) {
          try { ms.endOfStream("network"); } catch (_) {}
          cleanup();
        }
      });
    });
  }

  async function sendVoiceAudio(blob) {
    if (!blob || blob.size < 1000) { setState("idle"); isBusyRef.current = false; return; }
    try {
      const form = new FormData();
      form.append("audio", blob, "voice.wav");
      const res = await fetch("/api/jarvis/voice", { method: "POST", body: form });
      if (res.ok && res.headers.get("content-type")?.includes("audio")) {
        const reply = res.headers.get("X-Reply");
        if (reply) { setReplyText(reply); clearTimeout(replyTimer.current); replyTimer.current = setTimeout(() => setReplyText(""), 10000); }
        const canStream = typeof window.MediaSource !== "undefined" && MediaSource.isTypeSupported("audio/mpeg");
        if (canStream) {
          await playStreamingAudio(res);
        } else {
          const buf = await res.arrayBuffer();
          await playAudio(new Blob([buf], { type: "audio/mpeg" }));
        }
        setReplyText("");
      } else {
        const err = await res.json().catch(() => ({}));
        const msg = err.reply || err.error;
        if (msg) { setReplyText(msg); clearTimeout(replyTimer.current); replyTimer.current = setTimeout(() => setReplyText(""), 10000); }
      }
    } catch (err) {
      console.error("Z.E.R.O voice pipeline:", err);
    }
    if (interruptedRef.current) {
      // Voice interrupt happened: state is already "listening", don't clobber it
      interruptedRef.current = false;
    } else {
      // Natural end: cooldown prevents Z.E.R.O's own voice echo from re-triggering
      speakCooldownRef.current = true;
      setTimeout(() => { speakCooldownRef.current = false; }, 1000);
      setAlertMode(true);
      clearTimeout(alertTimerRef.current);
      alertTimerRef.current = setTimeout(() => setAlertMode(false), 30000);
      setState("idle");
      isBusyRef.current = false;
    }
  }

  // Keep ref up-to-date so the VAD closure (created once) always calls the latest version
  sendVoiceAudioRef.current = sendVoiceAudio;

  async function handleTextSubmit(e) {
    e.preventDefault();
    const text = textInput.trim();
    if (!text || stateRef.current !== "idle") return;
    setTextInput("");
    setTextOpen(false);
    setState("thinking");
    try {
      const res = await fetch("/api/jarvis/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (res.ok && res.headers.get("content-type")?.includes("audio")) {
        const reply = res.headers.get("X-Reply");
        if (reply) { setReplyText(reply); clearTimeout(replyTimer.current); replyTimer.current = setTimeout(() => setReplyText(""), 10000); }
        const canStream = typeof window.MediaSource !== "undefined" && MediaSource.isTypeSupported("audio/mpeg");
        if (canStream) {
          await playStreamingAudio(res);
        } else {
          const buf = await res.arrayBuffer();
          await playAudio(new Blob([buf], { type: "audio/mpeg" }));
        }
        setReplyText("");
      } else {
        const err = await res.json().catch(() => ({}));
        if (err.reply || err.error) setReplyText(err.reply || err.error);
      }
    } catch (err) {
      console.error("Z.E.R.O text pipeline:", err);
    }
    speakCooldownRef.current = true;
    setTimeout(() => { speakCooldownRef.current = false; }, 1000);
    setAlertMode(true);
    clearTimeout(alertTimerRef.current);
    alertTimerRef.current = setTimeout(() => setAlertMode(false), 30000);
    setState("idle");
  }

  function handleOrbTap() {
    // Tap while speaking: interrupt playback and return to idle
    if (stateRef.current === "speaking") {
      isBusyRef.current = false;
      interruptedRef.current = true;
      interruptRef.current?.();
      isRealAudio.current = false;
      setVoiceEnergy("output", 0);
      setState("idle");
    }
    // idle/listening/thinking: VAD handles everything automatically
  }

  return (
    <>
      <div aria-hidden="true" style={{
        position: "fixed", inset: 0, zIndex: 0, pointerEvents: "none",
        background: "radial-gradient(ellipse 80% 70% at 50% 46%, #0d0718 0%, #06030d 55%, #030108 100%)",
      }} />

      <ShaderBackground opacity={0.12} voiceActive={state === "speaking" || state === "listening"} />

      {!reducedMotion && <MiraField state={state} />}

      <div style={{ position: "fixed", inset: 0, zIndex: 15, pointerEvents: "none" }}>
        <div data-mira-orb style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", pointerEvents: "none" }}>
          <div className="mira-orb-scale"><MiraRing state={state} /></div>
        </div>
      </div>

      {!reducedMotion && (
        <div style={{ position: "fixed", inset: 0, zIndex: 15, pointerEvents: "none" }}>
          <MiraCore3D state={state} />
        </div>
      )}

      <MiraStatusBar state={state} alertMode={alertMode} />

      <button
        type="button"
        onClick={handleOrbTap}
        aria-label={`Z.E.R.O is ${LABEL[state]}. ${state === "speaking" ? "Tap to stop." : "Always listening."}`}
        title={state === "speaking" ? "Tap to stop" : state === "thinking" ? "Processing..." : "Always listening"}
        className="mira-tap"
      />

      {dialOpen
        ? <MiraDial onClose={() => setDialOpen(false)} state={state} onState={setState} />
        : (
          <div style={{ position: "fixed", bottom: 24, right: 24, zIndex: 50, display: "flex", gap: 8, alignItems: "center" }}>
            <button
              type="button"
              onClick={() => openInfo("mail")}
              aria-label="Check emails"
              title="Inbox"
              style={{
                background: infoOpen && infoTab === "mail" ? "rgba(109,40,217,0.6)" : "rgba(14,8,26,0.7)",
                border: "1px solid rgba(167,139,250,0.25)",
                borderRadius: 10, padding: "7px 10px", cursor: "pointer",
                color: "rgba(167,139,250,0.8)", fontSize: 14, lineHeight: 1,
                backdropFilter: "blur(12px)",
              }}
            >✉</button>
            <button
              type="button"
              onClick={() => openInfo("cal")}
              aria-label="Check calendar"
              title="Today"
              style={{
                background: infoOpen && infoTab === "cal" ? "rgba(109,40,217,0.6)" : "rgba(14,8,26,0.7)",
                border: "1px solid rgba(167,139,250,0.25)",
                borderRadius: 10, padding: "7px 10px", cursor: "pointer",
                color: "rgba(167,139,250,0.8)", fontSize: 14, lineHeight: 1,
                backdropFilter: "blur(12px)",
              }}
            >📅</button>
            <button
              type="button"
              onClick={() => { setTextOpen((v) => !v); setTimeout(() => textInputRef.current?.focus(), 50); }}
              aria-label="Toggle text input"
              title="Type to Jarvis"
              style={{
                background: textOpen ? "rgba(109,40,217,0.6)" : "rgba(14,8,26,0.7)",
                border: "1px solid rgba(167,139,250,0.25)",
                borderRadius: 10, padding: "7px 10px", cursor: "pointer",
                color: "rgba(167,139,250,0.8)", fontSize: 14, lineHeight: 1,
                backdropFilter: "blur(12px)",
              }}
            >⌨</button>
            <button type="button" className="dial-pill" onClick={() => setDialOpen(true)} aria-label="Open the design dial">Design dial</button>
          </div>
        )}

      {textOpen && (
        <div style={{
          position: "fixed", bottom: 72, left: "50%", transform: "translateX(-50%)",
          width: "min(480px, 90vw)", zIndex: 50,
          background: "rgba(14,8,26,0.92)", border: "1px solid rgba(167,139,250,0.25)",
          borderRadius: 14, padding: "12px 14px",
          backdropFilter: "blur(16px)",
        }}>
          <div style={{ fontFamily: "var(--font-mono)", fontSize: 8, letterSpacing: "0.26em", color: "rgba(167,139,250,0.5)", marginBottom: 8 }}>TEXT INPUT</div>
          <form onSubmit={handleTextSubmit} style={{ display: "flex", gap: 8 }}>
            <input
              ref={textInputRef}
              type="text"
              value={textInput}
              onChange={(e) => setTextInput(e.target.value)}
              placeholder="type to jarvis..."
              disabled={state !== "idle"}
              style={{
                flex: 1, background: "rgba(255,255,255,0.04)", border: "1px solid rgba(167,139,250,0.2)",
                borderRadius: 8, padding: "9px 12px", color: "rgba(240,237,232,0.9)",
                fontFamily: "var(--font-ui)", fontSize: 13, outline: "none",
              }}
            />
            <button
              type="submit"
              disabled={!textInput.trim() || state !== "idle"}
              style={{
                background: "rgba(109,40,217,0.5)", border: "1px solid rgba(167,139,250,0.3)",
                borderRadius: 8, padding: "9px 14px", color: "rgba(240,237,232,0.85)",
                fontSize: 12, cursor: "pointer", opacity: (!textInput.trim() || state !== "idle") ? 0.4 : 1,
              }}
            >send</button>
          </form>
        </div>
      )}

      {replyText && (
        <div style={{
          position: "fixed", bottom: 88, left: "50%", transform: "translateX(-50%)",
          maxWidth: "min(480px, 90vw)", zIndex: 50,
          background: "rgba(14,8,26,0.92)", border: "1px solid rgba(167,139,250,0.3)",
          borderRadius: 14, padding: "12px 18px",
          backdropFilter: "blur(16px)",
        }}>
          <div style={{ fontFamily: "var(--font-mono)", fontSize: 9, letterSpacing: "0.26em", color: "#a78bfa", marginBottom: 6 }}>Z.E.R.O</div>
          <div style={{ fontFamily: "var(--font-ui)", fontSize: 13, lineHeight: 1.55, color: "rgba(240,237,232,0.9)" }}>{replyText}</div>
        </div>
      )}

      {infoOpen && (
        <div style={{
          position: "fixed", top: "50%", left: "50%",
          transform: "translate(-50%, -50%)",
          width: "min(560px, 94vw)", maxHeight: "70vh",
          zIndex: 60,
          background: "rgba(10,6,20,0.97)", border: "1px solid rgba(167,139,250,0.3)",
          borderRadius: 18, display: "flex", flexDirection: "column",
          backdropFilter: "blur(24px)", overflow: "hidden",
        }}>
          {/* Header */}
          <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "12px 16px", borderBottom: "1px solid rgba(167,139,250,0.12)" }}>
            <button
              type="button"
              onClick={() => { setInfoTab("mail"); fetchEmails(); }}
              style={{
                background: infoTab === "mail" ? "rgba(109,40,217,0.5)" : "transparent",
                border: "1px solid rgba(167,139,250,0.2)", borderRadius: 8,
                padding: "5px 12px", color: "rgba(167,139,250,0.9)", fontSize: 12, cursor: "pointer",
              }}
            >✉ Mail</button>
            <button
              type="button"
              onClick={() => { setInfoTab("cal"); fetchCalendar(); }}
              style={{
                background: infoTab === "cal" ? "rgba(109,40,217,0.5)" : "transparent",
                border: "1px solid rgba(167,139,250,0.2)", borderRadius: 8,
                padding: "5px 12px", color: "rgba(167,139,250,0.9)", fontSize: 12, cursor: "pointer",
              }}
            >📅 Today</button>
            <div style={{ flex: 1 }} />
            <button
              type="button"
              onClick={() => setInfoOpen(false)}
              style={{ background: "none", border: "none", color: "rgba(167,139,250,0.5)", fontSize: 18, cursor: "pointer", lineHeight: 1 }}
            >×</button>
          </div>

          {/* Body */}
          <div style={{ overflow: "auto", flex: 1, padding: "12px 16px" }}>
            {infoTab === "mail" && (
              <MailPanel emails={emails} onReload={fetchEmails} onAsk={(q) => {
                setInfoOpen(false);
                setTextInput(q);
                setTextOpen(true);
                setTimeout(() => textInputRef.current?.focus(), 50);
              }} />
            )}
            {infoTab === "cal" && (
              <CalPanel calendar={calendar} onReload={fetchCalendar} />
            )}
          </div>
        </div>
      )}
    </>
  );
}

function MailPanel({ emails, onReload, onAsk }) {
  if (!emails) return <div style={{ color: "rgba(167,139,250,0.4)", fontSize: 12, textAlign: "center", padding: "24px 0" }}>Loading...</div>;

  const hasError = Object.values(emails).every(v => v?.error);
  if (hasError) {
    const firstErr = Object.values(emails)[0]?.error;
    if (firstErr === "not_authenticated") {
      return (
        <div style={{ color: "rgba(240,237,232,0.6)", fontSize: 12, padding: "12px 0" }}>
          <div style={{ color: "#a78bfa", marginBottom: 8 }}>Gmail not authenticated</div>
          <div>Send <code style={{ color: "#c4b5fd" }}>/mail auth paywise</code> via Telegram to authorize each account.</div>
        </div>
      );
    }
  }

  const allMsgs = [];
  for (const [acct, msgs] of Object.entries(emails)) {
    if (Array.isArray(msgs)) {
      msgs.forEach(m => allMsgs.push({ ...m, _acct: acct }));
    }
  }
  allMsgs.sort((a, b) => {
    const pa = { high: 0, medium: 1, low: 2 }[a.priority] ?? 1;
    const pb = { high: 0, medium: 1, low: 2 }[b.priority] ?? 1;
    return pa - pb;
  });

  if (!allMsgs.length) return (
    <div style={{ color: "rgba(167,139,250,0.4)", fontSize: 12, textAlign: "center", padding: "24px 0" }}>
      No unread emails.
      <button onClick={onReload} style={{ display: "block", margin: "8px auto 0", background: "none", border: "none", color: "#a78bfa", fontSize: 11, cursor: "pointer" }}>Refresh</button>
    </div>
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      {allMsgs.map((m) => (
        <div key={m.id} style={{
          padding: "8px 10px", borderRadius: 8,
          background: "rgba(167,139,250,0.04)", border: "1px solid rgba(167,139,250,0.08)",
          cursor: "pointer",
        }}
          onClick={() => onAsk(`/mail read ${m.id}`)}
        >
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 2 }}>
            <span style={{ fontSize: 11, color: m.priority === "high" ? "#f472b6" : "#a78bfa", fontFamily: "var(--font-mono)", letterSpacing: "0.1em" }}>
              {m._acct}{m.priority === "high" ? " !" : ""}
            </span>
            <span style={{ fontSize: 10, color: "rgba(167,139,250,0.4)" }}>{m.date ? new Date(m.date).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : ""}</span>
          </div>
          <div style={{ fontSize: 12, color: "rgba(240,237,232,0.5)", marginBottom: 2 }}>
            {(m.from || "").split("<")[0].trim()}
          </div>
          <div style={{ fontSize: 12, color: "rgba(240,237,232,0.85)", fontWeight: 500, marginBottom: 2 }}>{m.subject || "(no subject)"}</div>
          <div style={{ fontSize: 11, color: "rgba(167,139,250,0.5)", lineHeight: 1.4 }}>{(m.snippet || "").slice(0, 90)}</div>
        </div>
      ))}
      <button onClick={onReload} style={{ marginTop: 4, background: "none", border: "none", color: "rgba(167,139,250,0.4)", fontSize: 11, cursor: "pointer", textAlign: "left" }}>Refresh</button>
    </div>
  );
}

function CalPanel({ calendar, onReload }) {
  if (!calendar) return <div style={{ color: "rgba(167,139,250,0.4)", fontSize: 12, textAlign: "center", padding: "24px 0" }}>Loading...</div>;

  const events = [];
  for (const [acct, evts] of Object.entries(calendar)) {
    if (Array.isArray(evts)) evts.forEach(e => events.push({ ...e, _acct: acct }));
  }
  events.sort((a, b) => (a.start || "").localeCompare(b.start || ""));

  if (!events.length) return (
    <div style={{ color: "rgba(167,139,250,0.4)", fontSize: 12, textAlign: "center", padding: "24px 0" }}>
      Nothing on the calendar today.
      <button onClick={onReload} style={{ display: "block", margin: "8px auto 0", background: "none", border: "none", color: "#a78bfa", fontSize: 11, cursor: "pointer" }}>Refresh</button>
    </div>
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      {events.map((e, i) => (
        <div key={e.id || i} style={{
          padding: "8px 10px", borderRadius: 8,
          background: "rgba(167,139,250,0.04)", border: "1px solid rgba(167,139,250,0.08)",
        }}>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 2 }}>
            <span style={{ fontSize: 11, color: "#a78bfa", fontFamily: "var(--font-mono)" }}>{e._acct}</span>
            <span style={{ fontSize: 11, color: "rgba(167,139,250,0.5)" }}>
              {e.start ? new Date(e.start).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "all day"}
            </span>
          </div>
          <div style={{ fontSize: 13, color: "rgba(240,237,232,0.9)", fontWeight: 500 }}>{e.title}</div>
          {e.location && <div style={{ fontSize: 11, color: "rgba(167,139,250,0.5)", marginTop: 2 }}>{e.location}</div>}
        </div>
      ))}
      <button onClick={onReload} style={{ marginTop: 4, background: "none", border: "none", color: "rgba(167,139,250,0.4)", fontSize: 11, cursor: "pointer", textAlign: "left" }}>Refresh</button>
    </div>
  );
}
