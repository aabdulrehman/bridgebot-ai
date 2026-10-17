import type { Metadata, Viewport } from "next";
import "./globals.css";
import PwaRegister from "@/components/PwaRegister";
import WhatsAppFab from "@/components/WhatsAppFab";

export const metadata: Metadata = {
  title: "BridgeBot AI — Control Room",
  description: "Cloud-to-local bridge and on-demand skill training for your AI agents.",
  manifest: "/manifest.webmanifest",
  icons: { icon: "/icon-192.png", apple: "/icon-192.png" },
  appleWebApp: { capable: true, statusBarStyle: "black-translucent", title: "BridgeBot" },
};

export const viewport: Viewport = {
  themeColor: "#020617",
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-dvh antialiased">
        <PwaRegister />
        {children}
        <WhatsAppFab />
      </body>
    </html>
  );
}
