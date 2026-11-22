import { NextRequest, NextResponse } from "next/server";
import { appendTurn } from "../../../../lib/conversationLog";

const BACKEND = "http://127.0.0.1:8080/api/chat";
const SECRET = process.env.JARVIS_TOKEN || "";

export async function POST(req: NextRequest) {
  const body = await req.json();

  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (SECRET) headers["X-Jarvis-Token"] = SECRET;

  const res = await fetch(BACKEND, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });

  const responseHeaders = new Headers();
  const ct = res.headers.get("content-type");
  if (ct) responseHeaders.set("content-type", ct);
  const xReply = res.headers.get("x-reply");
  if (xReply) responseHeaders.set("x-reply", xReply);
  const xTranscript = res.headers.get("x-transcript");
  if (xTranscript) responseHeaders.set("x-transcript", xTranscript);

  if (xReply) {
    void appendTurn({
      ts: new Date().toISOString(),
      method: "text",
      transcript: (body.text || "").slice(0, 500),
      reply: Buffer.from(xReply, "latin1").toString("utf8"),
    });
  }

  return new NextResponse(res.body, { status: res.status, headers: responseHeaders });
}
