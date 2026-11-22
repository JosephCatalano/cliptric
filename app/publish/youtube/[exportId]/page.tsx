import { YouTubeComposer } from "../../../components/youtube-composer";

export default async function YouTubePage({
  params,
  searchParams,
}: {
  params: Promise<{ exportId: string }>;
  searchParams: Promise<{ at?: string }>;
}) {
  const { exportId } = await params;
  const { at } = await searchParams;
  return (
    <YouTubeComposer
      exportId={exportId}
      scheduledAt={typeof at === "string" ? at : ""}
    />
  );
}
