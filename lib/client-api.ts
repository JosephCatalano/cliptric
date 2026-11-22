export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function api<T>(
  path: string,
  options?: RequestInit,
  timeoutMs?: number,
): Promise<T> {
  const response = await fetch(path, {
    cache: "no-store",
    ...options,
    signal: AbortSignal.any([
      AbortSignal.timeout(
        timeoutMs ?? (options?.body instanceof File ? 1_830_000 : 20_000),
      ),
      ...(options?.signal ? [options.signal] : []),
    ]),
  });
  if (!response.ok) {
    const payload: unknown = await response.json().catch(() => null);
    const detail =
      payload && typeof payload === "object" && "detail" in payload
        ? payload.detail
        : null;
    const field =
      Array.isArray(detail) && Array.isArray(detail[0]?.loc)
        ? detail[0].loc
            .filter(
              (part: unknown) =>
                (typeof part === "string" || typeof part === "number") &&
                part !== "body",
            )
            .join(".")
        : "";
    throw new ApiError(
      typeof detail === "string"
        ? detail
        : Array.isArray(detail) && typeof detail[0]?.msg === "string"
          ? `${field ? `${field}: ` : ""}${detail[0].msg}`
          : `Request failed (${response.status}). Check your selection and try again.`,
      response.status,
    );
  }
  return response.json() as Promise<T>;
}

export const message = (error: unknown) =>
  error instanceof Error ? error.message : "Something went wrong. Try again.";

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
      let payload: unknown;
      try {
        payload = JSON.parse(request.responseText);
      } catch {
        reject(
          new Error(
            "The import response could not be read. Check your imported videos before retrying.",
          ),
        );
        return;
      }
      if (request.status < 200 || request.status >= 300) {
        const detail =
          payload && typeof payload === "object" && "detail" in payload
            ? payload.detail
            : null;
        reject(
          new ApiError(
            typeof detail === "string"
              ? detail
              : `Import failed (${request.status}). Check your video and try again.`,
            request.status,
          ),
        );
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
