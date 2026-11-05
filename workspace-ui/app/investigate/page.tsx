import { InvestigationPanel } from "@/components/workspace/investigation-panel"

export default function InvestigatePage() {
  return (
    <div className="min-h-screen bg-gray-100">
      <main className="mx-auto min-h-screen max-w-4xl bg-white p-6 text-gray-900 shadow">
        <InvestigationPanel />
      </main>
    </div>
  )
}