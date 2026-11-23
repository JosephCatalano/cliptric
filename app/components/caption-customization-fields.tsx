"use client";

import { useId, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Field,
  FieldGroup,
  FieldLabel,
  FieldDescription,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import {
  defaultCaptionCustomization,
  type CaptionCustomization,
  type CaptionSource,
  type Preset,
} from "../../lib/projects";
import { CaptionFilterReview } from "./caption-filter-review";
import {
  CaptionAppearancePreview,
  CaptionVisualChoices,
} from "./caption-appearance-preview";

export function captionCustomizationFromForm(
  form: FormData,
): CaptionCustomization {
  return {
    grouping: String(
      form.get("caption_grouping"),
    ) as CaptionCustomization["grouping"],
    capitalization: String(
      form.get("caption_capitalization"),
    ) as CaptionCustomization["capitalization"],
    font_size: Number(form.get("caption_font_size")),
    font_family: String(
      form.get("caption_font_family"),
    ) as CaptionCustomization["font_family"],
    font_weight: String(
      form.get("caption_font_weight"),
    ) as CaptionCustomization["font_weight"],
    animation: String(
      form.get("caption_animation"),
    ) as CaptionCustomization["animation"],
    background: String(
      form.get("caption_background"),
    ) as CaptionCustomization["background"],
    profanity: String(
      form.get("caption_profanity"),
    ) as CaptionCustomization["profanity"],
    censor_words: String(form.get("caption_censor_words") ?? "")
      .split(",")
      .map((word) => word.trim())
      .filter(Boolean),
    replacement: String(form.get("caption_replacement") ?? "[censored]"),
    ...Object.fromEntries(
      [
        "text_colour",
        "outline_colour",
        "shadow_colour",
        "background_colour",
        "emphasis_colour",
      ].map((key) => [key, String(form.get(`caption_${key}`) ?? "") || null]),
    ),
  };
}

export function CaptionCustomizationFields({
  value,
  defaultValue = defaultCaptionCustomization,
  onChange,
  disabled = false,
  source,
  preset = "classic",
  compact = false,
}: {
  value?: CaptionCustomization;
  defaultValue?: CaptionCustomization;
  onChange?: (value: CaptionCustomization) => void;
  disabled?: boolean;
  source?: CaptionSource;
  preset?: Preset;
  compact?: boolean;
}) {
  const id = useId();
  const [local, setLocal] = useState(defaultValue);
  const current = value ?? local;
  function change(next: CaptionCustomization) {
    setLocal(next);
    onChange?.(next);
  }
  function sample(overrides: Partial<CaptionCustomization> = {}) {
    return (
      <CaptionAppearancePreview
        value={{ ...current, ...overrides }}
        preset={preset === "none" ? "classic" : preset}
        compact
      />
    );
  }
  return (
    <FieldGroup>
      <Field>
        <span className="text-sm font-medium">Your caption look</span>
        <CaptionAppearancePreview
          value={current}
          preset={preset}
          showEmphasis
        />
        <FieldDescription>
          Sample text with an emphasized word. Changes appear here as you
          choose. Browser fonts and effects are approximate; review your
          rendered clip for final timing and line breaks.
        </FieldDescription>
      </Field>
      <CaptionVisualChoices
        compact={compact}
        label="Words per caption"
        name="caption_grouping"
        value={current.grouping}
        disabled={disabled}
        options={(
          [
            ["auto", "Automatic phrases"],
            ["one", "One word"],
            ["two", "Up to 2 words"],
            ["three", "Up to 3 words"],
            ["four", "Up to 4 words"],
          ] as const
        ).map(([grouping, label]) => ({
          value: grouping,
          label,
          sample: sample({ grouping }),
        }))}
        onChange={(grouping) => change({ ...current, grouping })}
        description="Pauses, punctuation, and available space can shorten each group."
      />
      <CaptionVisualChoices
        compact={compact}
        label="Capitalization"
        name="caption_capitalization"
        value={current.capitalization}
        disabled={disabled}
        options={(
          [
            ["original", "Original text"],
            ["upper", "UPPERCASE"],
            ["lower", "lowercase"],
          ] as const
        ).map(([capitalization, label]) => ({
          value: capitalization,
          label,
          sample: sample({ capitalization, grouping: "auto" }),
        }))}
        onChange={(capitalization) => change({ ...current, capitalization })}
      />
      <CaptionVisualChoices
        compact={compact}
        label="Font family"
        name="caption_font_family"
        value={current.font_family ?? "Arial"}
        disabled={disabled}
        options={(["Arial", "Georgia", "Courier New"] as const).map(
          (font_family) => ({
            value: font_family,
            label: font_family,
            sample: sample({ font_family, grouping: "auto" }),
          }),
        )}
        onChange={(font_family) => change({ ...current, font_family })}
      />
      <CaptionVisualChoices
        compact={compact}
        label="Font weight"
        name="caption_font_weight"
        value={current.font_weight ?? "preset"}
        disabled={disabled}
        options={(
          [
            ["preset", "Use preset"],
            ["regular", "Regular"],
            ["bold", "Bold"],
          ] as const
        ).map(([font_weight, label]) => ({
          value: font_weight,
          label,
          sample: sample({ font_weight, grouping: "auto" }),
        }))}
        onChange={(font_weight) => change({ ...current, font_weight })}
      />
      {!compact && (
      <CaptionVisualChoices
        compact={compact}
        label="Caption size"
        value={String(current.font_size)}
        disabled={disabled}
        options={([32, 48, 64, 80, 96] as const).map((font_size) => ({
          value: String(font_size),
          label: `${font_size} px`,
          sample: sample({ font_size, grouping: "two" }),
        }))}
        onChange={(font_size) =>
          change({ ...current, font_size: Number(font_size) })
        }
        description="Choose a size or enter an exact value below."
      />
      )}
      <Field>
        <FieldLabel htmlFor={`${id}-size`}>Exact font size</FieldLabel>
        <Input
          id={`${id}-size`}
          name="caption_font_size"
          type="number"
          min={32}
          max={96}
          step={1}
          required
          disabled={disabled}
          value={current.font_size}
          onChange={(event) =>
            change({ ...current, font_size: Number(event.target.value) })
          }
        />
        <FieldDescription>
          32–96 pixels on the 1080 × 1920 export. Dense captions shrink to stay
          within safe margins. Review the rendered export for final appearance.
        </FieldDescription>
      </Field>
      <CaptionVisualChoices
        compact={compact}
        label="Animation"
        name="caption_animation"
        value={current.animation ?? "preset"}
        disabled={disabled}
        options={(
          [
            ["preset", "Use preset"],
            ["none", "None"],
            ["pop", "Pop"],
            ["fade", "Fade"],
          ] as const
        ).map(([animation, label]) => ({
          value: animation,
          label,
          sample: sample({ animation, grouping: "auto" }),
        }))}
        onChange={(animation) => change({ ...current, animation })}
        description={compact ? "Pop and fade animate each phrase." : "Hover or focus to see motion. Pop and fade animate phrases; None disables preset timed word effects. Reduced-motion preferences are respected."}
      />
      <CaptionVisualChoices
        compact={compact}
        label="Caption background"
        name="caption_background"
        value={current.background ?? "preset"}
        disabled={disabled}
        options={(
          [
            ["preset", "Use preset"],
            ["none", "No box"],
            ["box", "Translucent box"],
          ] as const
        ).map(([background, label]) => ({
          value: background,
          label,
          sample: sample({ background, grouping: "auto" }),
        }))}
        onChange={(background) => change({ ...current, background })}
      />
      <details>
        <summary>Colours and caption filters</summary>
        <FieldGroup>
          {(
            [
              ["text_colour", "Text", "#ffffff"],
              ["outline_colour", "Outline", "#101010"],
              ["shadow_colour", "Shadow", "#000000"],
              ["background_colour", "Background", "#000000"],
              ["emphasis_colour", "Emphasized / active words", "#ffef00"],
            ] as const
          ).map(([key, label, fallback]) => (
            <Field key={key}>
              <FieldLabel htmlFor={`${id}-${key}`}>{label} colour</FieldLabel>
              <CaptionAppearancePreview
                value={
                  key === "background_colour"
                    ? { ...current, background: "box" }
                    : current
                }
                preset={preset === "none" ? "classic" : preset}
                compact
                showEmphasis={key === "emphasis_colour"}
              />
              <input
                type="hidden"
                name={`caption_${key}`}
                value={current[key] ?? ""}
              />
              <Input
                id={`${id}-${key}`}
                type="color"
                value={current[key] ?? fallback}
                disabled={disabled}
                onChange={(event) =>
                  change({ ...current, [key]: event.target.value })
                }
              />
              <Button
                type="button"
                variant="outline"
                disabled={disabled || !current[key]}
                onClick={() => change({ ...current, [key]: null })}
              >
                Use preset colour
              </Button>
              <FieldDescription>
                {current[key]
                  ? current[key]
                  : "Using the preset colour. Choose a colour to override it."}
              </FieldDescription>
            </Field>
          ))}
          <Field>
            <FieldLabel htmlFor={`${id}-profanity`}>
              Caption profanity
            </FieldLabel>
            <NativeSelect
              id={`${id}-profanity`}
              name="caption_profanity"
              value={current.profanity ?? "keep"}
              disabled={disabled}
              onChange={(event) =>
                change({
                  ...current,
                  profanity: event.target
                    .value as CaptionCustomization["profanity"],
                })
              }
            >
              <NativeSelectOption value="keep">
                Keep original words
              </NativeSelectOption>
              <NativeSelectOption value="symbols">
                Censor with symbols
              </NativeSelectOption>
              <NativeSelectOption value="replace">
                Replace displayed words
              </NativeSelectOption>
            </NativeSelect>
            <FieldDescription>
              Changes captions only. Audio remains unchanged. Review detection
              before publishing.
            </FieldDescription>
          </Field>
          <Field>
            <FieldLabel htmlFor={`${id}-censor-words`}>
              Additional words to censor
            </FieldLabel>
            <Input
              id={`${id}-censor-words`}
              name="caption_censor_words"
              maxLength={1000}
              disabled={disabled}
              value={(current.censor_words ?? []).join(", ")}
              onChange={(event) =>
                change({
                  ...current,
                  censor_words: event.target.value.split(","),
                })
              }
            />
            <FieldDescription>
              Up to 20 comma-separated words. Default exact-word list: fuck,
              fucking, fucked, shit, bullshit, bitch, asshole, damn. Matching
              ignores case; it does not infer context or catch every variant.
            </FieldDescription>
          </Field>
          <Field>
            <FieldLabel htmlFor={`${id}-replacement`}>
              Replacement text
            </FieldLabel>
            <Input
              id={`${id}-replacement`}
              name="caption_replacement"
              maxLength={24}
              required
              disabled={disabled}
              value={current.replacement ?? "[censored]"}
              onChange={(event) =>
                change({ ...current, replacement: event.target.value })
              }
            />
          </Field>
          {source && (
            <CaptionFilterReview
              source={source}
              customization={current}
              disabled={disabled}
            />
          )}
        </FieldGroup>
      </details>
    </FieldGroup>
  );
}
