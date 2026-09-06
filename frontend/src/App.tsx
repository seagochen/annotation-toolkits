import { Link, Route, Switch } from "wouter";

import { ProjectDetailPage } from "./pages/ProjectDetailPage";
import { ProjectListPage } from "./pages/ProjectListPage";

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
        <span className="local-badge">本地模式</span>
      </header>
      <main>
        <Switch>
          <Route path="/" component={ProjectListPage} />
          <Route path="/projects/:projectId" component={ProjectDetailPage} />
          <Route component={NotFoundPage} />
        </Switch>
      </main>
    </div>
  );
}
