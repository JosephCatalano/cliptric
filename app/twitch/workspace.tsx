"use client";

import { useState } from "react";
import { useSearchParams } from "next/navigation";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { TwitchDiscovery } from "../discover/twitch-discovery";
import { AutopilotWorkspace } from "../autopilot/workspace";

type Section = "find" | "automation" | "review";

export function TwitchWorkspace() {
  const search = useSearchParams();
  const requested = search.get("tab");
  const section: Section =
    requested === "automation" || requested === "review" ? requested : "find";
  const [visited, setVisited] = useState<Section[]>([]);

  function open(next: Section, scan?: string) {
    setVisited((previous) => [...new Set([...previous, section, next])]);
    const params = new URLSearchParams();
    if (next !== "find") params.set("tab", next);
    if (scan) params.set("scan", scan);
    window.history.pushState(
      null,
      "",
      params.size ? `/twitch?${params}` : "/twitch",
    );
  }

  return (
    <Tabs
      activationMode="manual"
      value={section}
      onValueChange={(value) => open(value as Section)}
    >
      <TabsList aria-label="Twitch workflow" className="max-w-full">
        <TabsTrigger value="find">Find clips</TabsTrigger>
        <TabsTrigger value="automation">Automation</TabsTrigger>
        <TabsTrigger value="review">Review queue</TabsTrigger>
      </TabsList>
      <TabsContent value="find" forceMount hidden={section !== "find"}>
        {(section === "find" || visited.includes("find")) && (
          <TwitchDiscovery
            visible={section === "find"}
            onAutomate={(scan) => open("automation", scan)}
          />
        )}
      </TabsContent>
      <TabsContent
        value="automation"
        forceMount
        hidden={section !== "automation"}
      >
        {(section === "automation" || visited.includes("automation")) && (
          <AutopilotWorkspace
            view="automation"
            visible={section === "automation"}
            suggestedScan={search.get("scan")}
          />
        )}
      </TabsContent>
      <TabsContent value="review" forceMount hidden={section !== "review"}>
        {(section === "review" || visited.includes("review")) && (
          <AutopilotWorkspace view="review" visible={section === "review"} />
        )}
      </TabsContent>
    </Tabs>
  );
}
