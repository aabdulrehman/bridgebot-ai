"use client";
/** Entry: route to dashboard if logged in, else login. */
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { getToken } from "@/lib/api";

export default function Home() {
  const router = useRouter();
  useEffect(() => {
    router.replace(getToken() ? "/dashboard/" : "/login/");
  }, [router]);
  return <main className="grid min-h-dvh place-items-center text-slate-400">Loading BridgeBot…</main>;
}
