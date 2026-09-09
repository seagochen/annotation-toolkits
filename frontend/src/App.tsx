import { Link, Route, Switch } from "wouter";

import { CanvasDemoPage } from "./pages/CanvasDemoPage";
import { ProjectDetailPage } from "./pages/ProjectDetailPage";
import { ProjectListPage } from "./pages/ProjectListPage";
import { CaptionReviewPage } from "./tasks/caption/CaptionReviewPage";
import { ClassificationReviewPage } from "./tasks/classification/ClassificationReviewPage";
import { ReIDReviewPage } from "./tasks/reid/ReIDReviewPage";

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
  return (
    <div className="app-shell">
      <header className="site-header">
        <Link className="brand" to="/" aria-label="Annotation Toolkits 首页">
          <span className="brand-mark">AT</span>
          <span>
            <strong>Annotation Toolkits</strong>
            <small>Local annotation workspace</small>
          </span>
        </Link>
        <nav className="site-nav" aria-label="主导航">
          <Link className="text-link" to="/canvas-demo">画布 Demo</Link>
          <span className="local-badge">本地模式</span>
        </nav>
      </header>
      <main>
        <Switch>
          <Route path="/" component={ProjectListPage} />
          <Route path="/canvas-demo" component={CanvasDemoPage} />
          <Route path="/projects/:projectId/classify" component={ClassificationReviewPage} />
          <Route path="/projects/:projectId/caption" component={CaptionReviewPage} />
          <Route path="/projects/:projectId/review" component={ReIDReviewPage} />
          <Route path="/projects/:projectId" component={ProjectDetailPage} />
          <Route component={NotFoundPage} />
        </Switch>
      </main>
    </div>
  );
}
