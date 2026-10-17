"use client";
/** Registers the service worker (production only, to keep dev HMR sane). */
import { useEffect } from "react";

export default function PwaRegister() {
  useEffect(() => {
    if (typeof window === "undefined" || !("serviceWorker" in navigator)) return;
    if (process.env.NODE_ENV !== "production") return;
    navigator.serviceWorker.register("/sw.js").catch((err) => console.warn("SW registration failed", err));
  }, []);
  return null;
}
