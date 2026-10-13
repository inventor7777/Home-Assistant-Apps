# Changelog

## 0.1.2

- Allow unfinished drafts to switch between YAML and Form while keeping strict save validation.
- Separate the editor into Triggers, Conditions, and Actions; keep its header and save controls visible while scrolling.
- Search entities only after selecting Add entity, with bounded results and no full entity list in the DOM.
- Remove homepage branding and tagline; collapse advanced editor options.

## 0.1.1

- Restore the container environment in the startup script so Python receives the Supervisor token under s6-overlay.

## 0.1.0

- Initial experimental app with entity/time reconciliation, multi-target priority resolution, gated alternate states, YAML persistence, ingress editor, light/dark themes, and transient snoozes.
