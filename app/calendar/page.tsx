"use client";

import dynamic from "next/dynamic";
import { Skeleton } from "@/components/ui/skeleton";

const PostCalendar = dynamic(
  () =>
    import("../components/post-calendar").then((module) => module.PostCalendar),
  {
    ssr: false,
    loading: () => <Skeleton className="m-6 h-96" />,
  },
);

export default function CalendarPage() {
  return <PostCalendar />;
}
