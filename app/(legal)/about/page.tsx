import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import cliptricLogoDark from "@/assets/branding/cliptric-logo-dark.webp";
import cliptricLogoLight from "@/assets/branding/cliptric-logo-light.webp";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";

export const metadata: Metadata = {
  title: "Video clipping and publishing",
  description: "Turn long recordings into captioned vertical clips with Cliptric’s local video workspace.",
};

export default function AboutPage() {
  return (
    <>
      <header className="flex flex-wrap items-center justify-between gap-4">
        <span className="block w-36">
          <Image src={cliptricLogoLight} alt="Cliptric" unoptimized className="h-auto w-full dark:hidden" />
          <Image src={cliptricLogoDark} alt="Cliptric" unoptimized className="hidden h-auto w-full dark:block" />
        </span>
        <Button asChild variant="outline">
          <Link href="/workspace" prefetch={false} className="no-underline!">Open workspace</Link>
        </Button>
      </header>
      <section className="my-8 flex flex-col gap-5">
        <h1 className="max-w-xl text-4xl! leading-tight tracking-tight sm:text-5xl!">
          Turn long recordings into short clips
        </h1>
        <p className="max-w-lg text-base text-muted-foreground">
          Cliptric is a video editor for creators working with streams, podcasts, and recorded interviews.
          Choose a moment, reframe it for vertical video, and review the captions before you export.
        </p>
      </section>
      <Separator />
      <section>
        <h2>Import and edit</h2>
        <p>Import a video file, a public YouTube video, or a completed public Twitch VOD. Trim the recording, adjust the framing, and correct the transcript before you export a captioned MP4.</p>
      </section>
      <section>
        <h2>Review and publish</h2>
        <p>Download your export or select a connected account. Review the exact video, destination, and post settings before approving a publish or scheduling it.</p>
      </section>
      <section>
        <h2>Local processing</h2>
        <p>Cliptric stores your recordings and projects and renders videos on the computer running its backend. AI features send relevant transcript text to OpenAI after you approve sharing. Optional visual analysis can also send sampled still images and local audio measurements.</p>
      </section>
    </>
  );
}
