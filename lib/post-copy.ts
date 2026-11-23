import type { PostCopyOption } from "./projects";

export function formatPostCopy(value?: PostCopyOption | null) {
  return value
    ? [value.description, value.hashtags.join(" ")].filter(Boolean).join("\n\n")
    : "";
}

export function splitPostCopy(text: string): PostCopyOption {
  const lines = text.trim().split(/\r?\n/);
  const hashtags: string[] = [];
  while (lines.length) {
    const words = lines.at(-1)!.trim().split(/\s+/);
    if (!words.every((word) => /^#[\p{L}\p{N}_]+$/u.test(word))) break;
    hashtags.unshift(...words);
    lines.pop();
  }
  return { description: lines.join("\n").trim(), hashtags };
}
