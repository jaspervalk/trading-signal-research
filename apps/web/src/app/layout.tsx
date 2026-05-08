import type { Metadata } from "next";
import { JetBrains_Mono, Newsreader } from "next/font/google";
import "./globals.css";
import { QueryProvider } from "@/components/QueryProvider";
import { Nav } from "@/components/Nav";

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-jetbrains-mono",
  display: "swap",
});

const newsreader = Newsreader({
  subsets: ["latin"],
  weight: ["400", "500"],
  style: ["italic"],
  variable: "--font-newsreader",
  display: "swap",
});

export const metadata: Metadata = {
  title: "trading-signal-research",
  description: "Research workbench for evaluating trader content creators against market data.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={`dark ${jetbrainsMono.variable} ${newsreader.variable}`}>
      <body className="antialiased min-h-screen">
        <QueryProvider>
          <Nav />
          <main className="max-w-[1480px] mx-auto px-6 py-8">{children}</main>
        </QueryProvider>
      </body>
    </html>
  );
}
