import { NextRequest, NextResponse } from "next/server";

const BACKEND = "http://127.0.0.1:8080/api/projects";
const SECRET = process.env.JARVIS_TOKEN || "";

export async function GET(_req: NextRequest) {
  const headers: Record<string, string> = {};
  if (SECRET) headers["X-Jarvis-Token"] = SECRET;

  try {
    const res = await fetch(BACKEND, { headers, next: { revalidate: 0 } });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch {
    return NextResponse.json({ error: "backend unreachable", projects: [] }, { status: 502 });
  }
}
