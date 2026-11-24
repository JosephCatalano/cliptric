import { redirect } from "next/navigation";

export default async function DiscoverPage({
  searchParams,
}: {
  searchParams: Promise<{ twitch?: string | string[] }>;
}) {
  const { twitch } = await searchParams;
  const params = new URLSearchParams();
  if (typeof twitch === "string") params.set("twitch", twitch);
  redirect(params.size ? `/twitch?${params}` : "/twitch");
}
