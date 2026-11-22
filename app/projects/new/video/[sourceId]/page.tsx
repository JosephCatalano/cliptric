import { NewProject } from "../../../../components/new-project";

export default async function VideoProjectPage({
  params,
}: {
  params: Promise<{ sourceId: string }>;
}) {
  const { sourceId } = await params;
  return <NewProject recordingId={sourceId} youtube />;
}
