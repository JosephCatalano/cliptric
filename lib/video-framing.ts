import type { SourceRegion } from "./projects";

export function portraitRegion(width: number, height: number): SourceRegion {
  const ratio = (9 * height) / (16 * width);
  const w = Math.min(1, ratio);
  const h = Math.min(1, 1 / ratio);
  return { x: (1 - w) / 2, y: (1 - h) / 2, width: w, height: h };
}

export function fitPortraitRegion(
  region: SourceRegion,
  width: number,
  height: number,
): SourceRegion {
  const ratio = (9 * height) / (16 * width);
  const w = Math.min(region.width, region.height * ratio);
  const h = Math.min(region.height, region.width / ratio);
  return {
    x: region.x + (region.width - w) / 2,
    y: region.y + (region.height - h) / 2,
    width: w,
    height: h,
  };
}

export function resizePortraitRegion(
  region: SourceRegion,
  dx: number,
  dy: number,
  aspect: number,
): SourceRegion {
  const ratio = 9 / 16 / aspect;
  const delta = Math.abs(dx / ratio) > Math.abs(dy) ? dx / ratio : dy;
  const minimum = Math.max(0.01, 0.01 / ratio);
  const maximum = Math.min(1 - region.y, (1 - region.x) / ratio);
  const height = Math.min(
    maximum,
    Math.max(Math.min(minimum, maximum), region.height + delta),
  );
  return { ...region, width: height * ratio, height };
}

// Match SourceRegion.pixels: crop on even chroma boundaries before scaling.
export function pixelRegion(
  region: SourceRegion,
  width: number,
  height: number,
  target?: [number, number],
) {
  width = Math.floor(width / 2) * 2;
  height = Math.floor(height / 2) * 2;
  let x = Math.floor((region.x * width) / 2) * 2;
  let y = Math.floor((region.y * height) / 2) * 2;
  let w = Math.min(Math.floor((region.width * width) / 2) * 2, width - x);
  let h = Math.min(Math.floor((region.height * height) / 2) * 2, height - y);
  if (target) {
    const cw = Math.min(
      w,
      Math.max(2, Math.floor((h * target[0]) / target[1] / 2) * 2),
    );
    const ch = Math.min(
      h,
      Math.max(2, Math.floor((w * target[1]) / target[0] / 2) * 2),
    );
    x += Math.floor((w - cw) / 4) * 2;
    y += Math.floor((h - ch) / 4) * 2;
    w = cw;
    h = ch;
  }
  return { x, y, width: w, height: h };
}
