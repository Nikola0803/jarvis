import { NextResponse } from "next/server";

// The HUD conversation feed. Both /api/jarvis/voice and /api/jarvis/chat append
// one line per turn to this file (empty user text for voice turns — STT text is
// not persisted by the backend). The side panels read the tail.
export const dynamic = "force-dynamic";

import { promises as fs } from "fs";
import path from "path";

const FILE = process.env.JARVIS_CONV_LOG || "/opt/jarvis/state/hud-conversations.jsonl";

export async function POST() {
  // not used; kept for type completeness
  return NextResponse.json({ error: "use GET" }, { status: 405 });
}

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const limit = Math.min(parseInt(searchParams.get("limit") || "12", 10) || 12, 50);
  try {
    const raw = await fs.readFile(FILE, "utf8");
    const lines = raw.split("\n").filter(Boolean);
    const turns = lines
      .slice(-limit)
      .map((l) => {
        try { return JSON.parse(l); } catch { return null; }
      })
      .filter(Boolean);
    return NextResponse.json({ turns, total: lines.length });
  } catch {
    return NextResponse.json({ turns: [], total: 0 });
  }
}
