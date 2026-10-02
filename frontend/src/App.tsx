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
import { CaptionReviewPage } from "./tasks/caption/CaptionReviewPage";
import { ClassificationReviewPage } from "./tasks/classification/ClassificationReviewPage";
import { DepthReviewPage } from "./tasks/depth/DepthReviewPage";
import { DetectionReviewPage } from "./tasks/detection/DetectionReviewPage";
import { ReIDReviewPage } from "./tasks/reid/ReIDReviewPage";
import { SegmentationReviewPage } from "./tasks/segmentation/SegmentationReviewPage";

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
  return (
    <ProjectsProvider>
      <div className="app-shell">
        <SideNav />
        <main className={ANNOTATION_PATH.test(location) ? "main-wide" : undefined}>
          <Switch>
            <Route path="/" component={HomePage} />
            <Route path="/new/:taskType" component={ProjectCreatePage} />
            <Route path="/canvas-demo" component={CanvasDemoPage} />
            <Route path="/projects/:projectId/import" component={ProjectImportPage} />
            <Route path="/projects/:projectId/settings" component={ProjectSettingsPage} />
            <Route path="/projects/:projectId/export" component={ProjectExportPage} />
            <Route path="/projects/:projectId/classify" component={ClassificationReviewPage} />
            <Route path="/projects/:projectId/caption" component={CaptionReviewPage} />
            <Route path="/projects/:projectId/detect" component={DetectionReviewPage} />
            <Route path="/projects/:projectId/segment" component={SegmentationReviewPage} />
            <Route path="/projects/:projectId/depth" component={DepthReviewPage} />
            <Route path="/projects/:projectId/review" component={ReIDReviewPage} />
            <Route path="/projects/:projectId" component={ProjectOverviewPage} />
            <Route component={NotFoundPage} />
          </Switch>
        </main>
      </div>
    </ProjectsProvider>
  );
}
