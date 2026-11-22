import { PublishReview } from "../../../components/publish-review";

export default async function PublishPage({
  params,
  searchParams,
}: {
  params: Promise<{ exportId: string }>;
  searchParams: Promise<{ at?: string; account_id?: string }>;
}) {
  const { exportId } = await params;
  const { at, account_id } = await searchParams;
  return (
    <PublishReview
      platform="tiktok"
      exportId={exportId}
      scheduledAt={typeof at === "string" ? at : ""}
      initialAccount={typeof account_id === "string" ? account_id : ""}
    />
  );
}
