import { NewProject } from "../../../../components/new-project";

export default async function YouTubeProjectPage({
  params,
}: {
  params: Promise<{ sourceId: string }>;
}) {
  const { sourceId } = await params;
  return <NewProject key={sourceId} recordingId={sourceId} youtube />;
}
