import { useEffect, useState, type ReactNode } from "react";

/** Standalone starts directly; hosted verifies the shared account over HTTP. */
export function HostedSession({ children }: { children: ReactNode }) {
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  useEffect(() => {
    let cancelled = false;
    async function start() {
      const config = await fetch("/runtime-config", { cache: "no-store" });
      if (config.status === 404) {
        if (!cancelled) setState("ready");
        return;
      }
      if (!config.ok) throw new Error("Runtime configuration unavailable");
      const runtime = await config.json();
      if (runtime.mode === "standalone") {
        if (!cancelled) setState("ready");
        return;
      }
      if (runtime.mode !== "hosted") throw new Error("Invalid runtime configuration");
      const identity = await fetch("/auth/platform/me", { cache: "no-store" });
      if (cancelled) return;
      if (identity.status === 401) {
        const path = window.location.pathname + window.location.search + window.location.hash;
        window.location.assign("/auth/platform/login?next=" + encodeURIComponent(path));
      } else if (identity.ok) setState("ready");
      else throw new Error("Identity service unavailable");
    }
    start().catch(() => { if (!cancelled) setState("error"); });
    return () => { cancelled = true; };
  }, []);
  if (state === "ready") return children;
  return <main className="page">
    <p>{state === "loading" ? "正在确认登录状态…" : "暂时无法连接账号服务，请稍后重试。"}</p>
    {state === "error" && <button onClick={() => window.location.reload()}>重试</button>}
  </main>;
}
