import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Prihlásenie · Smarthub",
  description: "Prihlásenie do interného nástroja Smarthub",
};

export default function LoginLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return children;
}
