import React from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./styles/tokens.css";
import "./styles/globals.css";
const rootElement = document.getElementById("root");
if (!rootElement) throw new Error("Missing #root mount element");
createRoot(rootElement).render(<React.StrictMode><App /></React.StrictMode>);