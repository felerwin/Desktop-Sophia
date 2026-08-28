import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Ember · Desktop Companion",
  description: "A local control room for Ember, Tony's desktop gaming companion.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
