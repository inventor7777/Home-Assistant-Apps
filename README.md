# Home Assistant Apps

Home Assistant apps maintained by Gabriel ([inventor7777](https://github.com/inventor7777)). Requires Home Assistant with Supervisor, normally Home Assistant OS, on `aarch64` or `amd64`.

| App | Description |
| --- | --- |
| [vnStat](vnstat/README.md) | Persistent network traffic monitoring with an ingress dashboard and CLI. |
| [State Sync](state_sync/README.md) | Maintain desired entity states with entity/time syncs, conditions, priorities, and snoozes. Experimental. |

## Install

[![Add repository to Home Assistant](https://my.home-assistant.io/badges/supervisor_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Finventor7777%2FHome-Assistant-Apps)

Or add `https://github.com/inventor7777/Home-Assistant-Apps` in **Settings → Apps → App store → Repositories**. Install the app, review its options, start it, and open its web UI through Home Assistant ingress.

Supervisor builds both containers locally; no registry account is required. Installation needs internet access for the base images and Alpine packages.

## Moving from the separate repositories

The previous repositories were [vnstat-ha](https://github.com/inventor7777/vnstat-ha) and [State-Sync-HA](https://github.com/inventor7777/State-Sync-HA). App slugs remain `vnstat` and `state_sync`, but Home Assistant derives repository identifiers from the repository URL. Apps installed from this repository are separate installations; adding it does not move existing options or data automatically.

1. Back up Home Assistant, including the old apps, and record their app options. Keep the old installations until the replacements work.
2. Stop the old apps and turn off their start-on-boot/watchdog settings before starting the replacements. Do not run two State Sync engines enforcing the same entities.
3. Add this repository, install the replacement apps, and copy their options. Keep the replacements stopped while copying data.
4. **State Sync:** copy `syncs.yaml` from `/app_configs/<old_repository>_state_sync/` to `/app_configs/<new_repository>_state_sync/`. Each is mounted as `/config` inside its respective app. Preserve file permissions. Snoozes are temporary and do not migrate.
5. **vnStat:** if you need existing traffic history, copy the old app's `/data/vnstat/` directory into the replacement app's `/data/vnstat/`, preserving ownership and permissions while both daemons are stopped. These are separate private app volumes; copying requires host access. If you skip this step, the replacement begins collecting fresh history.
6. Start the replacements and verify State Sync definitions/options and vnStat traffic history. Remove the old installations and repository entries only after verification.

See [Home Assistant's repository documentation](https://developers.home-assistant.io/docs/apps/repository/) and [app configuration documentation](https://developers.home-assistant.io/docs/apps/configuration/) for the repository layout and config directory naming.

## Development

App build contexts remain in `vnstat/` and `state_sync/`. See each app's README for details. From the repository root:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r state_sync/requirements.txt
.venv/bin/python -m unittest discover -s tests -v
node --check state_sync/app/static/app.js
node tests/test_ui.cjs
bash -n vnstat/run.sh vnstat/vnstat-cli
sh -n state_sync/run.sh
python3 -m py_compile vnstat/app/server.py
```

The GitHub Actions test workflow runs the same automated checks. Container builds and migration on a real Supervisor installation need separate verification.
