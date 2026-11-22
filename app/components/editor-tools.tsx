"use client";

import { useState, type ReactNode } from "react";
import { CaptionsIcon, SparklesIcon } from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

export function EditorTools({
  transcript,
  highlights,
}: {
  transcript: ReactNode;
  highlights: ReactNode;
}) {
  const [active, setActive] = useState("transcript");
  return (
    <Tabs value={active} onValueChange={setActive} className="editor-tools">
      <TabsList aria-label="Editing tools" className="max-w-full">
        <TabsTrigger value="transcript">
          <CaptionsIcon data-icon="inline-start" />
          Transcript &amp; captions
        </TabsTrigger>
        <TabsTrigger value="highlights">
          <SparklesIcon data-icon="inline-start" />
          Suggested moments
        </TabsTrigger>
      </TabsList>
      <TabsContent
        value="transcript"
        forceMount
        hidden={active !== "transcript"}
      >
        {transcript}
      </TabsContent>
      <TabsContent
        value="highlights"
        forceMount
        hidden={active !== "highlights"}
      >
        {highlights}
      </TabsContent>
    </Tabs>
  );
}
