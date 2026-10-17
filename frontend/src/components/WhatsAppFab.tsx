"use client";
/** Floating "Contact on WhatsApp" button (all pages). Prefills plan + email when known. */
import { useEffect, useState } from "react";
import { readMe, waLink, type CachedMe } from "@/lib/site";

export function WhatsAppIcon({ className = "h-5 w-5" }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} fill="currentColor" aria-hidden>
      <path d="M16.04 3C9.4 3 4 8.33 4 14.9c0 2.3.67 4.45 1.83 6.27L4 29l8.04-1.8a12.2 12.2 0 0 0 4 .67C22.66 27.87 28 22.53 28 15.97 28 9.4 22.66 3 16.04 3Zm0 22.6c-1.3 0-2.6-.25-3.8-.75l-.27-.11-4.77 1.07 1.05-4.55-.18-.29a9.6 9.6 0 0 1-1.5-5.07c0-5.35 4.4-9.7 9.47-9.7 5.2 0 9.47 4.45 9.47 9.77 0 5.33-4.27 9.63-9.47 9.63Zm5.2-7.1c-.28-.14-1.68-.83-1.94-.92-.26-.1-.45-.14-.64.14-.19.28-.73.92-.9 1.1-.16.2-.33.21-.61.07-.28-.14-1.2-.44-2.28-1.4a8.5 8.5 0 0 1-1.58-1.96c-.17-.28-.02-.43.12-.57.13-.13.28-.33.42-.5.14-.16.19-.28.28-.47.1-.19.05-.35-.02-.5-.07-.14-.64-1.54-.88-2.1-.23-.55-.47-.48-.64-.49h-.55c-.19 0-.5.07-.75.35-.26.28-.99.96-.99 2.35 0 1.39 1.01 2.73 1.15 2.92.14.19 1.99 3.04 4.82 4.26.67.29 1.2.47 1.61.6.68.21 1.3.18 1.78.11.54-.08 1.68-.69 1.92-1.35.24-.66.24-1.23.17-1.35-.07-.12-.26-.19-.54-.33Z" />
    </svg>
  );
}

export default function WhatsAppFab() {
  const [me, setMe] = useState<CachedMe | null>(null);
  useEffect(() => {
    const sync = () => setMe(readMe());
    sync();
    window.addEventListener("bridgebot-me", sync);
    return () => window.removeEventListener("bridgebot-me", sync);
  }, []);
  const text = me
    ? `Hello BridgeBot AI support. Account: ${me.email} (plan: ${me.plan_name ?? me.plan_id}). I need help with:`
    : "Hello BridgeBot AI, I have a question about your plans:";
  return (
    <a href={waLink(text)} target="_blank" rel="noopener noreferrer" aria-label="Contact on WhatsApp"
      className="fixed right-4 bottom-20 z-40 flex h-14 w-14 items-center justify-center rounded-full bg-[#25D366] text-white shadow-xl shadow-black/40 transition hover:scale-105 md:bottom-6">
      <WhatsAppIcon className="h-8 w-8" />
    </a>
  );
}
