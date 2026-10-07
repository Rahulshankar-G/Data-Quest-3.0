import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        background: "#090d16",
        surface: "#111827",
        line: "#202b3b",
        accent: "#75e0c0",
      },
    },
  },
  plugins: [],
};

export default config;
