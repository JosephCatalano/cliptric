import { NewProject } from "../../../components/new-project";

export default async function NewProjectPage({
  params,
}: {
  params: Promise<{ recordingId: string }>;
}) {
  const { recordingId } = await params;
  return <NewProject key={recordingId} recordingId={recordingId} />;
}
