import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Sophia · Desktop Companion",
  description: "A local control room for Sophia, Tony's desktop gaming companion.",
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
