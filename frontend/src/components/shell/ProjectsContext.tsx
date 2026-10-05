import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

import { listProjects, type ProjectListItem } from "../../api/client";
import { NewProjectDialog } from "./NewProjectDialog";

type ProjectsState =
  | { kind: "loading" }
  | { kind: "ready"; projects: ProjectListItem[] }
  | { kind: "error"; message: string };

type ProjectsContextValue = {
  state: ProjectsState;
  reload: () => Promise<void>;
  /** Opens the "pick a task type" dialog that starts a new project. */
  startNewProject: () => void;
};

const ProjectsContext = createContext<ProjectsContextValue | null>(null);

/**
 * The project list backs the side navigation on every page, so it is loaded
 * once here; pages that create, rename or delete a project call `reload()`.
 */
export function ProjectsProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<ProjectsState>({ kind: "loading" });
  const [creating, setCreating] = useState(false);
  const startNewProject = useCallback(() => setCreating(true), []);
  const closeDialog = useCallback(() => setCreating(false), []);

  const reload = useCallback(async () => {
    try {
      setState({ kind: "ready", projects: await listProjects() });
    } catch (error) {
      setState({ kind: "error", message: error instanceof Error ? error.message : "未知错误" });
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  return (
    <ProjectsContext.Provider value={{ state, reload, startNewProject }}>
      {children}
      {creating && <NewProjectDialog onClose={closeDialog} />}
    </ProjectsContext.Provider>
  );
}

export function useProjects(): ProjectsContextValue {
  const value = useContext(ProjectsContext);
  if (!value) throw new Error("useProjects must be used inside ProjectsProvider");
  return value;
}
