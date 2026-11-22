import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";

export function ScheduleFields({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <Field>
      <FieldLabel htmlFor="scheduled-time">Publish time (optional)</FieldLabel>
      <Input
        id="scheduled-time"
        type="datetime-local"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
      <FieldDescription>
        {value
          ? "Automatic upload starts at this time in your device’s local time zone. Keep the local API running and your computer awake. Platform processing can delay publication."
          : "Leave blank to publish now, or choose a future time to schedule."}
      </FieldDescription>
    </Field>
  );
}
