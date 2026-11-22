import { NextRequest, NextResponse } from "next/server";
import { appendTurn } from "../../../../lib/conversationLog";

// Server-side proxy: browser never sees the secret token
const BACKEND = "http://127.0.0.1:8080/api/voice";
const SECRET = process.env.JARVIS_TOKEN || "";

export async function POST(req: NextRequest) {
  const formData = await req.formData();

  const headers: Record<string, string> = {};
  if (SECRET) headers["X-Jarvis-Token"] = SECRET;

  const res = await fetch(BACKEND, {
    method: "POST",
    headers,
    body: formData,
  });

  const responseHeaders = new Headers();

  // Forward audio content type and custom headers
  const ct = res.headers.get("content-type");
  if (ct) responseHeaders.set("content-type", ct);
  const xReply = res.headers.get("x-reply");
  if (xReply) responseHeaders.set("x-reply", xReply);
  const xTranscript = res.headers.get("x-transcript");
  if (xTranscript) responseHeaders.set("x-transcript", xTranscript);

  // Persist the turn for the HUD conversation panel (fire-and-forget).
  // X-Reply only carries the first ~200 chars (ascii-sanitised), which is what
  // the HUD needs for a glanceable recap.
  if (ct?.includes("audio") && xReply) {
    const decode = (s: string) => Buffer.from(s, "latin1").toString("utf8");
    const transcript = xTranscript ? decode(xTranscript) : "";
    void appendTurn({
      ts: new Date().toISOString(),
      method: "voice",
      transcript,
      reply: decode(xReply),
    });
  }

  // Pipe the body stream directly -- no buffering, audio starts playing sooner
  return new NextResponse(res.body, { status: res.status, headers: responseHeaders });
}
