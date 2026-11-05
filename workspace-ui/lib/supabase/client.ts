import { createClient, type SupabaseClient } from "@supabase/supabase-js"

let supabase: SupabaseClient | null = null

export function createSupabaseClient(): SupabaseClient {
  if (supabase) return supabase

  const url = process.env.NEXT_PUBLIC_SUPABASE_URL
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY

  if (!url || !anonKey) {
    throw new Error("Supabase environment variables are missing.")
  }

  supabase = createClient(url, anonKey, {
    auth: {
      persistSession: true,
      autoRefreshToken: true,
      detectSessionInUrl: false,
      flowType: "pkce",
    },
  })

  return supabase
}