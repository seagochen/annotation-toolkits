import { PlusIcon } from "../components/shell/icons";
import { useProjects } from "../components/shell/ProjectsContext";
import { ErrorState } from "../components/AsyncState";

/**
 * No project grid here on purpose: projects live in the side navigation, and
 * the landing page only offers the one thing you cannot do from there.
 */
export function HomePage() {
  const { state, reload, startNewProject } = useProjects();
  if (state.kind === "error") return <ErrorState message={state.message} onRetry={() => void reload()} />;
  const count = state.kind === "ready" ? state.projects.length : 0;

  return (
    <section className="home">
      <div className="home-inner">
        <p className="eyebrow">Annotation Toolkits</p>
        <h1>{count ? "选择或新建一个项目" : "创建第一个标注项目"}</h1>
        <p className="home-lead">
          新建项目时选择标注任务、填写属性，然后在项目里导入数据、标注和导出。所有数据与结果都保存在本地。
        </p>
        <button className="new-project-button" onClick={startNewProject} type="button">
          <PlusIcon />
          新建项目
        </button>
        {count > 0 && <p className="home-hint">已有 {count} 个项目，可在左侧导航中打开。</p>}
      </div>
    </section>
  );
}
