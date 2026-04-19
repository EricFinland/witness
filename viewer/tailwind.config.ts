import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: [
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "sans-serif",
        ],
        mono: [
          "JetBrains Mono",
          "Geist Mono",
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Monaco",
          "monospace",
        ],
      },
      colors: {
        bg: {
          DEFAULT: "#0a0a0b",
          muted: "#111113",
          subtle: "#151518",
          card: "#161619",
        },
        border: {
          DEFAULT: "#1f1f23",
          muted: "#2a2a30",
        },
        fg: {
          DEFAULT: "#e8e8eb",
          muted: "#a0a0a8",
          subtle: "#6a6a72",
        },
        accent: {
          DEFAULT: "#7c5cff",
          muted: "#5a41cc",
        },
        success: "#4ade80",
        warning: "#fbbf24",
        danger: "#f87171",
      },
    },
  },
  plugins: [],
} satisfies Config;
