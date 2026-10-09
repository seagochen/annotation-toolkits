import { useEffect, useState } from "react";
import { legacyHistory } from "../../api/client";

/** Original annotations and AI decisions remain available after conversion. */
export function LegacyHistory({ projectId }: { projectId: string }) {
  const [history, setHistory] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    setHistory(null);
    setFailed(false);
    legacyHistory(projectId, controller.signal)
      .then((records) => {
        if (!controller.signal.aborted) setHistory(records);
      }).catch(() => { if (!controller.signal.aborted) setFailed(true); });
    return () => controller.abort();
  }, [projectId]);
  if (failed) return <p role="alert">暂时无法读取迁移前的历史记录。</p>;
  if (history === null) return null;
  return <article className="card">
    <h2>迁移前的项目历史</h2>
    <p>原始标注、版本和 AI 建议保留在这里。当前编辑请使用上方标注工作台。</p>
    <details><summary>查看原始记录</summary><pre style={{ overflowX: "auto", maxHeight: "30rem" }}>{history}</pre></details>
  </article>;
}
