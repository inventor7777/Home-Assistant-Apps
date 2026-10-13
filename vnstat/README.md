# vnStat HA App

- `vnstatd` stores its database in the app's accessible config directory so history survives app restarts and Home Assistant host reboots.
- A light ingress web UI shows data pulled from `vnstat --json`, and also exposes CLI output and the true CLI inside the container.
- The app can auto-detect the primary network interface or let you pin one explicitly.

## Accessing the true CLI

From the Home Assistant host or Terminal app, the simplest pattern is:

```sh
docker exec -it "$(docker ps --filter name=vnstat --format "{{.Names}}")" vnstat-cli
```

Useful variants:

```bash
docker exec -it "$(docker ps --filter name=vnstat --format "{{.Names}}")" vnstat-cli -d
docker exec -it "$(docker ps --filter name=vnstat --format "{{.Names}}")" vnstat-cli -m
docker exec -it "$(docker ps --filter name=vnstat --format "{{.Names}}")" vnstat-cli --json
```

If you use the terminal often, you can add this to your Terminal init commands to get native-like CLI behavior:

```bash
echo 'alias vnstat='\''docker exec -it "$(docker ps --filter name=vnstat --format "{{.Names}}")" vnstat-cli'\''' >> /root/.zshrc
```

Since this is the standard vnStat, all CLI commands should be available. To see all the commands, [see vnStat's official website.](https://humdi.net/vnstat/)

## Technical Info

- If `interface` is blank, the app's UI tries the default-route interface first and then the first non-loopback interface.
- Data is persisted under `/config/vnstat` inside the app, accessible at `/app_configs/<repository>_vnstat/vnstat/` in Home Assistant. Stop the app before copying or replacing its database.
- When moving from the separate vnstat-ha repository, stop both apps and copy the old database into the new app’s config folder manually; repository installations have separate storage. Without a manual copy, this app starts collecting fresh history.
- The UI is available through standard Ingress and follows the active Home Assistant theme. Standalone or cross-origin embeds use system light/dark fallback colors.
- The vnStat image is very lightweight. To keep it simple, rather than pulling from GHCR, this image will automatically build when you install it.
