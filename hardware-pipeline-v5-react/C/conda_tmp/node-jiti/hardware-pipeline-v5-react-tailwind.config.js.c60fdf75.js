"use strict";/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        navy:    "#070b14",
        panel:   "#1a2235",
        panel2:  "#2a3a50",
        teal:    "#00c6a7",
        "teal-dim": "rgba(0,198,167,0.15)",
        "teal-glow": "rgba(0,198,167,0.25)",
        "text-primary": "#e2e8f0",
        "text-muted": "#94a3b8",
        "text-dim": "#64748b",
        "border-panel": "rgba(42,58,80,0.8)",
        danger: "#dc2626",
        warning: "#f59e0b",
        blue: "#3b82f6",
      },
      fontFamily: {
        syne: ["Syne", "sans-serif"],
        mono: ["DM Mono", "JetBrains Mono", "monospace"],
        code: ["JetBrains Mono", "monospace"],
      },
      keyframes: {
        blink: { "0%,100%": { opacity: "1" }, "50%": { opacity: "0" } },
        fadeUp: { from: { opacity: "0", transform: "translateY(16px)" }, to: { opacity: "1", transform: "translateY(0)" } },
        pulse2: { "0%,100%": { opacity: "1" }, "50%": { opacity: "0.4" } },
      },
      animation: {
        blink: "blink 1s step-end infinite",
        fadeUp: "fadeUp 0.22s ease both",
        pulse2: "pulse2 2s ease-in-out infinite",
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
}
 /* v7-516b676b9637b0f4 */