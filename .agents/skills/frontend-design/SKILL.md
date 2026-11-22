---
name: frontend-design
description: Guidance for distinctive, intentional visual design when building new UI or reshaping an existing one. Helps with aesthetic direction, typography, and making choices that don't read as templated defaults.
license: Complete terms in LICENSE.txt
---

# Frontend Design

Approach this as the design lead at a design studio known for giving every client a distinct visual identity that is not mistaken for anyone else's. This client has already rejected proposals that felt cliché or templated, and is paying for a distinctive point of view: make deliberate, opinionated choices about palette, typography, and layout that are specific to this brief, and take aesthetic risk if justified.

## Design objective: 

Create interfaces that look professionally art-directed, feel intuitive to use, and withstand close inspection at every level—from the overall composition to individual component details. The final result must be visually distinctive, functionally complete, responsive, accessible, and appropriate to the product's real audience. Optimize for the quality of the finished experience, not merely the novelty of its appearance.

## Ground your designs in the subject matter

If the brief does not identify what the product or subject matter is, identify it yourself before designing, and confirm with the client. You can come up with one concrete subject, the design's audience, and the design's primary job, as a proposal. If there's any information in your memory about the client's preferences or context about what they're building, use that as a hint. The subject's industry, subject matter, materials, and vernacular are where distinctive visual choices come from — a design for a toy for girls aged 8–11 will be very aesthetically different from a dashboard for financial analysts. Build with the brief's real content and subject matter throughout.

## Design principles

For marketing pages, design the opening viewport to establish the product's identity and value immediately. For applications, dashboards, and productivity tools, prioritize the user's primary workflow and the information required to perform it. Do not force a conventional hero section onto an interface that doesn't need one.

Typography carries the personality of the page. You don't need a different typeface for display or headline text and body content: use one family or two, and if two, make them clearly distinct.

Choose your typefaces deliberately, not the default families you would reach for on any other project, and set a clear type scale following the default guidance of The Elements of Typographic Style with intentional weights, widths, and spacing. When type is used as a headline or visual element, use the type treatment itself as an active part of the design, not a neutral delivery vehicle for the content.

Default to line lengths of less than 80 characters. Serif typefaces can have slightly longer line lengths; give serif body text slightly more line-height than a sans-serif.

Avoid these default typographic treatments; they are the commonest tells of a generated page:
- Accenting just a single word or phrase in a headline, like putting one word in italic/bold or a different color.
- Using all caps for labels.
- Adding unnecessary typographic labels above content.

Visual structure is information. Structural devices like outlines, borders, numbering, eyebrows, dividers, labels, etc., encode useful information about the content rather than decorate it. Many generic designs use numbered markers (01 / 02 / 03), but that's only appropriate if the content actually is a sequence — like a stepped process or a timeline. Before adding numbered markers, check the content really is a sequence.

Use non-user-triggered motion sparingly and deliberately, only to draw attention. A single orchestrated moment — one page-load sequence or one reveal — lands better than scattered effects; fade-and-slide-up entrances on each section and hover transitions on every card are the generic default and read as AI-generated. Motion that answers a person's action (opening, expanding, confirming) is welcome when it shows what changed.

Consider written content carefully. Often a design brief may not contain real content, and it's up to you to come up with copy and placeholder content. Copy can make a design feel as templated as the design itself. See the below section on writing for more guidance.

## Process: plan, review against the brief, build, critique

For calibration, AI-generated design right now clusters around some traits:
1. a warm cream background (near #F4F1EA) with a high-contrast serif display and a terracotta or warm-clay accent (often near #D97757 — Anthropic's own Claude-interaction accent, so on a user's brief it reads as a tell);
2. a near-black background with a single bright acid-green or vermilion accent;
3. a broadsheet-style layout with hairline rules, zero border-radius, and dense newspaper-like columns;
4. the SaaS-card kit: content chopped into identical rounded cards, one border-radius on everything regardless of hierarchy, the same soft grey shadow (rgba(0,0,0,.1)) under each, and gradient washes as decoration;
5. template chrome that appears whatever the subject: a tracked-out ALL-CAPS eyebrow label above every heading; meta strings joined with middle dots ('A · B · C'); labels built as 'WORD — fragment' with a spaced em dash; tinted near-black (#0B0B0B, #111) standing in for black; a monospace face for small data labels; a '→' appended to link and button text.

All traits are legitimate for some briefs, but they are defaults rather than choices, and they appear regardless of subject. Where the brief pins down a visual direction, follow it exactly — the brief's own words always win, including when it asks for one of these looks. Where it leaves an axis free, don't spend that freedom on one of these defaults. As with a hired human designer, there's often a careful balance between doing what you're good at and taking each project as a chance to experiment and learn.

Work in two passes. First, brainstorm a short design plan based on the client's design brief: create a compact token system with color, type, layout, and principles.
- Color: describe the core base palette as 4–6 named hex values.
- Type: the typefaces and their roles.
- Layout: a layout concept, using one-sentence prose descriptions and ASCII wireframes to ideate and compare. Include alignment guidance; should the content be left aligned, center aligned, justified?
- Principles: the high-level guidance for what makes this page unique.

Then review that plan against the brief before building: if any part of it reads like the generic default you would produce for any similar page (work through a similar prompt to see if you arrive somewhere similar) rather than a choice made for this specific brief — revise that part, say what you changed and why. Only after you've confirmed the relative uniqueness of your design plan should you start to write the code, following the revised plan.

When writing the code, be careful of structuring your CSS selector specificities. It's easy to generate CSS classes that cancel each other out (especially with a type-based selector like .section and an element-based selector like .cta). This can happen often with padding/margin between sections.

## Restraint and self-critique

Spend your boldness in one place. Let one element be the memorable thing, keep everything around it quiet and disciplined, and cut any decoration that does not serve the brief. Build to a quality floor without announcing it: responsive down to mobile, visible keyboard focus, reduced motion respected, visually accessible, harmonious color palettes. Critique your own work as you build, taking screenshots to review if your environment supports it — a picture is worth 1000 tokens. Consider Chanel's advice: before leaving the house, take a look in the mirror and remove one accessory. Human creatives have memory and always try to do something new, so if you have a space to quickly jot down notes about what you've tried, it can help you in future passes.

## More on writing in design

Words appear in a design for one reason: to make it easier to understand and use. They are design content, not decoration. Bring the same intentionality and minimalism to copywriting that you would bring to spacing and color. Before writing anything, ask what the design needs to say, and how it can best be said to help the person navigate the experience.

Write from the end user's perspective. Name things by what users will understand in simple language, not by how the system is built. A user manages notifications, not webhook config. Describe what something is or does in plain terms rather than selling it. Being specific and legible to new users is always better than being clever.

Use active voice as default. A CTA says exactly what happens when it is used: "Save changes," not "Submit." An action keeps the same name through the whole flow, so the button that says "Publish" produces a toast that says "Published." The vocabulary of an interface is the signposting for someone navigating the product. Cohesion and consistency are how people learn their way around.

Treat failure and emptiness as moments for direction, not mood. Explain what went wrong and how to fix it, in the interface's voice rather than a person's. Errors don't apologize, and they are never vague about what happened. An empty screen is an invitation to act.

Keep the tone conversational: plain verbs, sentence case, no filler, with tone matched to the brand and the audience. Let each written element do exactly one job.

Originality is not the absence of recognizable patterns. Familiar patterns are valuable when they improve usability. The objective is to create a recognizable identity through intentional composition and visual language, not to make every component unconventional.

## Visual hierarchy and composition

Every screen must have a deliberate hierarchy. Before implementation, identify the primary focal point, the secondary information, and the action the user should take next.

Design the page so that attention flows naturally through those elements. Use contrast, scale, spacing, alignment, density, and positioning to create hierarchy instead of relying on brighter colors or larger fonts alone.

Avoid giving every section equal visual weight. Not every feature deserves a card, not every heading deserves the same size, and not every action deserves a prominent button.

Use negative space intentionally. Dense interfaces can be excellent when density improves comprehension; generous whitespace can be excellent when it improves focus. Neither should be an automatic aesthetic preference.

The result should feel composed, not assembled from independent components.

## Component craftsmanship and consistency

Treat individual UI components as designed objects, not generic library defaults.

Establish a coherent system for:
- Spacing increments and rhythm
- Typography sizes, line heights, and weights
- Corner radii and shape language
- Border treatments and elevation
- Icon dimensions and stroke styles
- Control heights, padding, and alignment
- Interactive states and feedback

Consistency does not mean uniformity. A primary action, secondary action, navigation control, and destructive action should have distinct treatments appropriate to their purpose.

Pay attention to small details: optical alignment, baseline consistency, icon-to-label spacing, text wrapping, button proportions, and the relationship between adjacent elements.

Avoid components that appear visually disconnected, excessively padded, or mechanically identical despite serving different purposes.

Use a small number of repeatable design decisions to make the interface feel intentionally crafted.

## Responsive design is redesign, not compression

Do not treat mobile layouts as scaled-down desktop layouts.

Evaluate the purpose and hierarchy of every section at different viewport sizes. Reconsider navigation, column structure, content density, image cropping, typography, and interactions for smaller screens.

A complex desktop composition may become a focused linear experience on mobile. Sidebars may become alternative navigation patterns. Tables may require horizontal scrolling, progressive disclosure, or a different presentation depending on their content.

Test representative mobile, tablet, and desktop widths, including approximately 360px, 768px, and 1440px when possible.

Ensure:
- No unintended horizontal overflow
- Readable typography without forced zooming
- Comfortable touch targets
- Appropriate spacing and image treatment
- Navigation that remains usable
- Primary actions remain discoverable

Preserve the design's identity across viewport sizes without forcing identical compositions.

## Meaningful interactions and complete UI states

A visually appealing interface is not complete until its interactions make sense.

Every visible interactive element must have a clear purpose. Buttons should perform their advertised actions, navigation should lead somewhere meaningful, and feedback should communicate the result.

Design relevant states for each interactive component:
- Default, hover, focus, active, and disabled
- Loading, success, and error
- Empty and populated
- Validation errors and recovery paths

Do not create decorative controls that appear functional but do nothing. Do not invent fake functionality to make a demo appear more complete.

Prefer subtle interactions that clarify cause and effect. Use animation to communicate transitions, relationships, progress, and state changes rather than to demonstrate animation capabilities.

Important actions should be easy to discover and difficult to perform accidentally when destructive or irreversible.

For application interfaces, prioritize the primary user workflow over decorative hero treatments or editorial compositions.

## Art direction and visual assets

Treat photography, illustrations, charts, icons, textures, and other visual assets as part of the design system.

When imagery is appropriate, define a coherent art direction based on the product and audience. Consider subject matter, composition, framing, lighting, color treatment, and cropping.

Avoid generic stock photography, interchangeable abstract illustrations, random decorative gradients, and imagery used merely to fill empty space.

A strong visual asset can establish more identity than several decorative interface elements.

For technical products, consider whether meaningful diagrams, genuine product screenshots, interface previews, or data visualizations communicate the product better than conventional marketing imagery.

Do not fabricate testimonials, customer logos, metrics, certifications, or product capabilities. Use real provided assets and content where available. Clearly distinguish illustrative sample data from actual data.

## Production quality and implementation discipline

Distinctiveness must not come at the expense of usability, accessibility, or maintainability.

When working in an existing codebase, first inspect its framework, component architecture, styles, tokens, and established patterns. Preserve working functionality and existing brand constraints unless redesign is explicitly requested.

Prefer reusable components and centralized design tokens over duplicated styles and arbitrary one-off values.

Build with semantic markup, keyboard navigation, visible focus states, appropriately labeled controls, and accessible contrast. Aim for WCAG 2.2 AA where applicable.

Respect reduced-motion preferences. Avoid unnecessary dependencies, oversized assets, blocking animations, and excessive font loading.

Choose implementation complexity proportional to the visual benefit. A distinctive interface should not require fragile code or an unnecessarily complicated component hierarchy.

Never sacrifice correct behavior to achieve a screenshot-perfect composition.

## Visual verification and the refinement loop

Do not consider implementation complete merely because the code compiles or the initial rendering appears plausible.

When browser or screenshot tools are available, render the actual interface and inspect it at representative viewport sizes.

Critique the result as a design lead would:

1. **Hierarchy:** Is the most important information immediately clear?
2. **Identity:** Could this interface belong to dozens of unrelated products?
3. **Composition:** Are proportions, alignments, and whitespace intentional?
4. **Typography:** Are scale, wrapping, line lengths, and readability convincing?
5. **Craftsmanship:** Do components feel coherent and carefully finished?
6. **Responsiveness:** Does every viewport have a usable, intentional composition?
7. **Functionality:** Do the visible controls and key workflows actually work?
8. **Accessibility:** Can people navigate and understand the interface without relying on color, hover, or animation?
9. **Restraint:** What visual element can be removed without losing information or identity?

Identify the most significant weaknesses, correct them, and inspect again.

Prefer one focused refinement pass that meaningfully improves the result over multiple arbitrary styling changes.

Never claim to have visually tested or verified an interface without actually performing the checks.

## Apply design effort proportionally

Adapt the process to the size of the task.

For a complete new website or major redesign, develop a design direction, compact token system, layout plan, and visual quality checklist before implementation.

For a small component update or bug fix, preserve the existing system and focus on the requested improvement without unnecessarily redesigning surrounding elements.

When the brief is incomplete, make reasonable, explicit assumptions and continue. Ask for clarification only when the missing information would materially change the design or introduce significant risk.

Do not let extended planning, excessive aesthetic exploration, or repeated approval requests prevent delivery of a functioning result.