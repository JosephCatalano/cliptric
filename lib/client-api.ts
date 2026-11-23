export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function responseError(status: number, payload: unknown, recovery: string) {
  const detail =
    payload && typeof payload === "object" && "detail" in payload
      ? payload.detail
      : null;
  if (typeof detail === "string" && detail.trim()) {
    return new ApiError(detail, status);
  }
  if (Array.isArray(detail) && typeof detail[0]?.msg === "string") {
    const field = Array.isArray(detail[0].loc)
      ? detail[0].loc
          .filter(
            (part: unknown) =>
              (typeof part === "string" || typeof part === "number") &&
              part !== "body",
          )
          .join(".")
      : "";
    return new ApiError(
      `Correct ${field ? `the “${field}” field` : "the request inputs"} and submit again. ${detail[0].msg}`,
      status,
    );
  }
  const known: Record<number, string> = {
    400: "Cliptric could not read the request. Reload the page and review the inputs.",
    401: "Your Cliptric sign-in is required. Open the workspace and sign in again.",
    403: "Access was denied. Open Cliptric from the address you normally sign in to and reload the page.",
    404: "This item or page could not be found. Return to the list and reopen it.",
    409: "This action conflicts with the current state. Refresh the item before deciding whether to retry.",
    413: "The request was too large. Choose a smaller file or reduce the submitted content.",
    429: "Too many requests were sent. Wait a moment before trying again.",
  };
  const explanation =
    known[status] ??
    (status === 408 || status === 504
      ? `The request timed out. ${recovery}`
      : status === 502 || status === 503
        ? `Cliptric did not receive a usable response from the backend or a connected service. ${recovery}`
        : status >= 500
          ? `Cliptric encountered a server error. ${recovery}`
          : `Cliptric could not complete the request. ${recovery}`);
  return new ApiError(`${explanation} (HTTP ${status})`, status);
}

export async function creationFingerprint(value: unknown): Promise<string> {
  const bytes = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(
      JSON.stringify(value, (_key, entry) =>
        entry && typeof entry === "object" && !Array.isArray(entry)
          ? Object.fromEntries(
              Object.entries(entry)
                .filter(([, value]) => value !== undefined)
                .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)),
            )
          : entry,
      ),
    ),
  );
  return Array.from(new Uint8Array(bytes), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
}

export async function api<T>(
  path: string,
  options?: RequestInit,
  timeoutMs?: number,
): Promise<T> {
  const write = !["GET", "HEAD"].includes(
    (options?.method ?? "GET").toUpperCase(),
  );
  const recovery = write
    ? "Refresh the saved status before deciding whether to try again."
    : "Check that the local Cliptric backend is running, then reload.";
  const timeout = AbortSignal.timeout(
    timeoutMs ?? (options?.body instanceof File ? 1_830_000 : 20_000),
  );
  const signal = options?.signal
    ? AbortSignal.any([timeout, options.signal])
    : timeout;
  let value: unknown;
  try {
    const response = await fetch(path, {
      cache: "no-store",
      ...options,
      signal,
    });
    value = await response.json().catch((error) => {
      if (signal.aborted) throw error;
      if (!response.ok) return null;
      throw new ApiError(
        `Cliptric received an unreadable response. ${recovery}`,
        502,
      );
    });
    if (!response.ok) throw responseError(response.status, value, recovery);
  } catch (error) {
    if (options?.signal?.aborted || error instanceof ApiError) throw error;
    if (timeout.aborted)
      throw new ApiError(`The request timed out. ${recovery}`, 408);
    throw new ApiError(
      `The connection to Cliptric failed. Check your network connection. ${recovery}`,
      0,
    );
  }
  if (
    typeof window !== "undefined" &&
    options?.method &&
    ["POST", "PUT", "PATCH"].includes(options.method.toUpperCase()) &&
    value &&
    typeof value === "object" &&
    "id" in value &&
    typeof value.id === "string" &&
    "status" in value &&
    typeof value.status === "string" &&
    "items" in value &&
    Array.isArray(value.items) &&
    "settings" in value
  ) {
    window.dispatchEvent(
      new CustomEvent("cliptric:project-processing", {
        detail: {
          project: value,
          generation:
            path === "/api/projects" ||
            path.endsWith("/resume") ||
            path.endsWith("/more-clips"),
        },
      }),
    );
  }
  return value as T;
}

export const message = (error: unknown) =>
  error instanceof Error && error.message
    ? error.message
    : "Cliptric could not complete the action. Check the saved status before trying again.";

// Fetch does not expose upload progress. Keep the same local streaming route
// and timeout while reporting bytes transferred by the browser.
export function uploadRecording<T>(
  file: File,
  signal: AbortSignal,
  onProgress: (percent: number) => void,
): Promise<T> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    const abort = () => request.abort();
    if (signal.aborted) {
      reject(new DOMException("Import cancelled", "AbortError"));
      return;
    }
    request.open(
      "POST",
      `/api/import?filename=${encodeURIComponent(file.name)}`,
    );
    request.setRequestHeader("Content-Type", "application/octet-stream");
    request.timeout = 1_830_000;
    request.upload.onprogress = (event) => {
      if (event.lengthComputable)
        onProgress(Math.min(100, (event.loaded / event.total) * 100));
    };
    request.upload.onload = () => onProgress(100);
    request.onload = () => {
      const recovery = "Check your imported videos before retrying.";
      let payload: unknown = null;
      try {
        payload = JSON.parse(request.responseText);
      } catch {
        if (request.status >= 200 && request.status < 300) {
          reject(
            new Error(`The import response could not be read. ${recovery}`),
          );
          return;
        }
      }
      if (request.status < 200 || request.status >= 300) {
        reject(responseError(request.status, payload, recovery));
        return;
      }
      resolve(payload as T);
    };
    request.onerror = () =>
      reject(
        new Error(
          "Import connection lost. Check your imported videos before retrying.",
        ),
      );
    request.ontimeout = () =>
      reject(
        new Error(
          "Import timed out. Check your imported videos before retrying.",
        ),
      );
    request.onabort = () =>
      reject(new DOMException("Import cancelled", "AbortError"));
    request.onloadend = () => signal.removeEventListener("abort", abort);
    signal.addEventListener("abort", abort, { once: true });
    request.send(file);
  });
}
