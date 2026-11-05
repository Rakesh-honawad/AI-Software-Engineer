
"use client"

import { useEffect, useState } from "react"
import { usePathname, useRouter } from "next/navigation"
import { createSupabaseClient } from "@/lib/supabase/client"

export function AuthGate({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const router = useRouter()
  const [hasSession, setHasSession] = useState<boolean | null>(null)

  useEffect(() => {
    const supabase = createSupabaseClient()
    let authEventSeen = false

    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, session) => {
      authEventSeen = true
      setHasSession(Boolean(session))
    })

    supabase.auth.getSession().then(({ data, error }) => {
      if (!authEventSeen) {
        setHasSession(!error && Boolean(data.session))
      }
    }).catch(() => {
      if (!authEventSeen) {
        setHasSession(false)
      }
    })

    return () => {
      subscription.unsubscribe()
    }
  }, [])

  const isCallback = pathname === "/auth/callback"
  const isLogin = pathname === "/login"

  useEffect(() => {
    if (isCallback || hasSession === null) return

    if (!hasSession && !isLogin) {
      router.replace("/login")
    } else if (hasSession && isLogin) {
      router.replace("/")
    }
  }, [pathname, hasSession, isCallback, isLogin, router])

  // Email confirmation must be allowed to establish the first session.
  if (isCallback) {
    return <>{children}</>
  }

  // Do not render protected pages while authentication is unresolved.
  if (hasSession === null) {
    return <LoadingScreen />
  }

  // Only authenticated users can access application pages.
  if (!hasSession && !isLogin) {
    return <LoadingScreen />
  }

  // Already-authenticated users should not remain on the login page.
  if (hasSession && isLogin) {
    return <LoadingScreen />
  }

  return <>{children}</>
}

function LoadingScreen() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-[#12141a] text-white">
      <div className="text-center">
        <div className="mx-auto mb-4 h-8 w-8 animate-spin rounded-full border-2 border-white/20 border-t-blue-500" />
        <p className="text-sm text-gray-400">Checking authentication...</p>
      </div>
    </main>
  )
}
