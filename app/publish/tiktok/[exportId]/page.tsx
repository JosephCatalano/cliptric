import { TikTokComposer } from "../../../components/tiktok-composer";

export default async function TikTokPage({
  params,
  searchParams,
}: {
  params: Promise<{ exportId: string }>;
  searchParams: Promise<{ at?: string }>;
}) {
  const { exportId } = await params;
  const { at } = await searchParams;
  return (
    <TikTokComposer
      exportId={exportId}
      scheduledAt={typeof at === "string" ? at : ""}
    />
  );
}
