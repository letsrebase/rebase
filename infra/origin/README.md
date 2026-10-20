# The origin's own nginx configuration

The files here belong to the origin's nginx as a whole rather than to one project's
vhost, so they live outside `projects/` (`docs/architecture.md` § Where it runs). Nothing
deploys them: like the vhosts under each project's `deploy/`, they are installed on the
host by hand, over SSH, and this directory is the record of what is installed.

| File | Installed as | What it does |
|---|---|---|
| `nginx/000-default.conf` | `/etc/nginx/sites-available/000-default.conf`, linked from `sites-enabled` | The catch-all: any name no vhost carries gets 444 on port 80 and a refused TLS handshake on 443 (REB-714) |
| `nginx/log-format.conf` | `/etc/nginx/conf.d/log-format.conf` | The access log in `combined` with the host appended, and the one `access_log` line; nginx.conf's own is commented out (REB-714) |

To install or change one, on the origin:

```bash
install -m 644 <file> <installed path>
nginx -t && systemctl reload nginx
```

`nginx -t` comes before every reload: a vhost that fails it takes every site on the
origin down with it. Check the named hosts answer as before, and that the bare IP and a
made-up host get nothing:

```bash
curl -sk -o /dev/null -w '%{http_code}\n' https://204.168.255.175/                       # 000, handshake refused
curl -s  -o /dev/null -w '%{http_code}\n' -H 'Host: x.example' http://204.168.255.175/  # 000, empty reply
```
