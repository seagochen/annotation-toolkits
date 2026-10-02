import type { ReactNode } from "react";

export function LoadingState({ children = "正在读取项目…" }: { children?: ReactNode }) {
  return (
    <div className="state-panel" role="status">
      <span className="spinner" aria-hidden="true" />
      <p>{children}</p>
    </div>
  );
}

/**
 * Not "connection failed": most errors that reach here are the server
 * answering (an invalid projects.yaml, a wrong task type), and blaming the
 * network sends people looking in the wrong place.
 */
export function ErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div className="state-panel error-panel" role="alert">
      <p className="eyebrow">加载失败</p>
      <h2>无法读取数据</h2>
      <p>{message}</p>
      <button type="button" onClick={onRetry ?? (() => window.location.reload())}>
        重新加载
      </button>
    </div>
  );
}
