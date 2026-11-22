# Cliptric colour reference

These files are the approved colour references from the Cliptric brand kit.

- [cliptric-theme.css](./cliptric-theme.css) defines the light and dark interface colours, brand gradients, editor colours, and chart colours. The app imports this file directly.
- [cliptric-tokens.json](./cliptric-tokens.json) provides the palette in a machine-readable format for design tools and other integrations.
- [contrast-report.json](./contrast-report.json) records the kit's text contrast ratios for selected foreground and background pairs.

The app translates the palette into shadcn and editor roles in [app/globals.css](../../../app/globals.css). The header switch sets `data-theme` on the root element, which drives the palette, Tailwind dark variants, and the matching wordmark. Use the semantic roles when styling components so they respond to either theme.

Editor colours style the editing interface. Caption previews show the saved caption style used for export.
