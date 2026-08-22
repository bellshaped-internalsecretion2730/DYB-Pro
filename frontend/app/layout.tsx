import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "DYB Pro — pre-wetlab protein design OS",
  description:
    "Type a research goal, drop sequences, and get a versioned design history plus a ranked wet-lab shortlist.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
