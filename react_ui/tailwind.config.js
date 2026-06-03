/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        dark: {
          background: "#111827",
          text: "#e5e7eb",
        },
      },
    },
  },
  plugins: [],
};
