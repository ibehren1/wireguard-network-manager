# WireGuard Network Manager

Web app for managing WireGuard Hosts, Clients, Networks (IPAM), and Keys, with
tunnel config generation. Flask + MongoDB 7, packaged as a single Docker
container (supervisord runs `mongod` and the Flask app together).

See [CLAUDE.md](CLAUDE.md) for the architecture/data model.

## Prerequisites

- Docker with Compose v2 (`docker compose ...`, not the old `docker-compose`)
- `make` (optional, but the fastest path)

## Quick start (make)

```bash
make up
```

This generates a `.env` with random secrets (if one doesn't already exist),
builds the image, and starts the container. It prints the generated admin
password — save it. The app is then at http://localhost:8080.

Other targets:

```bash
make logs        # tail container logs
make ps           # container status
make restart      # restart the container
make down         # stop the stack (keeps data)
make clean         # stop the stack and delete the Mongo data volume
make smoke-test    # start the stack and verify the login page responds
```

## Manual setup (no make)

1. Copy the env template and fill in real values:

   ```bash
   cp .env.example .env
   ```

   - `SECRET_KEY` — Flask session signing key.
   - `ENCRYPTION_KEY` — Fernet key used to encrypt private keys at rest. Generate one with:

     ```bash
     python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
     ```

   - `ADMIN_USERNAME` / `ADMIN_PASSWORD` — seeded as the single admin account on first run only (ignored after the `users` collection is non-empty).

2. Build and start:

   ```bash
   docker compose -f docker/docker-compose.yml --project-directory . up -d --build
   ```

3. Open http://localhost:8080 and log in.

## Data persistence

MongoDB data lives in the `mongo_data` named volume (`/data/db` inside the
container), so it survives `docker compose down` / restarts. `docker compose
down -v` (or `make clean`) deletes it.

## Deployment

For deploying a published image (built and pushed via `make prod`, see
`scripts/build.sh`) instead of building from source, use this compose file —
it's the same as [`docker/docker-compose.yml`](docker/docker-compose.yml)
except it pulls `ibehren1/wireguard-network-manager` from Docker Hub instead
of building locally:

```yaml
services:
  wireguard-network-manager:
    image: ibehren1/wireguard-network-manager:latest
    ports:
      - "8080:5000"
    environment:
      - MONGO_URI=mongodb://127.0.0.1:27017/wireguard_manager
      - SECRET_KEY=${SECRET_KEY:?SECRET_KEY is required}
      - ENCRYPTION_KEY=${ENCRYPTION_KEY:?ENCRYPTION_KEY is required}
      - ADMIN_USERNAME=${ADMIN_USERNAME:-admin}
      - ADMIN_PASSWORD=${ADMIN_PASSWORD:?ADMIN_PASSWORD is required}
    volumes:
      - mongo_data:/data/db
    restart: unless-stopped

volumes:
  mongo_data:
```

Save that as `docker-compose.yml`, put a `.env` next to it with `SECRET_KEY`,
`ENCRYPTION_KEY`, `ADMIN_USERNAME`, `ADMIN_PASSWORD` (see "Manual setup"
above for how to generate them), then `docker compose up -d`. This binds to
all interfaces on port 8080 rather than just localhost — put a reverse proxy
(with TLS) in front for anything beyond local/trusted-network use.

## Notes

- The image is based on Ubuntu 22.04, not Debian — MongoDB 7's official
  package repo doesn't publish arm64 builds for Debian, only for Ubuntu, and
  this needs to run on Apple Silicon too.
- Rotating a Host's or Client's key immediately invalidates any tunnel files
  already generated from the old key.
- Tunnel configs are viewable inline or downloadable from each Host's detail
  page, and from each Client's connection row.
