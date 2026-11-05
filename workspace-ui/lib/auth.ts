// Mirrors frontend/app/lib/auth.ts's storage key exactly -- workspace-ui
// runs same-origin via the Next.js rewrite, so localStorage set by
// frontend is directly readable here. Read-only: workspace-ui never
// writes/clears the session, that's frontend's job.
export function getToken(): string | null {
  if (typeof window === "undefined") return null
  return localStorage.getItem("token")
}