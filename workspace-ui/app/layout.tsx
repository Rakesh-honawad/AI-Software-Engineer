
import { Analytics } from "@vercel/analytics/next"
import type { Metadata, Viewport } from "next"
import { Inter, IBM_Plex_Mono } from "next/font/google"
import { AuthGate } from "@/components/auth/auth-gate"
import "./globals.css"
import "./workspace-theme.css"

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
})

const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-plex-mono",
  display: "swap",
})

export const metadata: Metadata = {
  title: "Engineer Mode — Dependency Graph",
  description:
    "A precise developer instrument for exploring code repositories, mapping dependencies, and tracing the blast radius of a change.",
  generator: "v0.app",
}

export const viewport: Viewport = {
  colorScheme: "dark",
  themeColor: "#12141a",
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
  userScalable: false,
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html lang="en" className={`${inter.variable} ${plexMono.variable}`}>
      <body className="antialiased" style={{ background: "#12141a" }}>
        <AuthGate>{children}</AuthGate>
        {process.env.NODE_ENV === "production" && <Analytics />}
      </body>
    </html>
  )
}
