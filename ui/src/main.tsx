import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { captureAccessTokenFromUrl } from "./api/access-token";
import "./design/base.css";
import "./design/components.css";

// A deployment that has an access token hands it over once, in the address bar. Take it
// before the first render, so the first request already carries it, and drop it from the
// URL. On a loopback deployment there is no token and this does nothing.
captureAccessTokenFromUrl();

const container = document.getElementById("root");
if (container) {
  createRoot(container).render(
    <StrictMode>
      <App />
    </StrictMode>
  );
}
