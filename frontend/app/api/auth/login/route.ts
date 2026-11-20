import { NextRequest, NextResponse } from "next/server";

export async function POST(req: NextRequest) {
  const { password } = await req.json().catch(() => ({ password: "" }));

  const expected = process.env.JARVIS_HUD_PASSWORD || "";
  const secret = process.env.JARVIS_TOKEN || "";

  if (!expected || !secret || password !== expected) {
    return NextResponse.json({ error: "access denied" }, { status: 401 });
  }

  const res = NextResponse.json({ ok: true });
  res.cookies.set("jarvis_auth", secret, {
    httpOnly: true,
    secure: false,
    sameSite: "lax",
    maxAge: 60 * 60 * 24 * 30, // 30 days
    path: "/",
  });
  return res;
}
