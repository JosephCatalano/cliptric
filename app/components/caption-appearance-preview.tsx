"use client";

import { useId, type CSSProperties, type ReactNode } from "react";
import {
  Field,
  FieldDescription,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { type CaptionCustomization, type Preset } from "@/lib/projects";

export function CaptionAppearancePreview({
  value,
  preset,
  text = "This is your moment.",
  compact = false,
  showEmphasis = false,
}: {
  value: CaptionCustomization;
  preset: Preset;
  text?: string;
  compact?: boolean;
  showEmphasis?: boolean;
}) {
  const words = text.split(" ");
  const count = { auto: 4, one: 1, two: 2, three: 3, four: 4 }[value.grouping];
  const sample = words.slice(-count).join(" ");
  const displayed =
    value.capitalization === "upper"
      ? sample.toUpperCase()
      : value.capitalization === "lower"
        ? sample.toLowerCase()
        : sample;
  const tokens = displayed.split(" ");
  const textColour =
    value.text_colour ?? (preset === "bold" ? "#ffef00" : "#ffffff");
  const accent =
    value.emphasis_colour ?? (preset === "focus" ? "#66ddee" : "#ffef00");
  const box =
    value.background === "box" ||
    (value.background !== "none" &&
      (preset === "boxed" || preset === "studio"));
  const animation = value.animation ?? "preset";
  const effect =
    animation === "preset"
      ? (({ impact: "pop", studio: "fade" } as Partial<Record<Preset, string>>)[
          preset
        ] ?? preset)
      : animation;
  const size = Number.isFinite(value.font_size)
    ? Math.min(96, Math.max(32, value.font_size))
    : 64;
  const style: CSSProperties = {
    fontFamily:
      value.font_family === "Georgia"
        ? "Georgia, serif"
        : value.font_family === "Courier New"
          ? '"Courier New", monospace'
          : "Arial, sans-serif",
    fontWeight:
      value.font_weight === "regular"
        ? 400
        : value.font_weight === "bold"
          ? 700
          : showEmphasis
            ? 400
            : 700,
    fontSize: `${size * (compact ? 0.3 : 0.45)}px`,
    color: textColour,
    WebkitTextStroke: `${(({ bold: 6, impact: 5, studio: 3 } as Partial<Record<Preset, number>>)[preset] ?? 4) * (compact ? 0.15 : 0.25)}px ${value.outline_colour ?? "#101010"}`,
    paintOrder: "stroke fill",
    textShadow: `1px 1px 2px ${value.shadow_colour ?? "#000000"}80`,
    backgroundColor: box
      ? `${value.background_colour ?? (preset === "studio" ? "#34224c" : "#000000")}${preset === "studio" && !value.background_colour ? "cc" : "80"}`
      : undefined,
    padding: box ? "0.2em 0.4em" : undefined,
    ...{
      "--caption-sample-colour": textColour,
      "--caption-sample-accent": accent,
      "--caption-sample-glow": value.emphasis_colour ?? "#bb99ff",
    },
  };
  return (
    <span
      className={
        compact ? "caption-appearance-sample" : "caption-appearance-stage"
      }
      aria-hidden="true"
    >
      {preset === "none" ? (
        <span className="text-sm text-muted-foreground">Captions off</span>
      ) : (
        <span
          className="caption-appearance-text"
          data-effect={effect}
          style={style}
        >
          {tokens.map((word, index) => {
            const last = index === tokens.length - 1;
            const marked = showEmphasis && last;
            return (
              <span
                key={index}
                style={{
                  animationDelay: `${index * 0.5}s`,
                  color:
                    marked ||
                    ((effect === "flow" || effect === "focus") && last)
                      ? accent
                      : undefined,
                  fontWeight: marked ? 700 : undefined,
                  textShadow:
                    effect === "glow" && last
                      ? `0 0 6px ${value.emphasis_colour ?? "#bb99ff"}`
                      : undefined,
                }}
              >
                {word}
                {last ? "" : " "}
              </span>
            );
          })}
        </span>
      )}
    </span>
  );
}

export function CaptionVisualChoices<T extends string>({
  label,
  name,
  value,
  options,
  onChange,
  disabled,
  description,
  compact = false,
}: {
  label: string;
  name?: string;
  value: string;
  options: readonly { value: T; label: string; sample: ReactNode }[];
  onChange: (value: T) => void;
  disabled?: boolean;
  description?: string;
  compact?: boolean;
}) {
  const id = useId();
  function select(next: string) {
    const option = options.find((item) => item.value === next);
    if (option) onChange(option.value);
  }
  if (compact) {
    return (
      <Field>
        <FieldLabel htmlFor={id}>{label}</FieldLabel>
        <NativeSelect id={id} name={name} value={value} disabled={disabled} className="w-full"
          aria-describedby={description ? `${id}-help` : undefined}
          onChange={(event) => select(event.target.value)}>
          {options.map((option) => <NativeSelectOption key={option.value} value={option.value}>{option.label}</NativeSelectOption>)}
        </NativeSelect>
        {description && <FieldDescription id={`${id}-help`}>{description}</FieldDescription>}
      </Field>
    );
  }
  return (
    <FieldSet disabled={disabled}>
      <FieldLegend id={`${id}-label`} variant="label">
        {label}
      </FieldLegend>
      <RadioGroup
        name={name}
        value={value}
        disabled={disabled}
        aria-labelledby={`${id}-label`}
        aria-describedby={description ? `${id}-help` : undefined}
        className="caption-visual-choices"
        onValueChange={select}
      >
        {options.map((option, index) => (
          <FieldLabel
            key={option.value}
            htmlFor={`${id}-${index}`}
            className="caption-style-card w-full flex-col gap-2"
          >
            {option.sample}
            <span className="caption-style-choice">
              <RadioGroupItem id={`${id}-${index}`} value={option.value} />
              <span>{option.label}</span>
            </span>
          </FieldLabel>
        ))}
      </RadioGroup>
      {description && (
        <FieldDescription id={`${id}-help`}>{description}</FieldDescription>
      )}
    </FieldSet>
  );
}
