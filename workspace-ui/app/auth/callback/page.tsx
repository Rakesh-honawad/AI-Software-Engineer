"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { createSupabaseClient } from "@/lib/supabase/client"

export default function AuthCallbackPage() {
  const router = useRouter()
  const [error, setError] = useState("")

  useEffect(() => {
    let active = true

    async function completeAuthentication() {
      try {
        const supabase = createSupabaseClient()
        const code = new URLSearchParams(window.location.search).get("code")

        if (code) {
          const { error } = await supabase.auth.exchangeCodeForSession(code)
          if (error) throw error
        }

        const { data, error } = await supabase.auth.getSession()

        if (error) throw error
        if (!data.session) {
          throw new Error("No active session found. Please sign in again.")
        }

        if (active) {
          router.replace("/")
          router.refresh()
        }
      } catch (err) {
        if (active) {
          setError(
            err instanceof Error ? err.message : "Could not complete authentication."
          )
        }
      }
    }

    completeAuthentication()

    return () => {
      active = false
    }
  }, [router])

  return (
    <main className="flex min-h-screen items-center justify-center bg-[#12141a] px-4 text-white">
      <div className="max-w-md text-center">
        {error ? (
          <>
            <h1 className="text-xl font-semibold">Authentication failed</h1>
            <p className="mt-3 text-sm text-red-400">{error}</p>
            <button
              onClick={() => router.replace("/login")}
              className="mt-6 rounded-lg bg-blue-600 px-5 py-3 font-medium hover:bg-blue-500"
            >
              Back to login
            </button>
          </>
        ) : (
          <>
            <h1 className="text-xl font-semibold">Verifying your account</h1>
            <p className="mt-3 text-sm text-gray-400">
              Please wait while we complete sign-in...
            </p>
          </>
        )}
      </div>
    </main>
  )
}