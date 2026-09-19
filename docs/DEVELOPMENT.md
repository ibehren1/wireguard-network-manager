<!-- Copyright © 2026 Isaac Behrens. All rights reserved. -->

# Development

How to build, run, test, and release WireGuard Network Manager. For what the
app does and how to deploy the published image, see the
[README](../README.md). For the shape of the codebase, see
[ARCHITECTURE.md](ARCHITECTURE.md); for day-to-day conventions and the full
UI/layout spec, see [CLAUDE.md](../CLAUDE.md).

## Prerequisites

- Docker with Compose v2 (`docker compose ...`, not the old `docker-compose`)
- `make` (optional, but the fastest path)
- [`uv`](https://docs.astral.sh/uv/) — only needed to change dependencies
  (regenerating `uv.lock`); the image build brings its own copy of `uv`

## Stack

- **Flask**, server-rendered (Jinja2 + Bootstrap 5 + Bootstrap Icons), no SPA
  and no JS build step — `vis-network` is loaded from a CDN.
- **MongoDB 7**, accessed through **PyMongo** directly (no ORM/ODM).
- **gunicorn** under **supervisord**, alongside `mongod`, in a single container.
- Dependencies declared in `pyproject.toml` and pinned in `uv.lock`, managed by
  **uv** (not `requirements.txt`/pip).

## Local dev loop

```bash
make up
```

That generates a `.env` with random secrets (skipped if one already exists),
builds the image, and starts the stack. It prints the generated admin
password — save it. The app is then at http://localhost:8080.

| Target | What it does |
| --- | --- |
| `make env` | Create `.env` from `.env.example` with random secrets (skips if `.env` exists) |
| `make build` | Build the Docker image |
| `make up` | `env` + `build` + start the stack |
| `make down` | Stop the stack (keeps the Mongo data volume) |
| `make restart` | Restart the running container |
| `make logs` | Tail container logs |
| `make ps` | Container status |
| `make clean` | Stop the stack **and delete the Mongo data volume** |
| `make smoke-test` | Start the stack and poll until the login page returns 200 |

`make help` prints the same list plus the release targets.

### Compose invocation

The `Makefile`'s `COMPOSE` variable is:

```bash
docker compose -f docker/docker-compose.yml --project-directory .
```

Both flags matter. `docker/docker-compose.yml` and `docker/Dockerfile` live
under `docker/`, but every relative path inside the compose file (including
`build.context: .`) and the `.env` lookup are written relative to the **repo
root** — that's what `--project-directory .` establishes. Don't `cd docker &&
docker compose up`; it will resolve paths against `docker/` and break the
build context. The compose file also pins `name: wireguard-network-manager` so
the project name doesn't silently become `docker`.

### Environment variables

Set in `.env` at the repo root (see `.env.example`):

| Variable | Purpose |
| --- | --- |
| `SECRET_KEY` | Flask session signing key |
| `ENCRYPTION_KEY` | Fernet key used to encrypt private keys at rest — separate from `SECRET_KEY` |
| `ADMIN_USERNAME` | Admin username, seeded on first run only |
| `ADMIN_PASSWORD` | Admin password, seeded on first run only |
| `MONGO_URI` | Defaults to the in-container `mongodb://127.0.0.1:27017/wireguard_manager` |

Generate a Fernet key with:

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Admin credentials are seeded by `app/bootstrap.py` only while the `users`
collection is empty; changing them in `.env` afterward has no effect.

### Data persistence

MongoDB data lives in the `mongo_data` named volume, mounted at `/data/db`
inside the container, so it survives `docker compose down` and container
recreation. `make clean` (`docker compose down -v`) deletes it.

## Docker packaging

`docker/Dockerfile` is a single-stage Ubuntu 22.04 image:

- **Ubuntu, not Debian** — MongoDB 7's official package repo publishes arm64
  builds for Ubuntu only, and this has to run on Apple Silicon too.
- The `uv` binary is copied straight out of the official image
  (`COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv`).
- `uv sync --frozen --no-install-project` installs dependencies into
  `/app/.venv` (`UV_PROJECT_ENVIRONMENT=/app/.venv`) **before** the app code is
  copied in, so a code change doesn't invalidate the dependency layer.
  `--no-install-project` is required because `pyproject.toml` sets
  `[tool.uv] package = false` — this is a Flask app run via `wsgi.py`, not an
  installable library, so `uv` manages only the dependency set.
- MongoDB 7 server is installed into the same image.

`docker/supervisord.conf` runs two programs:

- `mongod --bind_ip 127.0.0.1 --port 27017 --dbpath /data/db`
- `/app/.venv/bin/gunicorn -w 2 --worker-class gthread --threads 4 --timeout 60 -b 0.0.0.0:5000 wsgi:app`

Note the absolute gunicorn path (it isn't installed system-wide) and the
`gthread` worker class. With the default `sync` workers, an idle keep-alive
connection — browsers routinely hold several open per origin — occupies an
entire worker process while it blocks reading the next request. With only 2
workers, both can end up stuck that way, starving real requests until
gunicorn's `--timeout` kills the stalled worker (visible as `CRITICAL WORKER
TIMEOUT` in `webapp.err.log`, and as an intermittent hung request that clears
on refresh). `gthread` lets an idle connection occupy one thread instead of a
whole worker.

## Changing dependencies

1. Edit the `dependencies` list in `pyproject.toml`.
2. Run `uv lock` to regenerate `uv.lock`.
3. Rebuild (`make build`).

`uv sync --frozen` in the Dockerfile fails if `uv.lock` is out of sync with
`pyproject.toml`, so step 2 is not optional.

## Versioning

- Single source of truth: the `VERSION` file at the repo root — a plain semver
  string, no `v` prefix, no trailing content beyond a newline.
- `app/config.py` reads it once at startup (`Config.VERSION`, via
  `_read_version()`), falling back to `"0.0.0"` if the file is missing rather
  than failing to start.
- `create_app()` in `app/__init__.py` exposes it to every template as
  `app_version` via a context processor; `base.html` renders it in the footer.
- When cutting a release: bump `VERSION` and tag the commit `vX.Y.Z` (git tag,
  **with** the `v` prefix). Nothing automated enforces this yet.

`VERSION` is the one file in the repo that deliberately carries **no**
copyright header — it's read with a bare `.read().strip()`, so a comment line
would corrupt the version string. Every other hand-authored file carries
`Copyright © 2026 Isaac Behrens. All rights reserved.` as a header comment in
that file type's comment syntax, at creation time. (Generated lockfiles like
`uv.lock` are exempt.)

## Release builds

`scripts/build.sh [Local|Dev|PubDev|Prod]`, wrapped by make targets. Image
name is `wireguard-network-manager`; tags derive from `VERSION`.

| Target | Registry | Tags | Flags |
| --- | --- | --- | --- |
| `make local` | none | `:${VERSION}`, `:latest` | no `--push`, no `--load` |
| `make dev` | `$INTERNAL_REG` | `dev-latest`, `dev-${VERSION}` | `--no-cache --push` |
| `make pubdev` | Docker Hub (`$DOCKER_USER`) | `dev-latest`, `dev-${VERSION}` | `--no-cache --push` |
| `make prod` | Docker Hub (`$DOCKER_USER`) | `latest`, `${VERSION}` | `--no-cache --push` |

All four modes build with `docker buildx build
--platform=linux/arm64,linux/amd64` against a named `multi-platform-builder`
buildx builder. The script creates it on first run (`docker buildx create
--use ...`) and falls back to `docker buildx use multi-platform-builder` on
later runs, since `create` errors on an existing name — it relies on that
failure plus `||`, not a pre-check.

Docker Hub pushes authenticate with `$DOCKER_USER` / `$DOCKER_PAT`; a `Dev`
build additionally requires `$INTERNAL_REG`. Pushing a multi-arch manifest
list is handled by `--push` directly — no separate `docker push` step.

**`make local` does not produce a runnable local image.** A multi-platform
buildx build with neither `--push` nor `--load` leaves nothing in the
single-arch local image store, so `docker images` won't show it. It's useful
for warming the buildx cache and validating that both platforms build. To
actually run the image locally, use the `make up` compose loop above, which
builds single-platform for the host arch and loads it.

## Verification

No automated test suite yet. `make smoke-test` covers "does it boot and serve
the login page". Beyond that, verify through the UI:

1. `make up`, then confirm both programs are running under supervisor
   (`make logs`, or `docker exec ... supervisorctl status`).
2. Create a Network, create a Host (name only), add a `client`-type interface
   to it on a `host_network`-type Network, then rotate/assign that interface's
   key from the Host detail page's per-interface actions.
3. Create a Client, attach it to that interface, and view/download both
   generated tunnel configs. Confirm valid `wg-quick` syntax and that the IPs
   and keys match on both sides.
4. Create a second Host, give both hosts a `p2p`-type interface on a `/30`
   `p2p`-type Network (each with its own key), add a Host↔Host peer connection,
   and confirm both sides' configs list each other correctly — `Endpoint` and
   `PublicKey` must resolve from each side's *own* interface, not a host-level
   field.

## Screenshots

The screenshots in the README live in `docs/images/` and are captured from a
locally running stack (`make up`) with demo data, at a 1440×900 viewport and
2× device scale factor. Keep them free of real key material: the config-export
example in the README is a hand-written code block for exactly that reason,
and private keys are masked behind click-to-reveal in the UI.
