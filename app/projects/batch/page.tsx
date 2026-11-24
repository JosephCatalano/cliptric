import { NewBatch } from "../../components/new-batch";
import { BatchFromRecording } from "../../components/batch-from-recording";

export default async function BatchPage({
  searchParams,
}: {
  searchParams: Promise<{
    recording?: string | string[];
    mode?: string | string[];
  }>;
}) {
  const { recording, mode } = await searchParams;
  const initialMode = mode === "keep" ? "keep" : "highlights";
  if (typeof recording === "string" && /^[a-f0-9-]{36}$/i.test(recording))
    return <BatchFromRecording id={recording} mode={initialMode} />;
  return <NewBatch initialMode={initialMode} />;
}
