"use client";

import { useEffect, useRef, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const GOOGLE_CLIENT_ID = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID ?? "";

type Status = "checking" | "authenticated" | "unauthenticated";

declare global {
  interface Window {
    google?: {
      accounts: {
        id: {
          initialize: (config: {
            client_id: string;
            callback: (response: { credential: string }) => void;
          }) => void;
          renderButton: (parent: HTMLElement, options: Record<string, unknown>) => void;
        };
      };
    };
  }
}

export default function AuthGate({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<Status>("checking");
  const [error, setError] = useState("");
  const buttonRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetch(`${API_URL}/api/auth/me`, {
      credentials: "include",
      signal: controller.signal,
    })
      .then((response) => setStatus(response.ok ? "authenticated" : "unauthenticated"))
      .catch((requestError: Error) => {
        if (requestError.name !== "AbortError") setStatus("unauthenticated");
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (status !== "unauthenticated" || !GOOGLE_CLIENT_ID) return;

    const handleCredential = async (response: { credential: string }) => {
      setError("");
      try {
        const loginResponse = await fetch(`${API_URL}/api/auth/google`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({ credential: response.credential }),
        });
        const payload = (await loginResponse.json()) as { detail?: string };
        if (!loginResponse.ok) {
          throw new Error(payload.detail || "Вход отклонён.");
        }
        setStatus("authenticated");
      } catch (requestError) {
        setError((requestError as Error).message);
      }
    };

    const renderGoogleButton = () => {
      if (!window.google || !buttonRef.current) return;
      window.google.accounts.id.initialize({
        client_id: GOOGLE_CLIENT_ID,
        callback: handleCredential,
      });
      window.google.accounts.id.renderButton(buttonRef.current, {
        theme: "outline",
        size: "large",
        locale: "ru",
      });
    };

    if (window.google) {
      renderGoogleButton();
      return;
    }

    const script = document.createElement("script");
    script.src = "https://accounts.google.com/gsi/client";
    script.async = true;
    script.onload = renderGoogleButton;
    document.body.appendChild(script);
    return () => {
      script.onload = null;
    };
  }, [status]);

  if (status === "checking") {
    return <div className="auth-gate-loading">Проверяем вход…</div>;
  }

  if (status === "unauthenticated") {
    return (
      <div className="auth-gate">
        <div className="auth-gate-card">
          <h1>ФинКонтроль</h1>
          <p>Доступ только для владельца аккаунта.</p>
          {!GOOGLE_CLIENT_ID ? (
            <p className="auth-gate-error">
              GOOGLE_CLIENT_ID не настроен (NEXT_PUBLIC_GOOGLE_CLIENT_ID).
            </p>
          ) : (
            <div ref={buttonRef} />
          )}
          {error ? <p className="auth-gate-error">{error}</p> : null}
        </div>
      </div>
    );
  }

  return <>{children}</>;
}
