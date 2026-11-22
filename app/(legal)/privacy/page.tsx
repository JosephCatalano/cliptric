import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Privacy Policy",
  description:
    "How Cliptric handles media, transcripts, and connected account information.",
};

export default function PrivacyPage() {
  return (
    <>
      <header>
        <h1>Privacy Policy</h1>
        <p className="mt-2 text-muted-foreground">
          Last updated: November 21, 2026
        </p>
      </header>
      <p>
        This policy describes the current local version of Cliptric and its
        optional integrations. See our{" "}
        <Link href="/terms">Terms of Service</Link> for conditions of use.
      </p>
      <section>
        <h2>Information Cliptric processes</h2>
        <p>
          Cliptric stores imported videos and source links, filenames, media
          metadata, thumbnails, transcripts and corrections, project settings,
          clip selections, exports, and processing history on the computer
          running its backend. Transcription and video rendering run locally.
          Operational logs may contain processing details and errors.
        </p>
        <p className="mt-2">
          When you connect TikTok, YouTube or Instagram, Cliptric processes
          account or channel identifiers, profile details provided by the
          platform, authorized permissions, access and refresh tokens, and
          publishing records such as captions, visibility settings, upload
          status, and platform video IDs. These support account display,
          authorized uploads, connection renewal, and publication status checks.
        </p>
      </section>
      <section>
        <h2>When information leaves your computer</h2>
        <p>
          If you approve AI video review, highlight analysis, caption emphasis
          or supplied-clip titles, Cliptric sends the relevant saved transcript
          text and, where needed, source timestamps to OpenAI. Project-specific
          AI instructions are included when supplied for that task. These
          requests do not send the source video or audio. If you import a
          YouTube or Twitch VOD link, Cliptric contacts that provider and its
          media services to retrieve the video. When you approve a post,
          Cliptric sends the selected exported video and your chosen publishing
          details to TikTok, YouTube, X or Meta for Instagram and Facebook.
          Account connection and status checks also communicate with the
          relevant platform.
        </p>
        <p className="mt-2">
          These providers process information under their own policies:{" "}
          <a href="https://openai.com/policies/privacy-policy/">OpenAI</a>,{" "}
          <a href="https://www.tiktok.com/legal/privacy-policy">TikTok</a>, and{" "}
          <a href="https://policies.google.com/privacy">Google</a>, and{" "}
          <a href="https://privacycenter.instagram.com/policy/">
            Meta for Instagram and Facebook
          </a>
          , and <a href="https://x.com/en/privacy">X</a>. Cliptric does not sell
          your personal information or use connected account data for
          advertising.
        </p>
      </section>
      <section>
        <h2>Cookies and security</h2>
        <p>
          Cliptric uses functional cookies to remember sidebar preferences and
          validate temporary account connection requests. It does not include
          advertising trackers or third-party analytics. Connected account
          credentials are encrypted using Windows user-scoped protection. Other
          local media and project files are not encrypted by Cliptric; protect
          your device and backups. No storage or transmission method guarantees
          complete security.
        </p>
      </section>
      <section>
        <h2>Retention, disconnection, and deletion</h2>
        <p>
          Local media, transcripts, projects, processing history, and publishing
          records remain in the installation&apos;s data storage until removed.
          There is no automatic expiry. Delete project removes the workspace and
          its current clip exports; Delete clip removes an individual exported
          video and linked project entries. Source recordings, transcripts,
          saved analysis, older export versions, publishing records, downloaded
          copies and backups remain. Active processing and unresolved publishing
          must finish or be resolved first. Files still in use may require an
          API restart to complete disk cleanup. The person operating the
          installation controls its storage and backups. To remove all local
          data, stop the application and remove its data directory; this
          permanently removes saved media and project history.
        </p>
        <p className="mt-2">
          Disconnect an account in Connected accounts to remove locally stored
          credentials for that destination. TikTok also requests revocation and
          reports a failure. For other services, revoke access in the
          provider&apos;s settings. Revoking shared grants can affect other
          connected accounts. You can also revoke Google access from your{" "}
          <a href="https://myaccount.google.com/permissions">
            Google account permissions
          </a>
          . Disconnecting does not remove local publishing history or videos
          already sent to a platform. Manage those videos and any platform-held
          data directly with that provider. Provider retention policies apply to
          information already sent.
        </p>
      </section>
      <section>
        <h2>Children and policy changes</h2>
        <p>
          Cliptric is intended for adults aged 18 and over. Do not submit
          children&apos;s personal information without appropriate rights and
          consent. If our data practices change, we will update this page and
          its revision date.
        </p>
      </section>
      <section>
        <h2>Privacy questions and requests</h2>
        <p>
          Public contact details are pending and will be added before public
          release. For this local version, the person operating your
          installation controls access to and removal of its stored data.
        </p>
      </section>
    </>
  );
}
