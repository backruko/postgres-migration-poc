import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { AppProviders } from "./app/AppProviders";
import { CalendarPage } from "./features/calendar/CalendarPage";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AppProviders>
      <CalendarPage />
    </AppProviders>
  </StrictMode>,
);
