// Server configuration shared by the rewrite and streaming import route.
const url = new URL(process.env.CLIPPING_API_URL ?? "http://127.0.0.1:8765");
if (
  url.protocol !== "http:" ||
  !["127.0.0.1", "localhost"].includes(url.hostname) ||
  url.pathname !== "/" ||
  url.search ||
  url.hash ||
  url.username ||
  url.password
) {
  throw new Error("CLIPPING_API_URL must be a local HTTP origin.");
}
export const backendOrigin = url.origin;
