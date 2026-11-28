import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App, createQueryClient } from "./app/App";
import "./styles/tokens.css";
import "./styles/base.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App queryClient={createQueryClient()} />
  </StrictMode>,
);
