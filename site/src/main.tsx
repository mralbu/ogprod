import "@fontsource-variable/inter";
import "@fontsource-variable/jetbrains-mono";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { initAnalytics } from "./lib/analytics";
import "./index.css";

initAnalytics(import.meta.env.VITE_GOATCOUNTER_URL);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
