import type { Metadata } from "next";
import { JetBrains_Mono, Roboto } from "next/font/google";
import { Suspense } from "react";

import { CartProvider } from "@/components/cart-provider";
import { SiteHeader } from "@/components/site-header";
import { cn } from "@/lib/utils";

import "./globals.css";

const roboto = Roboto({ subsets: ["latin"], variable: "--font-sans" });
const jetbrainsMono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-mono" });

export const metadata: Metadata = {
  title: { default: "Acme Store", template: "%s · Acme Store" },
  description: "Small goods for desk, kitchen and home.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={cn("font-sans", roboto.variable, jetbrainsMono.variable)}>
      <body className="flex min-h-screen flex-col">
        <CartProvider>
          <Suspense fallback={<div className="h-14 border-b" />}>
            <SiteHeader />
          </Suspense>
          <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8 sm:px-6">{children}</main>
          <footer className="border-t">
            <div className="mx-auto max-w-6xl px-4 py-5 text-xs text-muted-foreground sm:px-6">
              Acme Store is a demo shop. No real payments are taken.
            </div>
          </footer>
        </CartProvider>
      </body>
    </html>
  );
}
