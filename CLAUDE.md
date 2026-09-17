# WireGuard Manager

Web app to manage WireGuard Hosts, Clients, Networks (IPAM), and Keys, with visual
association management and tunnel config file export.

## Stack

- Flask, server-rendered (Jinja2 + Bootstrap/HTMX) — no separate SPA build.
- MongoDB 7, accessed via **PyMongo** directly (no ORM).
- Served by `gunicorn`, managed by `supervisord` alongside `mongod` in a single container.
- Packaged as a Docker container (Dockerfile) + `docker-compose.yml`.

## Auth

Single admin account. Credentials seeded from env vars (`ADMIN_USERNAME`,
`ADMIN_PASSWORD`) on first run if no user exists in Mongo. Flask session-based login.
`SECRET_KEY` via env var.

## Key handling

- Keys generated in pure Python via **PyNaCl** (Curve25519), base64-encoded in
  standard WireGuard format. No shelling out to `wg`.
- Private keys encrypted at rest with **Fernet**, key from `ENCRYPTION_KEY` env var
  (separate from `SECRET_KEY`). Decrypt only when generating tunnel files or
  displaying to the user.
- One **active** key per Host/Client at a time. Rotating generates a new key; old
  keys kept in DB with `active=false` (history, not deleted).

## IPAM

- IPv4 only.
- A Network has a CIDR. On attaching a Host/Client to a Network, auto-assign the
  next free IP in that CIDR; user can edit the assignment afterward.
- Validate: no duplicate IP within a Network, CIDR format correctness.

## Topology / peering rules

- **Clients only ever connect to Hosts** — never Client-to-Client.
- **Hosts can connect to other Hosts** — covers `/30` point-to-point links; either
  side may be the one dialing out (both can have `endpoint` set).
- No PresharedKey support (deferred — not implemented yet).

## Data model

**WireGuardKey**
- `publicKey`, `privateKey` (Fernet-encrypted), `createdAt`, `ownerType` (`host`/`client`), `ownerId`, `active`.

**WireGuardNetwork**
- `name`, `cidr` (IPv4), `description`.
- Pure IPAM pool; tracks which IPs in the CIDR are assigned to which Host/Client.

**WireGuardHost**
- `name`, `activeKeyId`, `endpoint` (optional `host:port`, set when this Host should be dialable), `listenPort`, `dns` (optional), `mtu` (optional).
- `networkMemberships`: `[{networkId, ip}, ...]` — a Host can belong to multiple Networks, one IP per Network.
- `peerConnections` (Host↔Host, for P2P links): `[{peerHostId, allowedIps, endpointOverride?, persistentKeepalive?}, ...]`.
- Serves as the peer target for any Clients attached to it.

**WireGuardClient**
- `name`, `activeKeyId`, `dns` (optional).
- `networkMemberships`: `[{networkId, ip}, ...]` — a Client can belong to multiple Networks.
- `connections`: `[{hostId, allowedIps, persistentKeepalive}, ...]` — one entry per Host this Client connects to.
  - `allowedIps` defaults to that Network's CIDR, overridable (e.g. `0.0.0.0/0` for full-tunnel).
  - `persistentKeepalive` defaults to `10`.
  - A Client needing multiple exported configs for different environments (e.g. split-tunnel vs full-tunnel) gets this via either multiple `connections` entries, or an export-time `allowedIps` override on a single connection (no separate stored "profile" entity).

## Tunnel file generation (`wg-quick` `.conf` format)

**Host config:**
```
[Interface]
PrivateKey = <host private key>
Address = <ip/prefix>, ...   # one per network membership
ListenPort = <listenPort>

[Peer]   # one per attached Client
PublicKey = <client public key>
AllowedIPs = <client ip>/32
PersistentKeepalive = <if set>

[Peer]   # one per Host-Host connection
PublicKey = <peer host public key>
Endpoint = <peer host endpoint, if it has one>
AllowedIPs = <per-connection allowedIps>
```

**Client config:**
```
[Interface]
PrivateKey = <client private key>
Address = <ip/prefix>, ...
DNS = <if set>

[Peer]   # one per Host connection
PublicKey = <host public key>
Endpoint = <host endpoint>
AllowedIPs = <per-connection allowedIps, default = network CIDR>
PersistentKeepalive = 10   # default, editable
```

Export UI lets the user copy or download the generated file, with an option to
override `AllowedIPs` at export time without necessarily persisting the override.

## Docker packaging

- `Dockerfile`: installs Python/Flask deps + MongoDB 7 server; `supervisord` config
  runs two programs: `mongod` (bound to localhost, data dir `/data/db`) and the
  Flask app via `gunicorn`.
- `docker-compose.yml`: builds the image, maps app port (e.g. `8080:5000`), mounts a
  named volume at `/data/db` for Mongo persistence, passes env vars
  (`ADMIN_USERNAME`, `ADMIN_PASSWORD`, `SECRET_KEY`, `ENCRYPTION_KEY`, `MONGO_URI` if needed).

## Open items to decide while building

- Flask project layout (blueprints per entity: hosts/clients/networks/keys/auth).
- Exact form validation rules beyond IP/CIDR checks (e.g. key format validation for
  user-supplied keys).

## Verification

- `docker compose up`: confirm both `mongod` and Flask start under supervisor
  (check logs / `supervisorctl status`).
- Through the UI: create a Network, create a Host (generate a key), create a
  Client, attach the Client to the Host with an IP in the Network, download/view
  both generated tunnel configs, confirm valid `wg-quick` syntax and matching
  IPs/keys.
- Create a second Host and a Host-Host P2P connection on a `/30` network, confirm
  both sides' configs list each other correctly.
