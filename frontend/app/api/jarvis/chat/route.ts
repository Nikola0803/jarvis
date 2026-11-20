import { NextRequest, NextResponse } from "next/server";

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

  return new NextResponse(res.body, { status: res.status, headers: responseHeaders });
}
