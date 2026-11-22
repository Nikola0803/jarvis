import type { Metadata } from "next";
import "./globals.css";
import "./hud-rails.css";

export const metadata: Metadata = {
  title: "Z.E.R.O",
  description: "Zero-latency Executive Reasoning Orchestrator -- Jarvis AI command center.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link
          href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300;400;500&family=Share+Tech+Mono&display=swap"
          rel="stylesheet"
        />
      </head>
      <body>{children}</body>
    </html>
  );
}
