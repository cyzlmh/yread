# yread on Caddy

This scaffold serves a central, static yread collection. Caddy provides the
files and its built-in JSON directory listing; there is no application server,
database, generated global index, or reindex task.

## Server layout

```text
/var/www/yread-hub/
├── index.html
└── projects/
    ├── owner/repo/       # GitHub project_id
    └── local-project/    # non-GitHub project_id
```

Each published leaf contains its flat HTML pages plus `project.json`. The Hub
home page discovers those files through Caddy's `/projects/` browse JSON.
Browser navigation to `/projects/` redirects to the Hub home page; the JSON
request used for discovery is left unchanged.

## Install

1. Install Caddy and create a deployment user with SSH access.
   Install `rsync` on the server as well.
2. Copy `index.html` to `/var/www/yread-hub/index.html`.
3. Create `/var/www/yread-hub/projects` and give the deployment user write
   access to `/var/www/yread-hub/projects`.
4. Replace `docs.example.com` in `Caddyfile`, install it as
   `/etc/caddy/Caddyfile`, then validate and reload Caddy:

```bash
caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Configure the client with the same root and publish:

```bash
yread config set HUB_TARGET deploy@docs:/var/www/yread-hub
yread publish
```

For a non-default SSH port, configure a host alias in `~/.ssh/config` and use
that alias in `HUB_TARGET`.

`publish` synchronizes each project leaf with `rsync --delete`. Treat those
leaf directories as generated output and do not place unrelated files in them.
