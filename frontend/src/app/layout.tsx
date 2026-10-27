import type { Metadata } from "next";
import { Fraunces, Inter, Noto_Sans_Ethiopic } from "next/font/google";
import SiteHeader from "@/components/SiteHeader";
import { LangProvider } from "@/lib/lang-context";
import "./globals.css";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
});

const fraunces = Fraunces({
  variable: "--font-fraunces",
  subsets: ["latin"],
});

const notoSansEthiopic = Noto_Sans_Ethiopic({
  variable: "--font-ethiopic",
  subsets: ["ethiopic"],
});

export const metadata: Metadata = {
  title: "Soil Fertility Analyzer",
  description:
    "Predict soil fertility, get crop-specific fertilizer recommendations and top crop matches from your N, P, K and pH readings.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${inter.variable} ${fraunces.variable} ${notoSansEthiopic.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        <LangProvider>
          <SiteHeader />
          {children}
        </LangProvider>
      </body>
    </html>
  );
}
