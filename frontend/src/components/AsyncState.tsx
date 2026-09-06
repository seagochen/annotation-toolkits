import type { ReactNode } from "react";

export function LoadingState({ children = "正在读取项目…" }: { children?: ReactNode }) {
  return (
    <div className="state-panel" role="status">
      <span className="spinner" aria-hidden="true" />
      <p>{children}</p>
    </div>
  );
}

export function ErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div className="state-panel error-panel" role="alert">
      <p className="eyebrow">连接失败</p>
      <h2>暂时无法读取项目</h2>
      <p>{message}</p>
      <button type="button" onClick={onRetry ?? (() => window.location.reload())}>
        重新加载
      </button>
    </div>
  );
}
