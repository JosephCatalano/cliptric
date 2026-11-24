import { NewBatch } from "../../components/new-batch";
import { BatchFromRecording } from "../../components/batch-from-recording";

export default async function BatchPage({
  searchParams,
}: {
  searchParams: Promise<{
    recording?: string | string[];
    mode?: string | string[];
    twitchClip?: string | string[];
  }>;
}) {
  const { recording, mode, twitchClip } = await searchParams;
  const initialMode = mode === "keep" ? "keep" : "highlights";
  if (typeof recording === "string" && /^[a-f0-9-]{36}$/i.test(recording))
    return <BatchFromRecording id={recording} mode={initialMode} />;
  if (
    typeof twitchClip === "string" &&
    /^[A-Za-z0-9_-]{1,150}$/.test(twitchClip) &&
    twitchClip.toLowerCase() !== "embed"
  )
    return (
      <NewBatch
        key={twitchClip}
        initialMode="keep"
        twitchClipUrl={`https://clips.twitch.tv/${twitchClip}`}
      />
    );
  return <NewBatch initialMode={initialMode} />;
}
