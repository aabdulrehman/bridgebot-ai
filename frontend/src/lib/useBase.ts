"use client";
/** API base URL for docs examples (resolved on the client). */
import { useEffect, useState } from "react";
import { apiBase } from "./api";

export function useApiBase(): string {
  const [base, setBase] = useState("https://bridgebot-ai.onrender.com");
  useEffect(() => setBase(apiBase()), []);
  return base;
}
