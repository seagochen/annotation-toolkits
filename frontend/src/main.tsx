import React from "react";
import ReactDOM from "react-dom/client";

import { App } from "./App";
import { HostedSession } from "./HostedSession";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <HostedSession><App /></HostedSession>
  </React.StrictMode>,
);
