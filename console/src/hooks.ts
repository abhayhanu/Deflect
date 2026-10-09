import { useCallback, useEffect, useRef, useState } from "react";

export interface Route {
  screen: "inbox" | "ticket" | "approvals" | "metrics";
  id?: string;
}

export function parseRoute(hash: string): Route {
  const parts = hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  if (parts[0] === "tickets" && parts[1]) return { screen: "ticket", id: decodeURIComponent(parts[1]) };
  if (parts[0] === "approvals") return { screen: "approvals" };
  if (parts[0] === "metrics") return { screen: "metrics" };
  return { screen: "inbox" };
}

export function go(path: string) {
  window.location.hash = path;
}

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parseRoute(window.location.hash));
  useEffect(() => {
    const changed = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener("hashchange", changed);
    return () => window.removeEventListener("hashchange", changed);
  }, []);
  return route;
}

export interface Loaded<T> {
  data: T | null;
  error: Error | null;
  loading: boolean;
  reload: () => void;
}

// Loads once, then again every interval while the tab is visible. Zero means load once only.
export function useLoad<T>(load: () => Promise<T>, deps: unknown[], everyMs = 0): Loaded<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const latest = useRef(load);
  latest.current = load;

  const reload = useCallback(() => {
    latest
      .current()
      .then((value) => {
        setData(value);
        setError(null);
      })
      .catch((problem: Error) => setError(problem))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    setLoading(true);
    setData(null);
    reload();
    if (!everyMs) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") reload();
    }, everyMs);
    return () => window.clearInterval(timer);
  }, deps);

  return { data, error, loading, reload };
}
