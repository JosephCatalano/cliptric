import { ProjectResults } from "../../components/project-results";

export default async function ProjectPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ clip?: string | string[] }>;
}) {
  const { id } = await params;
  const { clip } = await searchParams;
  return <ProjectResults key={id} id={id} initialClip={typeof clip === "string" ? clip : undefined} />;
}
