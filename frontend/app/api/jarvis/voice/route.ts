import { NextRequest, NextResponse } from "next/server";

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

  const body = await res.arrayBuffer();
  const responseHeaders = new Headers();

  // Forward audio content type and custom headers
  const ct = res.headers.get("content-type");
  if (ct) responseHeaders.set("content-type", ct);
  const xReply = res.headers.get("x-reply");
  if (xReply) responseHeaders.set("x-reply", xReply);
  const xTranscript = res.headers.get("x-transcript");
  if (xTranscript) responseHeaders.set("x-transcript", xTranscript);

  return new NextResponse(body, { status: res.status, headers: responseHeaders });
}
