# Deployment

Live at **https://qa.contentcoach.org**, on the shared VPS (the ContentCoach server). The service runs in its own
Docker container, listening only on `127.0.0.1:8673`. The public address goes through the existing Cloudflare
tunnel, so the server's IP is not exposed and no firewall port is opened.

| Piece | Where |
|---|---|
| Code + data | `/opt/uzex-qa` (copied with `git archive`, not a git checkout) |
| Secrets | `/opt/uzex-qa/.env.server` (mode 600): `OPENAI_API_KEY`, `LLM_PROVIDERS=openai`, `PLANNER=grammar` |
| Container | `uzex-qa`, `--restart unless-stopped`, `--memory 2g`, `-p 127.0.0.1:8673:8000` |
| Tunnel | `/etc/cloudflared/config.yml` (service `cloudflared.service`, tunnel `5645765f…`): `qa.contentcoach.org → http://localhost:8673` |
| DNS | CNAME `qa.contentcoach.org` → the tunnel, added with `cloudflared tunnel route dns` from the developer machine (the server's own Cloudflare login belongs to another account) |

## Update to the current commit

```bash
git archive --format=tar HEAD | ssh vps 'cd /opt/uzex-qa && tar -xf -'
ssh vps 'cd /opt/uzex-qa && docker build -q -t uzex-qa:latest . && docker rm -f uzex-qa && docker run -d --name uzex-qa --restart unless-stopped --env-file /opt/uzex-qa/.env.server -p 127.0.0.1:8673:8000 --memory 2g uzex-qa:latest'
curl -s https://qa.contentcoach.org/health
```

`ssh vps` goes through Cloudflare Access (`ssh.contentcoach.org`); direct SSH to the IP may time out.

## Logs

```bash
ssh vps 'docker logs --since 1h uzex-qa'
```

Refusals by the closed vocabulary are logged with the unknown word (`closed vocabulary (stage 1): no data about «…»`).
