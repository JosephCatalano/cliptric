import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Data deletion",
  description: "How to disconnect an account and request deletion of Cliptric data.",
};

export default function DataDeletionPage() {
  return (
    <>
      <h1>Data deletion</h1>
      <section>
        <h2>Disconnect a social account</h2>
        <p>In Cliptric, open Accounts, select the connection, and choose Disconnect. This removes the stored credentials for that destination. You can also revoke Cliptric’s access from your platform’s connected app settings.</p>
      </section>
      <section>
        <h2>Request deletion</h2>
        <p>Email <a href="mailto:hello.cliptric@gmail.com">hello.cliptric@gmail.com</a> with the platform, account name, and the data you want removed. We may ask you to verify that you control the account. Keep passwords and access tokens out of the email.</p>
      </section>
      <section>
        <h2>Manage saved files and posts</h2>
        <p>Use the workspace’s project and clip deletion controls to remove the corresponding saved items. For media and records retained by Cliptric, see the <Link href="/privacy">Privacy Policy</Link>. Manage any posts already published through the platform where they appear.</p>
      </section>
    </>
  );
}
