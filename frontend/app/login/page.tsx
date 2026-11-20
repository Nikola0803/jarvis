"use client";
import { useState, useRef, useEffect } from "react";
import { useRouter } from "next/navigation";

export default function LoginPage() {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const router = useRouter();

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError("");
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password }),
      });
      if (res.ok) {
        router.replace("/");
      } else {
        setError("access denied");
        setPassword("");
        inputRef.current?.focus();
      }
    } catch {
      setError("connection error");
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <style>{`
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { background: #06030d; }
        .wrap {
          min-height: 100dvh;
          display: flex;
          align-items: center;
          justify-content: center;
          background: radial-gradient(ellipse 70% 60% at 50% 46%, #0d0718 0%, #06030d 60%, #030108 100%);
          font-family: 'Space Grotesk', system-ui, sans-serif;
        }
        .card {
          width: min(340px, 90vw);
          display: flex;
          flex-direction: column;
          align-items: center;
          gap: 32px;
        }
        .label {
          font-size: 9px;
          letter-spacing: 0.28em;
          color: #a78bfa;
          text-transform: uppercase;
        }
        .orb {
          width: 56px; height: 56px; border-radius: 50%;
          background: radial-gradient(circle at 38% 36%, #c4b5fd 0%, #7c3aed 48%, #3b0764 100%);
          box-shadow: 0 0 32px 8px rgba(139,92,246,0.25), 0 0 0 1px rgba(167,139,250,0.15);
        }
        form { width: 100%; display: flex; flex-direction: column; gap: 12px; }
        input[type=password] {
          width: 100%;
          background: rgba(14,8,26,0.85);
          border: 1px solid rgba(167,139,250,0.2);
          border-radius: 10px;
          padding: 13px 16px;
          font-family: 'Space Grotesk', monospace;
          font-size: 15px;
          color: rgba(240,237,232,0.9);
          outline: none;
          transition: border-color 0.15s;
          letter-spacing: 0.08em;
        }
        input[type=password]::placeholder { color: rgba(167,139,250,0.3); letter-spacing: 0.04em; }
        input[type=password]:focus { border-color: rgba(167,139,250,0.5); }
        button[type=submit] {
          width: 100%;
          padding: 13px;
          background: rgba(109,40,217,0.6);
          border: 1px solid rgba(167,139,250,0.3);
          border-radius: 10px;
          font-family: 'Space Grotesk', sans-serif;
          font-size: 11px;
          letter-spacing: 0.22em;
          color: rgba(240,237,232,0.85);
          cursor: pointer;
          text-transform: uppercase;
          transition: background 0.15s, opacity 0.15s;
        }
        button[type=submit]:hover { background: rgba(109,40,217,0.8); }
        button[type=submit]:disabled { opacity: 0.4; cursor: not-allowed; }
        .error {
          font-size: 10px;
          letter-spacing: 0.18em;
          color: rgba(248,113,113,0.8);
          text-align: center;
          text-transform: uppercase;
          min-height: 14px;
        }
      `}</style>
      <div className="wrap">
        <div className="card">
          <div className="orb" aria-hidden="true" />
          <span className="label">Z.E.R.O access</span>
          <form onSubmit={handleSubmit}>
            <input
              ref={inputRef}
              type="password"
              placeholder="passphrase"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
            <button type="submit" disabled={loading || !password}>
              {loading ? "verifying..." : "enter"}
            </button>
          </form>
          <span className="error">{error}</span>
        </div>
      </div>
    </>
  );
}
