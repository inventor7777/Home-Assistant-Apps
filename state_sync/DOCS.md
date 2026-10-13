# State Sync

Start the app and open its web UI. Create an Entity or Time sync, select your target entities, set their desired state, and optionally add conditions or an alternate desired state.

Higher priorities win independently for each target. Unknown sources/conditions skip the sync by default; unavailable targets are skipped. Snoozes clear on restart; disabled syncs stay disabled.

Definitions are saved in `syncs.yaml` in this app’s config directory. Manual changes reload automatically; invalid configuration pauses enforcement. App options control logging, retry timing, and light/dark accent colors.

See the [repository documentation](https://github.com/inventor7777/State-Sync-HA#readme) for YAML examples, supported domains, custom service conditions, and installation/development instructions.
