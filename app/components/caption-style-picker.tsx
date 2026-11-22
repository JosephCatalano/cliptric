"use client";

import { useId } from "react";
import {
  FieldDescription,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { cn } from "@/lib/utils";
import { presets, type Preset } from "../../lib/projects";

type Props = { name: string; disabled?: boolean } & (
  | { value: Preset; onChange: (preset: Preset) => void; defaultValue?: never }
  | { defaultValue: Preset; value?: never; onChange?: never }
);
const descriptions: Record<Preset, string> = {
  classic: "White text with a dark outline",
  bold: "Yellow text with a heavier outline",
  boxed: "White text on a dark background",
  flow: "The spoken word highlights yellow in time with your audio",
  glow: "A soft violet glow follows the spoken word",
  none: "Keep the video free of captions",
  impact: "Bold white lettering, a crisp outline and a restrained entrance pop",
  studio: "A deep violet backing with a smooth, synchronized fade",
  focus:
    "Cyan emphasis follows each spoken word against a precise dark outline",
};
export function CaptionStylePicker({
  name,
  value,
  defaultValue,
  onChange,
  disabled,
}: Props) {
  const id = useId();
  const selected = value ?? defaultValue;
  const choices =
    selected && ["classic", "bold", "boxed"].includes(selected)
      ? [...presets, { value: selected, label: "Saved legacy style" }]
      : presets;
  return (
    <FieldSet className="caption-style-picker" disabled={disabled}>
      <FieldLegend id={`${id}-title`}>Caption style</FieldLegend>
      <FieldDescription id={`${id}-help`}>
        Your clips use the words from your video. Hover or focus a card for a
        short motion example.
      </FieldDescription>
      <RadioGroup
        name={name}
        value={value}
        defaultValue={defaultValue}
        disabled={disabled}
        aria-labelledby={`${id}-title`}
        aria-describedby={`${id}-help`}
        className="caption-style-grid gap-4"
        onValueChange={(next) => {
          const preset = choices.find((item) => item.value === next);
          if (preset) onChange?.(preset.value);
        }}
      >
        {choices.map((preset) => (
          <FieldLabel
            key={preset.value}
            htmlFor={`${id}-${preset.value}`}
            className="caption-style-card w-full flex-col gap-3"
          >
            <span className="caption-style-preview" aria-hidden="true">
              <span
                className={cn(
                  "caption-style-example",
                  `caption-example-${preset.value}`,
                )}
              >
                {preset.value === "none"
                  ? "Captions off"
                  : ["This", "is", "your", "moment."].map((word, index) => (
                      <span
                        key={word}
                        className="caption-preview-word"
                        style={{ animationDelay: `${index * 0.5}s` }}
                      >
                        {word}
                        {index < 3 ? " " : ""}
                      </span>
                    ))}
              </span>
            </span>
            <span className="caption-style-choice">
              <RadioGroupItem
                id={`${id}-${preset.value}`}
                value={preset.value}
                aria-describedby={`${id}-${preset.value}-description`}
              />
              <span>{preset.label}</span>
            </span>
            <span
              id={`${id}-${preset.value}-description`}
              className="caption-style-description"
            >
              {descriptions[preset.value]}
            </span>
          </FieldLabel>
        ))}
      </RadioGroup>
    </FieldSet>
  );
}
