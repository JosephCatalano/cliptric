"use client";

import { Button } from "@/components/ui/button";

import { useId, useRef, type KeyboardEvent, type PointerEvent } from "react";
import Image from "next/image";

export function clock(value: number) {
  const seconds = Math.max(0, value);
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = (seconds % 60).toFixed(1).padStart(4, "0");
  return `${hours ? `${hours}:` : ""}${String(minutes).padStart(2, "0")}:${remainder}`;
}

export function AnalysisRange({
  recordingId,
  duration,
  start,
  end,
  disabled,
  onChange,
  onPreview,
}: {
  recordingId?: string;
  duration: number;
  start: number;
  end: number;
  disabled: boolean;
  onChange(start: number, end: number): void;
  onPreview(value: number): void;
}) {
  const fieldId = useId();
  const track = useRef<HTMLDivElement>(null);
  const minimum = Math.min(0.1, duration);
  function change(edge: "start" | "end", value: number) {
    const next =
      edge === "start"
        ? Math.max(0, Math.min(value, end - minimum))
        : Math.min(duration, Math.max(value, start + minimum));
    if (edge === "start") onChange(next, end);
    else onChange(start, next);
    return next;
  }
  function drag(event: PointerEvent<HTMLButtonElement>, edge: "start" | "end") {
    if (
      !event.currentTarget.hasPointerCapture(event.pointerId) ||
      !track.current ||
      disabled
    )
      return;
    const bounds = track.current.getBoundingClientRect();
    if (bounds.width > 0)
      change(
        edge,
        Math.round(
          ((event.clientX - bounds.left) / bounds.width) * duration * 1000,
        ) / 1000,
      );
  }
  function keyboard(
    event: KeyboardEvent<HTMLButtonElement>,
    edge: "start" | "end",
  ) {
    const step = event.shiftKey ? 10 : event.altKey ? 0.1 : 1;
    let value = edge === "start" ? start : end;
    if (["ArrowLeft", "ArrowDown"].includes(event.key)) value -= step;
    else if (["ArrowRight", "ArrowUp"].includes(event.key)) value += step;
    else if (event.key === "Home")
      value = edge === "start" ? 0 : start + minimum;
    else if (event.key === "End")
      value = edge === "start" ? end - minimum : duration;
    else return;
    event.preventDefault();
    onPreview(change(edge, value));
  }
  return (
    <section className="analysis-range" aria-label="Source analysis range">
      <div className="range-readout">
        <span>
          Start <strong>{clock(start)}</strong>
        </span>
        <span>{(end - start).toFixed(1)} seconds selected</span>
        <span>
          End <strong>{clock(end)}</strong>
        </span>
      </div>
      <div className="analysis-track" ref={track}>
        {recordingId ? (
          <div className="analysis-filmstrip" aria-hidden="true">
            {Array.from({ length: 6 }, (_, index) => (
              <Image
                key={index}
                unoptimized
                draggable={false}
                src={`/api/recordings/${recordingId}/thumbnail?index=${index}`}
                width={160}
                height={90}
                alt=""
              />
            ))}
          </div>
        ) : null}
        <div
          className="analysis-shade"
          style={{ left: 0, width: `${(start / duration) * 100}%` }}
        />
        <div
          className="analysis-shade"
          style={{ right: 0, width: `${((duration - end) / duration) * 100}%` }}
        />
        <div
          className="analysis-selection"
          style={{
            left: `${(start / duration) * 100}%`,
            right: `${((duration - end) / duration) * 100}%`,
          }}
        />
        {(["start", "end"] as const).map((edge) => (
          <button
            className="analysis-handle"
            key={edge}
            type="button"
            role="slider"
            aria-label={`Analysis ${edge}`}
            aria-orientation="horizontal"
            aria-valuemin={edge === "start" ? 0 : start + minimum}
            aria-valuemax={edge === "start" ? end - minimum : duration}
            aria-valuenow={edge === "start" ? start : end}
            aria-valuetext={clock(edge === "start" ? start : end)}
            aria-describedby={`${fieldId}-help`}
            disabled={disabled}
            style={{
              left: `${((edge === "start" ? start : end) / duration) * 100}%`,
            }}
            onPointerDown={(event) => {
              if (disabled) return;
              event.preventDefault();
              event.currentTarget.focus();
              event.currentTarget.setPointerCapture(event.pointerId);
            }}
            onPointerMove={(event) => drag(event, edge)}
            onPointerUp={(event) => {
              if (event.currentTarget.hasPointerCapture(event.pointerId))
                event.currentTarget.releasePointerCapture(event.pointerId);
              onPreview(edge === "start" ? start : Math.max(start, end - 0.1));
            }}
            onKeyDown={(event) => keyboard(event, edge)}
          >
            <span aria-hidden="true">Ⅱ</span>
          </button>
        ))}
      </div>
      <p id={`${fieldId}-help`}>
        Drag either edge to choose where AI looks for clips. Arrow keys move one
        second; hold Shift for ten seconds or Alt for finer control.
      </p>
      <Button
        type="button"
        disabled={disabled}
        onClick={() => onChange(0, duration)}
        variant="outline"
      >
        Use full video
      </Button>
    </section>
  );
}
