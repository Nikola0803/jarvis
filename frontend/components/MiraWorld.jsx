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
import { startMic, stopMic, micLevel, getMicStream } from "../lib/micLevel";
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
  const [textInput, setTextInput] = useState("");
  const [replyText, setReplyText] = useState("");
  const stateRef = useRef(state);
  const isRealAudio = useRef(false);
  const recorderRef = useRef(null);
  const chunksRef = useRef([]);
  const replyTimer = useRef(null);
  const audioCtxRef = useRef(null);
  const textInputRef = useRef(null);
  stateRef.current = state;

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

  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const apply = () => setReducedMotion(mq.matches);
    apply();
    mq.addEventListener("change", apply);
    return () => mq.removeEventListener("change", apply);
  }, []);

  useEffect(() => { enterMira({ instant: true }); }, []);

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

  // Start mic for visualization when listening; cleanup on unmount
  useEffect(() => {
    if (state === "listening") startMic();
  }, [state]);
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
        isRealAudio.current = false;
        setVoiceEnergy("output", 0);
        URL.revokeObjectURL(url);
        audioCtxRef.current?.close().catch(() => {});
        audioCtxRef.current = null;
        resolve();
      };
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
        isRealAudio.current = false;
        setVoiceEnergy("output", 0);
        URL.revokeObjectURL(url);
        resolve();
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
    setState("idle");
  }

  async function handleOrbTap() {
    const s = stateRef.current;

    if (s === "idle") {
      await startMic();
      const micStream = getMicStream();
      chunksRef.current = [];
      if (micStream) {
        const mimeType = MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" : "audio/mp4";
        const mr = new MediaRecorder(micStream, { mimeType });
        mr.ondataavailable = (e) => { if (e.data.size > 0) chunksRef.current.push(e.data); };
        mr.start(100);
        recorderRef.current = mr;
      }
      setState("listening");

    } else if (s === "listening") {
      unlockAudio(); // must run synchronously in gesture handler before any async code
      const mr = recorderRef.current;
      setState("thinking");
      let blob;
      if (mr) {
        blob = await new Promise((res) => {
          mr.onstop = () => res(new Blob(chunksRef.current, { type: mr.mimeType || "audio/webm" }));
          mr.stop();
        });
      }
      stopMic();
      recorderRef.current = null;

      if (!blob || blob.size < 1000) { setState("idle"); return; }

      try {
        const form = new FormData();
        const ext = (blob.type || "").includes("mp4") ? "voice.mp4" : "voice.webm";
        form.append("audio", blob, ext);
        const res = await fetch("/api/jarvis/voice", { method: "POST", body: form });
        if (res.ok && res.headers.get("content-type")?.includes("audio")) {
          const reply = res.headers.get("X-Reply");
          if (reply) { setReplyText(reply); clearTimeout(replyTimer.current); replyTimer.current = setTimeout(() => setReplyText(""), 10000); }
          const canStream = typeof window.MediaSource !== "undefined" && MediaSource.isTypeSupported("audio/mpeg");
          if (canStream) {
            await playStreamingAudio(res);
          } else {
            const buf = await res.arrayBuffer();
            const mp3 = new Blob([buf], { type: "audio/mpeg" });
            await playAudio(mp3);
          }
          setReplyText("");
        } else {
          const err = await res.json().catch(() => ({}));
          if (err.reply) {
            setReplyText(err.reply);
            clearTimeout(replyTimer.current);
            replyTimer.current = setTimeout(() => setReplyText(""), 10000);
          }
        }
      } catch (err) {
        console.error("Z.E.R.O voice pipeline:", err);
      }
      setState("idle");

    } else if (s === "speaking") {
      isRealAudio.current = false;
      setVoiceEnergy("output", 0);
      setState("idle");
    }
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
          <MiraCore3D state="idle" />
        </div>
      )}

      <MiraStatusBar state={state} />

      <button
        type="button"
        onClick={handleOrbTap}
        aria-label={`Z.E.R.O is ${LABEL[state]}. ${state === "idle" ? "Tap to speak." : state === "listening" ? "Tap to send." : state === "speaking" ? "Tap to stop." : "Processing..."}`}
        title={state === "idle" ? "Tap to speak" : state === "listening" ? "Tap to send" : state === "thinking" ? "Processing..." : "Tap to stop"}
        className="mira-tap"
      />

      {dialOpen
        ? <MiraDial onClose={() => setDialOpen(false)} state={state} onState={setState} />
        : (
          <div style={{ position: "fixed", bottom: 24, right: 24, zIndex: 50, display: "flex", gap: 8, alignItems: "center" }}>
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
    </>
  );
}
