import { NextRequest, NextResponse } from "next/server";

const BACKEND = "http://127.0.0.1:8080/api/emails";
const SECRET = process.env.JARVIS_TOKEN || "";

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const account = searchParams.get("account") || "all";
  const limit = searchParams.get("limit") || "10";
  const unread_only = searchParams.get("unread_only") ?? "true";

  const url = `${BACKEND}?account=${account}&limit=${limit}&unread_only=${unread_only}`;
  const headers: Record<string, string> = {};
  if (SECRET) headers["X-Jarvis-Token"] = SECRET;

  try {
    const res = await fetch(url, { headers, next: { revalidate: 0 } });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch {
    return NextResponse.json({ error: "backend unreachable" }, { status: 502 });
  }
}
