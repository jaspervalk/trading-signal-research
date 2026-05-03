import type { Metadata } from "next";
import "./globals.css";
import { QueryProvider } from "@/components/QueryProvider";
import { Nav } from "@/components/Nav";

export const metadata: Metadata = {
  title: "trading-signal-research",
  description: "Research workbench for evaluating trader content creators against market data.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className="dark">
      <body className="antialiased min-h-screen">
        <QueryProvider>
          <Nav />
          <main className="max-w-7xl mx-auto px-6 py-8">{children}</main>
        </QueryProvider>
      </body>
    </html>
  );
}
