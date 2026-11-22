import { Skeleton } from "@/components/ui/skeleton";

export function LoadingStatus({ label }: { label: string }) {
  return (
    <div role="status" className="flex flex-col gap-3">
      <span className="text-sm text-muted-foreground">{label}</span>
      <Skeleton aria-hidden="true" className="h-4 w-3/4" />
      <Skeleton aria-hidden="true" className="h-4 w-1/2" />
    </div>
  );
}

export function WorkspaceLoading({
  label = "Loading workspace…",
}: {
  label?: string;
}) {
  return (
    <div role="status" className="flex flex-col gap-6">
      <span className="sr-only">{label}</span>
      <div aria-hidden="true" className="flex flex-col gap-4">
        <Skeleton className="h-8 w-2/3 max-w-sm" />
        <Skeleton className="aspect-video w-full max-w-2xl" />
        <Skeleton className="h-5 w-1/2" />
        <Skeleton className="h-10 w-full max-w-2xl" />
      </div>
    </div>
  );
}
