import { EngineerWorkspace } from "@/components/workspace/engineer-workspace"

export default async function RepoWorkspacePage({
  params,
}: {
  params: Promise<{ repositoryId: string }>
}) {
  const { repositoryId } = await params
  return <EngineerWorkspace repositoryId={decodeURIComponent(repositoryId)} />
}
