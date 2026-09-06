# Annotation Toolkits Frontend

本地多任务标注平台的 React/TypeScript 前端。当前包含项目列表和项目详情，标注工作台由
后续任务类型 issue 接入。

## 开发

先在仓库根目录启动后端：

```bash
export ANNOTATION_PROJECTS_CONFIG=backend/configs/projects.example.yaml
uvicorn annotation_platform.server:app --app-dir backend --reload
```

再启动前端：

```bash
cd frontend
npm install
npm run dev
```

Vite 默认在 `http://localhost:5173` 提供页面，并把 `/api` 代理到
`http://127.0.0.1:8000`。如前后端分开部署，可通过 `VITE_API_BASE_URL` 指定 API
基础地址。

## API 类型

`npm run generate:api` 先从后端 `app.openapi()` 写出 `src/api/openapi.json`，再生成
`src/api/schema.d.ts`。生成器直接导入本仓库后端，不要求先启动 HTTP 服务。`build`、
`typecheck` 和 `test` 都会先执行此命令，后端契约变化不会被手写类型掩盖。

## 验证

```bash
npm run typecheck
npm test
npm run build
```
