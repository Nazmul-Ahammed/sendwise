import "./globals.css";

export const metadata = {
  title: "Sendwise - Agent OS",
  description: "Sendwise - the autonomous email marketing orchestrator",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
