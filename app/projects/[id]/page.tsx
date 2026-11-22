import { ProjectResults } from "../../components/project-results";

export default async function ProjectPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <ProjectResults key={id} id={id} />;
}
