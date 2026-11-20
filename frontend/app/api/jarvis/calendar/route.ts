import { NextRequest, NextResponse } from "next/server";

const BACKEND = "http://127.0.0.1:8080/api/calendar";
const SECRET = process.env.JARVIS_TOKEN || "";

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const hours = searchParams.get("hours") || "24";

  const url = `${BACKEND}?hours=${hours}`;
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
