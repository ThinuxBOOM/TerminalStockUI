import React from "react";
import LandingPage from "../features/landing/LandingPage.jsx";
import "../features/landing/landing.css";

// Public landing page ("/"): standalone, outside the terminal shell.
function WelcomePage() {
  return <LandingPage />;
}

export { WelcomePage as default };
