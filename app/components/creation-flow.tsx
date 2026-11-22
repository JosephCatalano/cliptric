"use client";

import { useId, useState, type FormEvent, type ReactNode } from "react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import {
  Accordion,
  AccordionItem,
  AccordionTrigger,
  AccordionContent,
} from "@/components/ui/accordion";

export function useCreationFlow(
  sourceReady: boolean,
  styleReady = true,
  busy = false,
) {
  const [step, setStep] = useState(0);
  const [visited, setVisited] = useState(0);
  const formId = useId();
  const canContinue = step === 0 ? sourceReady : styleReady;
  function move(next: number, focusPanel = false) {
    if (busy) return;
    if (next > step) {
      if (!canContinue) return;
      const panel = document
        .getElementById(formId)
        ?.querySelector(`[data-creation-step="${step}"]`);
      const inputs = panel?.querySelectorAll<
        HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement
      >("input, select, textarea");
      for (const input of inputs ?? []) if (!input.reportValidity()) return;
    }
    setStep(next);
    setVisited((value) => Math.max(value, next));
    if (focusPanel)
      requestAnimationFrame(() =>
        document
          .getElementById(formId)
          ?.querySelector<HTMLElement>(`[data-creation-step="${next}"]`)
          ?.focus(),
      );
  }
  function onInvalid(event: FormEvent<HTMLFormElement>) {
    const input = event.target as HTMLInputElement;
    const panel = input.closest<HTMLElement>("[data-creation-step]");
    if (panel && Number(panel.dataset.creationStep) !== step) {
      event.preventDefault();
      setStep(Number(panel.dataset.creationStep));
      requestAnimationFrame(() => {
        input.focus();
        input.reportValidity();
      });
    }
  }
  function submit(
    event: FormEvent<HTMLFormElement>,
    create: (event: FormEvent<HTMLFormElement>) => void,
  ) {
    if (busy) {
      event.preventDefault();
      return;
    }
    if (step < 2) {
      event.preventDefault();
      move(step + 1);
    } else create(event);
  }
  return { step, visited, move, formId, onInvalid, submit, canContinue, busy };
}

type Flow = ReturnType<typeof useCreationFlow>;

export function CreationFlow({
  flow,
  preview,
  children,
}: {
  flow: Flow;
  preview: ReactNode;
  children: ReactNode;
}) {
  return (
    <Tabs
      value={String(flow.step)}
      onValueChange={(value) => flow.move(Number(value))}
    >
      <TabsList aria-label="Creation steps" className="w-full">
        {["Source", "Style", "Create"].map((label, index) => (
          <TabsTrigger
            key={label}
            value={String(index)}
            disabled={
              flow.busy ||
              index > flow.visited + 1 ||
              (index > flow.step && !flow.canContinue)
            }
          >
            {index + 1}. {label}
          </TabsTrigger>
        ))}
      </TabsList>
      <div className="creation-layout">
        <aside className="creation-preview" aria-label="Video preview">
          {preview}
        </aside>
        <div className="min-w-0">{children}</div>
      </div>
    </Tabs>
  );
}

export function CreationPanel({
  value,
  active,
  children,
}: {
  value: number;
  active: number;
  children: ReactNode;
}) {
  return (
    <TabsContent
      value={String(value)}
      forceMount
      data-creation-step={value}
      style={active !== value ? { display: "none" } : undefined}
      className="flex flex-col gap-5"
    >
      {children}
    </TabsContent>
  );
}

export function CreationFooter({
  flow,
  busy,
  action,
}: {
  flow: Flow;
  busy: boolean;
  action: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t pt-4">
      <Button
        type="button"
        variant="outline"
        disabled={flow.step === 0 || busy}
        onClick={() => flow.move(flow.step - 1, true)}
      >
        Back
      </Button>
      {flow.step === 2 ? (
        action
      ) : (
        <Button
          type="button"
          disabled={busy || !flow.canContinue}
          onClick={() => flow.move(flow.step + 1, true)}
        >
          Continue to {flow.step === 0 ? "style" : "create"}
        </Button>
      )}
    </div>
  );
}

export function AdvancedOptions({
  label = "Advanced settings",
  children,
}: {
  label?: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState("");
  return (
    <Accordion type="single" collapsible value={open} onValueChange={setOpen}>
      <AccordionItem value="advanced">
        <AccordionTrigger>{label}</AccordionTrigger>
        <AccordionContent
          forceMount
          hidden={open !== "advanced"}
          onInvalidCapture={(event) => {
            if (!open) {
              event.preventDefault();
              setOpen("advanced");
              const input = event.target as HTMLInputElement;
              requestAnimationFrame(() => {
                input.focus();
                input.reportValidity();
              });
            }
          }}
        >
          <div className="flex flex-col gap-5">{children}</div>
        </AccordionContent>
      </AccordionItem>
    </Accordion>
  );
}
