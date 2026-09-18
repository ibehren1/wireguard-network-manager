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
- Every key has a required `name` (shown wherever a key is displayed, e.g.
  `office-gw (Vr5M...)`, and used as the label in any dropdown that lists keys).
- Only the private key is ever provided by a user — the public key is always
  derived from it (`derive_public_key` in `app/utils/crypto.py`). Forms that let
  you source a key (`app/keys/forms.py`, `app/hosts/forms.py`,
  `app/clients/forms.py`) take a single optional `private_key` field: blank means
  generate a new keypair, a provided value is validated with `is_valid_wg_key()`
  and its public key derived. There is no "paste a public+private keypair" flow.
- **No exclusive ownership.** A Key document has no owner/active fields — it's
  just `{_id, name, public_key, private_key, created_at}`. "Usage" is derived,
  not stored: a key is in use by every Host/Client whose `active_key_id` equals
  that key's `_id` (zero, one, or many). Keys are created standalone via the Keys
  page (`/keys/new`), then attached to a Host or Client "at will" — either from
  the Keys list (`/keys/<id>/assign`), from a Host/Client's create form
  (`key_source=existing`, listing all keys by name), or by swapping a Host/
  Client's current key from its detail page (`/hosts/<id>/assign-key`,
  `/clients/<id>/assign-key`). A key can be attached to more than one Host/Client
  simultaneously ("while not ideal") — assigning a key that's already in use
  elsewhere shows a warning ("This key is already used by: X, Y — it will now
  also be used by Z. This isn't recommended...") but doesn't block it. Rotating a
  Host/Client's key just generates a new key and repoints `active_key_id`; the
  old key document is left alone (it may still be referenced by other Hosts/
  Clients). A key can only be deleted once it has zero current usages.

## IPAM

- IPv4 only.
- A Network has a CIDR. On attaching a Host/Client to a Network, auto-assign the
  next free IP in that CIDR; user can edit the assignment afterward.
- Validate: no duplicate IP within a Network, CIDR format correctness.

## Topology / peering rules

- **Clients only ever connect to Hosts** — never Client-to-Client.
- **Hosts can connect to other Hosts** — covers `/30` point-to-point links; either
  side may be the one dialing out (both can have `hostname` set).
- No PresharedKey support (deferred — not implemented yet).

## Data model

**WireGuardKey**
- `name`, `publicKey`, `privateKey` (Fernet-encrypted), `createdAt`.
- No owner/active fields — a key is not exclusively owned. It's "in use" by
  every Host/Client whose `activeKeyId` points at it (derived via reverse
  lookup, not stored on the key). Multiple Hosts/Clients may share a key (the UI
  warns but doesn't block this); a key can only be deleted once nothing
  references it.

**WireGuardNetwork**
- `name`, `cidr` (IPv4), `description`.
- Pure IPAM pool; tracks which IPs in the CIDR are assigned to which Host/Client.

**WireGuardHost**
- `name`, `activeKeyId`, `hostname` (optional bare hostname/IP, no port, set when this
  Host should be dialable), `listenPort`, `dns` (optional), `mtu` (optional). The peer
  "Endpoint" value (`host:port`) is never stored directly — it's always computed as
  `f"{hostname}:{listenPort}"` when both are set (a per-connection `endpointOverride`
  can still override this at the connection level).
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
Endpoint = <computed as peer hostname:listenPort, or endpointOverride if set>
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
Endpoint = <computed as host hostname:listenPort, if both set>
AllowedIPs = <per-connection allowedIps, default = network CIDR>
PersistentKeepalive = 10   # default, editable
```

Export UI lets the user copy or download the generated file, with an option to
override `AllowedIPs` at export time without necessarily persisting the override.

## Visual/UI conventions

- Icons (Bootstrap Icons via CDN): Hosts = `bi-server`, Clients = `bi-laptop`,
  Networks = `bi-diagram-3`. Used consistently in tables and links across
  Networks/Hosts/Clients/Keys pages so entity type is recognizable at a glance.
- **Network detail page** (`/networks/<id>`): lists every address in the CIDR
  (IP column + Host/Client column with icon, name, and a link to that entity's
  detail page; unassigned addresses show "free"). Capped at 1024 addresses for
  a full listing — larger networks (e.g. a /16) fall back to showing assigned
  IPs only, since enumerating every address wouldn't be useful anyway.
- **Topology graphs** (`vis-network` via CDN, no build step): a global graph
  (all Networks/Hosts/Clients and their relationships) is embedded directly on
  the dashboard (main page), underneath the stat cards, plus a focused
  one-hop-neighborhood graph embedded on each Host's and Client's detail page
  (`/hosts/<id>/graph.json`, `/clients/<id>/graph.json` feed the respective
  `<div id="graph">`). `/topology/graph.json` is a pure JSON API (no page of
  its own) that feeds the dashboard's graph. Node shape/color convention:
  Host = blue box, Client = green ellipse, Network = gray diamond. Edges:
  dashed gray = network membership, solid green arrow = Client→Host
  connection, solid blue double-arrow = Host↔Host peer connection.
  Graph-building logic lives in `app/services/graph.py`.

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
