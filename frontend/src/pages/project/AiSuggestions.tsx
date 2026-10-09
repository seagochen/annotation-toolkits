import { useEffect, useRef, useState } from "react";
import { hostedRequest } from "../../api/client";

type Input = { id: string; itemId: string; engine: string; confidence: number };
type Job = { id: string; request: Input; status: string; sourceRevision: number;
  result: { artifact: unknown; polygons: unknown[] } | null };
type Item = { item_id: string; image_path: string };

/** Human approval is separate from run completion; stale annotations are rejected. */
export function AiSuggestions({ projectId, onApplied }: { projectId: string; onApplied: () => void }) {
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [items, setItems] = useState<Item[]>([]);
  const [itemId, setItemId] = useState("");
  const [engine, setEngine] = useState("owlv2");
  const [confidence, setConfidence] = useState(0.25);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const currentProject = useRef(projectId);
  currentProject.current = projectId;
  const base = `/api/projects/${encodeURIComponent(projectId)}`;
  async function load(signal?: AbortSignal) {
    const result = await hostedRequest<{ items: Job[] }>(base + "/ai-jobs", undefined, signal);
    if (signal?.aborted || currentProject.current !== projectId) return;
    setJobs(result?.items ?? null);
    if (result !== null) {
      const queue = await hostedRequest<{ items: Item[] }>(base + "/queue?limit=200", undefined, signal);
      if (signal?.aborted || currentProject.current !== projectId) return;
      setItems(queue?.items ?? []);
      setItemId((prior) => prior || queue?.items[0]?.item_id || "");
    }
  }
  useEffect(() => {
    const controller = new AbortController();
    setJobs(null); setError(""); setItemId(""); setItems([]); setBusy(false);
    load(controller.signal).catch((reason) => { if (!controller.signal.aborted) setError(String(reason.message)); });
    return () => controller.abort();
  }, [projectId]);
  async function action(path: string, body: unknown, applied = false) {
    setBusy(true); setError("");
    try {
      await hostedRequest(path, body);
      if (currentProject.current !== projectId) return;
      await load();
      if (applied && currentProject.current === projectId) onApplied();
    } catch (reason) {
      if (currentProject.current !== projectId) return;
      setError(reason instanceof Error ? reason.message : "AI 请求失败");
      // A lost submission reply may have left a recoverable job in the app.
      await load().catch(() => undefined);
    } finally { if (currentProject.current === projectId) setBusy(false); }
  }
  if (jobs === null && !error) return null;
  return <article className="card">
    <h2>AI 标注建议</h2>
    <p>选择图片生成建议，查看结果后再应用。已有标注变化时，需要重新生成建议。</p>
    {error && <p role="alert">{error}</p>}
    {jobs !== null && <>
      <label>图片<select value={itemId} onChange={(event) => setItemId(event.target.value)}>
        {items.map((item) => <option key={item.item_id} value={item.item_id}>{item.image_path}</option>)}
      </select></label>
      <label>模型<select value={engine} onChange={(event) => setEngine(event.target.value)}>
        <option value="owlv2">OWL-V2</option><option value="rfdetr-detect">RF-DETR</option>
      </select></label>
      <label>置信度<input type="number" min="0" max="1" step="0.05" value={confidence}
        onChange={(event) => setConfidence(Number(event.target.value))} /></label>
      <button disabled={busy || !itemId} onClick={() => void action(base + "/ai-jobs",
        { id: crypto.randomUUID(), itemId, engine, confidence })}>生成建议</button>
      {jobs.map((job) => <section key={job.id}>
        <p>{items.find((item) => item.item_id === job.request.itemId)?.image_path ?? job.request.itemId} · {job.request.engine} · {job.status}</p>
        {job.result && <details><summary>查看建议（{job.result.polygons.length} 个）</summary>
          <pre style={{ overflowX: "auto", maxHeight: "20rem" }}>{JSON.stringify(job.result.artifact, null, 2)}</pre></details>}
        {job.status === "running" && <button disabled={busy} onClick={() => void action(`${base}/ai-jobs/${job.id}/refresh`, {})}>刷新结果</button>}
        {job.status === "submitting" && <button disabled={busy} onClick={() => void action(base + "/ai-jobs",
          { id: job.id, itemId: job.request.itemId, engine: job.request.engine, confidence: job.request.confidence })}>恢复提交</button>}
        {job.status === "ready" && <>
          <button disabled={busy} onClick={() => void action(`${base}/ai-jobs/${job.id}/apply`, {}, true)}>应用建议并继续编辑</button>
          <button disabled={busy} onClick={() => void action(`${base}/ai-jobs/${job.id}/reject`, {})}>拒绝建议</button>
        </>}
      </section>)}
    </>}
  </article>;
}
