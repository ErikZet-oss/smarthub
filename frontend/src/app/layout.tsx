import type { Metadata, Viewport } from "next";
import { Inter } from "next/font/google";
import "./globals.css";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Smarthub",
  description: "Interný porovnávač cien a skladov",
  icons: {
    icon: [{ url: "/icon.svg", type: "image/svg+xml", sizes: "any" }],
    apple: [{ url: "/icon.svg", sizes: "180x180", type: "image/svg+xml" }],
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="sk" className={`${inter.variable} h-full antialiased`}>
      <body className="min-h-full flex flex-col overflow-x-hidden">{children}</body>
    </html>
  );
}
