import { useCallback, useEffect, useState } from "react";

import { ApiError, getProject, type ProjectDetail } from "../../api/client";

export type ProjectState =
  | { kind: "loading" }
  | { kind: "ready"; project: ProjectDetail }
  | { kind: "missing" }
  | { kind: "error"; message: string };

/** Loads one project for the dashboard pages; `reload` re-reads its status. */
export function useProject(projectId: string) {
  const [state, setState] = useState<ProjectState>({ kind: "loading" });

  const reload = useCallback(async () => {
    try {
      setState({ kind: "ready", project: await getProject(projectId) });
    } catch (error) {
      setState(
        error instanceof ApiError && error.status === 404
          ? { kind: "missing" }
          : { kind: "error", message: error instanceof Error ? error.message : "未知错误" },
      );
    }
  }, [projectId]);

  useEffect(() => {
    setState({ kind: "loading" });
    void reload();
  }, [reload]);

  return { state, reload };
}
