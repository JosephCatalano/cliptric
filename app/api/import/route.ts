import { backendOrigin } from "../../../lib/backend";

export const runtime = "nodejs";

export async function POST(request: Request) {
  const headers = new Headers();
  for (const name of [
    "content-type",
    "content-length",
    "origin",
    "sec-fetch-site",
  ]) {
    const value = request.headers.get(name);
    if (value !== null) headers.set(name, value);
  }
  // Preserve the caller's host for the backend's local-only boundary checks.
  headers.set("x-forwarded-host", request.headers.get("host") ?? "invalid");
  const signal = AbortSignal.any([
    request.signal,
    AbortSignal.timeout(1_830_000),
  ]);
  try {
    const options: RequestInit & { duplex: "half" } = {
      method: "POST",
      headers,
      body: request.body,
      duplex: "half",
      signal,
      cache: "no-store",
      redirect: "error",
    };
    // Route handlers stream the original body. External rewrites clone it and
    // truncate at Next.js's buffer limit, which is unsuitable for large videos.
    const response = await fetch(
      `${backendOrigin}/api/recordings${new URL(request.url).search}`,
      options,
    );
    return new Response(response.body, {
      status: response.status,
      headers: {
        "Content-Type":
          response.headers.get("content-type") ?? "application/json",
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
      },
    });
  } catch (error) {
    if (!signal.aborted) console.error("Local import forwarding failed", error);
    return Response.json(
      {
        detail: signal.aborted
          ? "Import interrupted or timed out. Reload the library before retrying."
          : "Cannot reach the local API. Start it and retry.",
      },
      { status: signal.aborted ? 408 : 502 },
    );
  }
}
