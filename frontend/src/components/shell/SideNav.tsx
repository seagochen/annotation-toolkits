import { useEffect, useState, type ReactNode } from "react";
import { Link, useLocation } from "wouter";

import { taskEntries, taskLabel } from "../../project-meta";
import { categoryColor } from "../workspace/palette";
import {
  AnnotateIcon,
  CollapseIcon,
  ExportIcon,
  ImportIcon,
  OverviewIcon,
  PlusIcon,
  SettingsIcon,
} from "./icons";
import { useProjects } from "./ProjectsContext";

const COLLAPSE_KEY = "annotation-toolkits.nav-collapsed";
const TYPE_ORDER = Object.keys(taskEntries);

function readCollapsed(): boolean {
  try {
    return window.localStorage.getItem(COLLAPSE_KEY) === "1";
  } catch {
    return false;
  }
}

function NavLink({
  to,
  icon,
  label,
  active,
  collapsed,
}: {
  to: string;
  icon: ReactNode;
  label: string;
  active: boolean;
  collapsed: boolean;
}) {
  return (
    <Link
      aria-current={active ? "page" : undefined}
      className={active ? "sidenav-link active" : "sidenav-link"}
      title={collapsed ? label : undefined}
      to={to}
    >
      {icon}
      <span className="sidenav-label">{label}</span>
    </Link>
  );
}

/** Project sections, in the order work on a project usually goes. */
export function projectSections(projectId: string, taskType: string) {
  const base = `/projects/${projectId}`;
  const entry = taskEntries[taskType];
  return [
    { to: base, label: "概览", icon: <OverviewIcon /> },
    { to: `${base}/import`, label: "导入数据", icon: <ImportIcon /> },
    ...(entry
      ? [{ to: `${base}/${entry.path}`, label: taskType === "reid" ? "审核" : "标注", icon: <AnnotateIcon /> }]
      : []),
    { to: `${base}/settings`, label: "属性", icon: <SettingsIcon /> },
    { to: `${base}/export`, label: "导出", icon: <ExportIcon /> },
  ];
}

/**
 * `compact` (annotation pages) shows the icon rail whatever the saved
 * preference, so the annotation workspace keeps the full width.
 */
export function SideNav({ compact = false }: { compact?: boolean }) {
  const { state, startNewProject } = useProjects();
  const [location] = useLocation();
  const [preferCollapsed, setCollapsed] = useState(readCollapsed);
  const collapsed = preferCollapsed || compact;

  useEffect(() => {
    try {
      window.localStorage.setItem(COLLAPSE_KEY, preferCollapsed ? "1" : "0");
    } catch {
      // Private mode / blocked storage: the toggle still works for this visit.
    }
  }, [preferCollapsed]);

  const activeId = /^\/projects\/([^/]+)/.exec(location)?.[1];
  const projects = state.kind === "ready" ? state.projects : [];

  return (
    <aside className={collapsed ? "sidenav collapsed" : "sidenav"}>
      <div className="sidenav-top">
        <Link aria-label="Annotation Toolkits 首页" className="sidenav-brand" to="/">
          <span className="brand-mark">AT</span>
          <span className="sidenav-label">Annotation Toolkits</span>
        </Link>
        {!compact && (
          <button
            aria-label={collapsed ? "展开导航" : "收起导航"}
            className="sidenav-collapse"
            onClick={() => setCollapsed((current) => !current)}
            type="button"
          >
            <CollapseIcon collapsed={collapsed} />
          </button>
        )}
      </div>

      <button
        className="sidenav-new"
        onClick={startNewProject}
        title={collapsed ? "新建项目" : undefined}
        type="button"
      >
        <PlusIcon />
        <span className="sidenav-label">新建项目</span>
      </button>

      <nav aria-label="项目" className="sidenav-projects">
        <p className="sidenav-heading">项目</p>
        {state.kind === "loading" && <p className="sidenav-note">正在读取…</p>}
        {state.kind === "error" && (
          <p className="sidenav-note sidenav-error" role="alert" title={state.message}>
            项目读取失败
          </p>
        )}
        {state.kind === "ready" && projects.length === 0 && <p className="sidenav-note">还没有项目</p>}
        <ul>
          {projects.map((project) => {
            const isActive = project.id === activeId;
            return (
              <li key={project.id}>
                <Link
                  aria-current={isActive && location === `/projects/${project.id}` ? "page" : undefined}
                  className={isActive ? "sidenav-project active" : "sidenav-project"}
                  title={collapsed ? project.name : project.id}
                  to={`/projects/${project.id}`}
                >
                  <span
                    aria-hidden="true"
                    className="project-avatar"
                    style={{ background: categoryColor(TYPE_ORDER.indexOf(project.task_type)) }}
                  >
                    {project.name.trim().charAt(0).toUpperCase() || "?"}
                  </span>
                  <span className="sidenav-label sidenav-project-text">
                    <span className="sidenav-project-name">{project.name}</span>
                    <span className="sidenav-project-type">{taskLabel(project.task_type)}</span>
                  </span>
                </Link>
                {isActive && (
                  <div className="sidenav-sections">
                    {projectSections(project.id, project.task_type).map((section) => (
                      <NavLink
                        active={location === section.to}
                        collapsed={collapsed}
                        icon={section.icon}
                        key={section.to}
                        label={section.label}
                        to={section.to}
                      />
                    ))}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      </nav>
    </aside>
  );
}
