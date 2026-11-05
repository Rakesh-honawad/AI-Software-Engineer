"use client"

import { useParams } from "next/navigation"
import { EngineerWorkspace } from "@/components/workspace/engineer-workspace"

export default function WorkspacePage() {
  const params = useParams<{ repoId: string }>()
  return <EngineerWorkspace repositoryId={decodeURIComponent(params.repoId)} />
}