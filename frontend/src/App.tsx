import { Link, Route, Switch, useRoute } from "wouter";

import { CanvasDemoPage } from "./pages/CanvasDemoPage";
import { ProjectDetailPage } from "./pages/ProjectDetailPage";
import { ProjectListPage } from "./pages/ProjectListPage";
import { CaptionReviewPage } from "./tasks/caption/CaptionReviewPage";
import { ClassificationReviewPage } from "./tasks/classification/ClassificationReviewPage";
import { DepthReviewPage } from "./tasks/depth/DepthReviewPage";
import { DetectionReviewPage } from "./tasks/detection/DetectionReviewPage";
import { ReIDReviewPage } from "./tasks/reid/ReIDReviewPage";
import { SegmentationReviewPage } from "./tasks/segmentation/SegmentationReviewPage";

function NotFoundPage() {
  return (
    <section className="state-panel">
      <p className="eyebrow">404</p>
      <h2>页面不存在</h2>
      <Link className="text-link" to="/">
        返回项目列表
      </Link>
    </section>
  );
}

export function App() {
  // Task pages (/projects/:id/<task>) are full-viewport tools; everything
  // else is a centred document page.
  const [isTaskPage] = useRoute("/projects/:projectId/:task");
  return (
    <div className="app-shell">
      <header className="site-header">
        <Link className="brand" to="/" aria-label="Annotation Toolkits 首页">
          <span className="brand-mark">AT</span>
          <span>
            <strong>Annotation Toolkits</strong>
            <small>本地标注工作台</small>
          </span>
        </Link>
      </header>
      <main className={isTaskPage ? "main-wide" : undefined}>
        <Switch>
          <Route path="/" component={ProjectListPage} />
          <Route path="/canvas-demo" component={CanvasDemoPage} />
          <Route path="/projects/:projectId/classify" component={ClassificationReviewPage} />
          <Route path="/projects/:projectId/caption" component={CaptionReviewPage} />
          <Route path="/projects/:projectId/detect" component={DetectionReviewPage} />
          <Route path="/projects/:projectId/segment" component={SegmentationReviewPage} />
          <Route path="/projects/:projectId/depth" component={DepthReviewPage} />
          <Route path="/projects/:projectId/review" component={ReIDReviewPage} />
          <Route path="/projects/:projectId" component={ProjectDetailPage} />
          <Route component={NotFoundPage} />
        </Switch>
      </main>
    </div>
  );
}
