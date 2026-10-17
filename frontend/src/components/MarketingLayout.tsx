/** Wrapper for public pages: navbar + content + footer. */
import type { ReactNode } from "react";
import Footer from "./Footer";
import MarketingNav from "./MarketingNav";

export default function MarketingLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-dvh flex-col">
      <MarketingNav />
      <main className="flex-1">{children}</main>
      <Footer />
    </div>
  );
}
