import { InstagramComposer } from "../../../components/instagram-composer";

export default async function InstagramPage({
  params,
}: {
  params: Promise<{ exportId: string }>;
}) {
  const { exportId } = await params;
  return <InstagramComposer key={exportId} exportId={exportId} />;
}
