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
  const stateRef = useRef(state);
  const isRealAudio = useRef(false);
  const recorderRef = useRef(null);
  const chunksRef = useRef([]);
  stateRef.current = state;

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
      let ctx, analyser, buf, rafId;

      audio.onplay = () => {
        try {
          ctx = new AudioContext();
          const src = ctx.createMediaElementSource(audio);
          analyser = ctx.createAnalyser();
          analyser.fftSize = 512;
          buf = new Float32Array(analyser.fftSize);
          src.connect(analyser);
          src.connect(ctx.destination);
          isRealAudio.current = true;
          setState("speaking");
          const measure = () => {
            if (!analyser || stateRef.current !== "speaking") return;
            analyser.getFloatTimeDomainData(buf);
            let sum = 0;
            for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
            setVoiceEnergy("output", Math.min(1, Math.sqrt(sum / buf.length) * 12));
            rafId = requestAnimationFrame(measure);
          };
          rafId = requestAnimationFrame(measure);
        } catch (_) {}
      };

      const done = () => {
        isRealAudio.current = false;
        if (rafId) cancelAnimationFrame(rafId);
        setVoiceEnergy("output", 0);
        ctx?.close().catch(() => {});
        URL.revokeObjectURL(url);
        resolve();
      };
      audio.onended = done;
      audio.onerror = done;
      audio.play().catch(done);
    });
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
          await playAudio(await res.blob());
        } else {
          const err = await res.json().catch(() => ({}));
          console.warn("Z.E.R.O voice error:", err);
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
        : <button type="button" className="dial-pill" onClick={() => setDialOpen(true)} aria-label="Open the design dial">Design dial</button>}
    </>
  );
}
