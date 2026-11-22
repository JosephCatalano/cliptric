import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Terms of Service",
  description: "Terms for using Cliptric and its connected publishing features.",
};

export default function TermsPage() {
  return (
    <>
      <header>
        <h1>Terms of Service</h1>
        <p className="mt-2 text-muted-foreground">Last updated: November 17, 2026</p>
      </header>
      <p>
        These terms apply to Cliptric, a video clipping and publishing application.
        By using Cliptric, you agree to these terms and acknowledge our{" "}
        <Link href="/privacy">Privacy Policy</Link>. If you do not agree, do not use the application.
      </p>
      <section>
        <h2>Using Cliptric</h2>
        <p>
          You must be at least 18 and legally able to accept these terms. Use Cliptric
          lawfully and only with videos, music, images, and other material you own or
          have permission to process and publish. Do not use it to infringe others&apos;
          rights, distribute unlawful content, bypass access restrictions, or interfere
          with the application or connected services.
        </p>
      </section>
      <section>
        <h2>Your content and responsibilities</h2>
        <p>
          You retain your rights to your content. You authorize Cliptric to process,
          store, and transfer it as needed to carry out the actions you request.
          Review generated clips, transcripts, captions, and AI suggestions before
          using them. You are responsible for their accuracy, permissions, required
          disclosures, and publication. Keep backups of important files and protect
          access to your computer and connected accounts.
        </p>
      </section>
      <section>
        <h2>Connected services</h2>
        <p>
          Optional integrations are subject to the provider&apos;s own terms, policies,
          permissions, and approval requirements. Connecting an account does not
          authorize automatic posting: you must review and approve each post in
          Cliptric. You can disconnect accounts in Connected accounts. Disconnecting
          does not delete content already published on the platform.
        </p>
        <p className="mt-2">
          TikTok use is subject to{" "}
          <a href="https://www.tiktok.com/legal/terms-of-service">TikTok&apos;s terms</a>.
          YouTube use is subject to the{" "}
          <a href="https://www.youtube.com/t/terms">YouTube Terms of Service</a>.
          Instagram use is subject to the{" "}
          <a href="https://help.instagram.com/581066165581870">Instagram Terms of Use</a>.
          Third-party API or processing charges may apply to the services you configure.
        </p>
      </section>
      <section>
        <h2>Availability and limitations</h2>
        <p>
          Cliptric is provided on an &quot;as is&quot; and &quot;as available&quot; basis.
          Features may change or become unavailable. We do not guarantee error-free
          operation, AI accuracy, platform approval, publication, audience reach,
          or earnings. To the extent permitted by law, we disclaim implied warranties
          and liability for indirect or consequential losses, including lost data,
          profits, or platform access. Nothing in these terms limits rights or
          liabilities that applicable law does not allow us to limit.
        </p>
      </section>
      <section>
        <h2>Stopping use and changes</h2>
        <p>
          You may stop using Cliptric at any time and revoke connected permissions.
          Access to any services we operate may be restricted for misuse or security
          reasons. Updates to these terms will appear here with a revised date;
          review them before continuing to use the application.
        </p>
      </section>
      <section>
        <h2>Contact</h2>
        <p>Public contact details are pending and will be added before public release.</p>
      </section>
    </>
  );
}
