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
- Keys can also be created standalone with no owner (`owner_type`/`owner_id` both
  `None`, `active=False`) via the Keys page (`/keys/new`, generate or paste), then
  assigned later to a Host or Client "at will" — either from the Keys list
  (`/keys/<id>/assign`), from a Host/Client's create form (`key_source=existing`),
  or by swapping a Host/Client's current key from its detail page
  (`/hosts/<id>/assign-key`, `/clients/<id>/assign-key`). Assigning retires
  whatever active key the target currently has (kept as history) and makes the
  chosen key active. Only never-assigned (`owner_type is None`) keys can be
  deleted.

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
- `publicKey`, `privateKey` (Fernet-encrypted), `createdAt`, `ownerType` (`host`/`client`/`None`), `ownerId` (`None` when unassigned), `active`.

**WireGuardNetwork**
- `name`, `cidr` (IPv4), `description`.
- Pure IPAM pool; tracks which IPs in the CIDR are assigned to which Host/Client.

**DnsServer**
- `name`, `ips` — the raw comma-delimited string the user typed (e.g. `"1.1.1.1, 1.0.0.1"`),
  stored verbatim since wg-quick's `DNS =` line already accepts a comma-separated list
  directly. Validated at write time as a comma-separated list of valid IPv4 addresses
  (`ipaddress.ip_address()` per entry after splitting/stripping).
- Managed at `/dns-servers` (blueprint `app.dns_servers`, collection `dns_servers`).
- Referenced by ID (not free text) from `WireGuardHost.dnsServerId` and
  `WireGuardClient.dnsServerId` — nullable; a Host/Client with no DNS server set omits
  the `DNS =` line from its generated config. Deleting a DnsServer is blocked while any
  Host or Client still references it.

**AllowedIpsSet**
- `name`, `cidrs` — the raw comma-delimited string the user typed (e.g.
  `"10.0.0.0/24, 192.168.1.0/24"` or `"0.0.0.0/0"` for full-tunnel), stored verbatim.
  Validated at write time by parsing each comma-separated entry with
  `ipaddress.ip_network(entry, strict=False)`.
- Managed at `/allowed-ips` (blueprint `app.allowed_ips`, collection `allowed_ips`).
- Referenced by ID (not free text) from each entry in `WireGuardClient.connections`
  (`allowedIpsSetId`) and `WireGuardHost.peerConnections` (`allowedIpsSetId`) — required
  on both. Deleting an AllowedIpsSet is blocked while any Client connection or Host peer
  connection still references it.
- The export-time `allowed_ips_override` query param on a Client's config endpoint
  (`/clients/<id>/config/<index>?allowed_ips_override=...`) remains a raw string override
  independent of this preset system, for ad-hoc one-off exports.

**WireGuardHost**
- `name`, `activeKeyId`, `endpoint` (optional `host:port`, set when this Host should be dialable), `listenPort`, `dnsServerId` (optional, references DnsServer), `mtu` (optional).
- `networkMemberships`: `[{networkId, ip}, ...]` — a Host can belong to multiple Networks, one IP per Network.
- `peerConnections` (Host↔Host, for P2P links): `[{peerHostId, allowedIpsSetId, endpointOverride?, persistentKeepalive?}, ...]`.
  - When a peer connection is created, the reciprocal `peerConnections` entry pushed onto
    the peer Host defaults to the SAME `allowedIpsSetId` the user picked for the primary
    side (rather than auto-creating a `/32`-style preset) — edit it afterward via the
    existing remove/re-add flow if a different value is needed on that side.
- Serves as the peer target for any Clients attached to it.

**WireGuardClient**
- `name`, `activeKeyId`, `dnsServerId` (optional, references DnsServer).
- `networkMemberships`: `[{networkId, ip}, ...]` — a Client can belong to multiple Networks.
- `connections`: `[{hostId, allowedIpsSetId, persistentKeepalive}, ...]` — one entry per Host this Client connects to.
  - `allowedIpsSetId` references an AllowedIpsSet (e.g. that Network's CIDR for split-tunnel, or a `0.0.0.0/0` set for full-tunnel).
  - `persistentKeepalive` defaults to `10`.
  - A Client needing multiple exported configs for different environments (e.g. split-tunnel vs full-tunnel) gets this via either multiple `connections` entries (each pointing at a different AllowedIpsSet), or an export-time `allowed_ips_override` on a single connection (no separate stored "profile" entity).

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

## Visual/UI conventions

- Icons (Bootstrap Icons via CDN): Hosts = `bi-server`, Clients = `bi-laptop`,
  Networks = `bi-diagram-3`. Used consistently in tables and links across
  Networks/Hosts/Clients/Keys pages so entity type is recognizable at a glance.
- **Network detail page** (`/networks/<id>`): lists every address in the CIDR
  (IP column + Host/Client column with icon, name, and a link to that entity's
  detail page; unassigned addresses show "free"). Capped at 1024 addresses for
  a full listing — larger networks (e.g. a /16) fall back to showing assigned
  IPs only, since enumerating every address wouldn't be useful anyway.
- **Topology graphs** (`vis-network` via CDN, no build step): a global graph at
  `/topology` (all Networks/Hosts/Clients and their relationships), plus a
  focused one-hop-neighborhood graph embedded on each Host's and Client's
  detail page (`/hosts/<id>/graph.json`, `/clients/<id>/graph.json`,
  `/topology/graph.json` feed the respective `<div id="graph">`). Node
  shape/color convention: Host = blue box, Client = green ellipse, Network =
  gray diamond. Edges: dashed gray = network membership, solid green arrow =
  Client→Host connection, solid blue double-arrow = Host↔Host peer connection.
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
