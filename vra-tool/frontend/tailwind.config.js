/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx,ts,tsx}"],
  theme: {
    extend: {
      colors: {
        paytm: { blue: "#00BAF2", dark: "#002970", bg: "#F5F7FA" },
      },
    },
  },
  plugins: [],
};
