import { Link, Route, Switch, useLocation } from "wouter";

import { ProjectsProvider } from "./components/shell/ProjectsContext";
import { SideNav } from "./components/shell/SideNav";
import { CanvasDemoPage } from "./pages/CanvasDemoPage";
import { HomePage } from "./pages/HomePage";
import { ProjectCreatePage } from "./pages/ProjectCreatePage";
import { ProjectExportPage } from "./pages/project/ProjectExportPage";
import { ProjectImportPage } from "./pages/project/ProjectImportPage";
import { ProjectOverviewPage } from "./pages/project/ProjectOverviewPage";
import { ProjectSettingsPage } from "./pages/project/ProjectSettingsPage";
import { taskEntries } from "./project-meta";
import { taskPages } from "./img-annotation/pages";

const ANNOTATION_PATH = new RegExp(
  `^/projects/[^/]+/(${Object.values(taskEntries).map((entry) => entry.path).join("|")})$`,
);

function NotFoundPage() {
  return (
    <section className="state-panel">
      <p className="eyebrow">404</p>
      <h2>页面不存在</h2>
      <Link className="text-link" to="/">
        返回首页
      </Link>
    </section>
  );
}

export function App() {
  // Annotation pages are full-bleed tools; every other page is a padded
  // document in the work area.
  const [location] = useLocation();
  const annotating = ANNOTATION_PATH.test(location);
  return (
    <ProjectsProvider>
      <div className="app-shell">
        <SideNav compact={annotating} />
        <main className={annotating ? "main-wide" : undefined}>
          <Switch>
            <Route path="/" component={HomePage} />
            <Route path="/new/:taskType" component={ProjectCreatePage} />
            <Route path="/canvas-demo" component={CanvasDemoPage} />
            <Route path="/projects/:projectId/import" component={ProjectImportPage} />
            <Route path="/projects/:projectId/settings" component={ProjectSettingsPage} />
            <Route path="/projects/:projectId/export" component={ProjectExportPage} />
            {Object.entries(taskPages).map(([taskType, page]) => (
              <Route
                component={page}
                key={taskType}
                path={`/projects/:projectId/${taskEntries[taskType].path}`}
              />
            ))}
            <Route path="/projects/:projectId" component={ProjectOverviewPage} />
            <Route component={NotFoundPage} />
          </Switch>
        </main>
      </div>
    </ProjectsProvider>
  );
}
