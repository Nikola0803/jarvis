import { promises as fs } from "fs";
import path from "path";

const FILE = process.env.JARVIS_CONV_LOG || "/opt/jarvis/state/hud-conversations.jsonl";
const MAX_LINES = 400;

export type Turn = {
  ts: string;
  method: "voice" | "text";
  transcript?: string;
  reply: string;
};

export async function appendTurn(turn: Turn) {
  try {
    await fs.mkdir(path.dirname(FILE), { recursive: true });
    await fs.appendFile(FILE, JSON.stringify(turn) + "\n", "utf8");
    // Trim occasionally so the file never grows without bound.
    const raw = await fs.readFile(FILE, "utf8");
    const lines = raw.split("\n").filter(Boolean);
    if (lines.length > MAX_LINES + 50) {
      await fs.writeFile(FILE, lines.slice(-MAX_LINES).join("\n") + "\n", "utf8");
    }
  } catch {
    // logging must never break a voice turn
  }
}
