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
- Both the public and private key are shown on a Host's/Client's detail page
  (decrypted on the fly via `decrypt_private_key()`, never stored decrypted).
  This is a deliberate choice, not an oversight — the app is a single-admin
  internal tool, so there's no untrusted-viewer concern that would call for
  masking, a reveal step, or a copy button.
- Every key has a required `name` (shown wherever a key is displayed, e.g.
  `office-gw (Vr5M...)`, and used as the label in any dropdown that lists keys).
  The name can be edited after creation via `/keys/<id>/edit`, regardless of
  whether the key is in use — the keypair itself (`public_key`/`private_key`)
  is immutable once created; there's no edit path for it, only rotate (Host/
  Client detail page) or assign a different key.
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
- The export-time `allowed_ips_override` query param on a Client's per-interface config
  endpoint (`/clients/<id>/config/<network_id>?allowed_ips_override=...`) remains a raw
  string override independent of this preset system, for ad-hoc one-off exports. Since a
  single interface's exported config can contain more than one `[Peer]` block (one per
  Host connection on that network), the override — when present — is applied to *every*
  peer block's `AllowedIPs` line in that export, not just one.

**WireGuardHost**
- `name`, `activeKeyId`, `hostname` (optional bare hostname/IP, no port, set when this
  Host should be dialable), `listenPort`, `dnsServerId` (optional, references DnsServer),
  `mtu` (optional). The peer "Endpoint" value (`host:port`) is never stored directly —
  it's always computed as `f"{hostname}:{listenPort}"` when both are set (a
  per-connection `endpointOverride` can still override this at the connection level).
- `networkMemberships`: `[{networkId, ip, interfaceName}, ...]` — a Host can belong to multiple Networks, one IP per Network.
  `interfaceName` (e.g. `wg0`, `wg-lan`) is freely editable and defaults to `wg<index>` (based on
  the Host's current membership count) when adding a new membership via the UI; it's used by
  tunnel-config generation to name each per-interface config (one interface per network
  membership). Memberships created before this field existed won't have it set.
- `peerConnections` (Host↔Host, for P2P links): `[{peerHostId, allowedIpsSetId, endpointOverride?, persistentKeepalive?}, ...]`.
  - When a peer connection is created, the reciprocal `peerConnections` entry pushed onto
    the peer Host defaults to the SAME `allowedIpsSetId` the user picked for the primary
    side (rather than auto-creating a `/32`-style preset) — edit it afterward via the
    existing remove/re-add flow if a different value is needed on that side.
- Serves as the peer target for any Clients attached to it.

**WireGuardClient**
- `name`, `activeKeyId`, `dnsServerId` (optional, references DnsServer).
- `networkMemberships`: `[{networkId, ip, interfaceName}, ...]` — a Client can belong to multiple Networks.
  `interfaceName` behaves the same as on `WireGuardHost` above (freely editable, defaults to
  `wg<index>` when adding a new membership, used to name each per-interface tunnel config).
- `connections`: `[{hostId, networkId, allowedIpsSetId, persistentKeepalive}, ...]` — one
  entry per Host this Client connects to on a given Network. `networkId` must be a
  Network the Client is a member of (and that the Host is also a member of); a Client can
  have more than one `connections` entry on the *same* `networkId` (e.g. two Hosts on the
  same Network for redundancy) — see "Tunnel file generation" below for how that's
  exported.
  - `allowedIpsSetId` references an AllowedIpsSet (e.g. that Network's CIDR for split-tunnel, or a `0.0.0.0/0` set for full-tunnel).
  - `persistentKeepalive` defaults to `10`.
  - A Client needing multiple exported configs for different environments (e.g. split-tunnel vs full-tunnel) gets this via either multiple `connections` entries (each pointing at a different AllowedIpsSet), or an export-time `allowed_ips_override` on a single interface's export (no separate stored "profile" entity).

## Tunnel file generation (`wg-quick` `.conf` format)

Configs are generated **per interface**, not per Host/Client. Each network membership
(`networkMemberships` entry) is treated as one WireGuard interface — assumption: one
IP/network = one interface — so a Host or Client belonging to N networks has N
independently-exportable `.conf` files, each with a single `[Interface]` `Address` line
and only the `[Peer]` blocks relevant to that one network. There is no combined
"everything this Host/Client is attached to" config.

- `app/services/tunnel.py`: `render_host_interface_config(host_id, network_id)` and
  `render_client_interface_config(client_id, network_id, allowed_ips_override=None)`
  each build exactly one interface's config, given the owning Host/Client and the
  Network whose membership identifies the interface. Both raise `ValueError` if the
  Host/Client isn't a member of that Network.
- Routes: `GET /hosts/<host_id>/config/<network_id>` and
  `GET /clients/<client_id>/config/<network_id>` (both support `?download=1` for an
  attachment response instead of inline `text/plain`; the download filename is
  `f"{name}-{interface_name}.conf"`, falling back to the raw `network_id` string if the
  membership has no `interfaceName`).

**Host interface config** — filters to only the Client connections and Host
`peerConnections` whose `networkId` matches this interface's network:
```
[Interface]
PrivateKey = <host private key>
Address = <this membership's ip>/<prefixlen>
ListenPort = <listenPort>

[Peer]   # one per Client connected to the Host on THIS network
PublicKey = <client public key>
AllowedIPs = <client ip>/32
PersistentKeepalive = <if set>

[Peer]   # one per Host-Host peerConnection on THIS network
PublicKey = <peer host public key>
Endpoint = <computed as peer hostname:listenPort, or endpointOverride if set>
AllowedIPs = <per-connection allowedIps>
```

**Client interface config** — includes every `connections` entry whose `networkId`
matches this interface's network; this can be zero, one, or more than one `[Peer]`
block (e.g. two Hosts on the same Network for redundancy):
```
[Interface]
PrivateKey = <client private key>
Address = <this membership's ip>/<prefixlen>
DNS = <if set>

[Peer]   # one per Host connection on THIS network (may repeat)
PublicKey = <host public key>
Endpoint = <computed as host hostname:listenPort, if both set>
AllowedIPs = <per-connection allowedIps, default = network CIDR>
PersistentKeepalive = 10   # default, editable
```

Export UI lets the user copy or download the generated file, with an option to override
`AllowedIPs` at export time without necessarily persisting the override
(`allowed_ips_override` query param). Because an interface's export can contain multiple
`[Peer]` blocks, this override — when supplied — is applied to *every* peer block's
`AllowedIPs` line in that interface's export, not just a single connection's.

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
  its own) that feeds the dashboard's graph.
  - **Static, not physics-simulated**: every graph uses `physics: false` plus
    vis-network's `hierarchical` layout (`direction: "UD"`) — no jiggle, and
    every node carries an explicit `level` (see below) rather than letting
    vis-network infer position from edges.
  - **Vertical ordering**: Networks sit at the top. A Network's `level` is its
    CIDR nesting depth among the networks in that particular graph (root/
    top-level = 0, a subnet of it = 1, a subnet of that = 2, ...), with an
    explicit supernet→subnet edge drawn between a network and its most
    specific containing network. A Host/Client's `level` is one below the
    network(s) it belongs to; if attached to networks at different levels, it
    sits at the midpoint between them rather than below the deepest one
    (`_member_level` in `app/services/graph.py`).
  - **Category filter** (dashboard only): each node carries a `category`
    (`"network"`/`"host"`/`"client"`); checkboxes above the dashboard graph
    toggle a node's `hidden` flag per category (vis-network automatically
    hides edges attached to a hidden node). Default: Networks and Hosts
    checked, Clients unchecked.
  - Node shape/color/size convention: Host = blue box, Client = green
    ellipse, Network = gray box, larger font — chosen because box/ellipse are
    vis-network shapes that size themselves to fit their label drawn *inside*
    the shape, unlike diamond/dot/star which draw the label below a
    fixed-size shape. Network nodes are pinned to a fixed size via
    `widthConstraint` (so name length doesn't affect node size) and labeled
    with the CIDR only (e.g. `"10.0.0.0/24"`); the network's name is shown as
    a hover tooltip (`title`) instead.
  - Edges: dashed gray = network membership, solid gray = network
    supernet→subnet containment, solid green arrow = Client→Host connection.
    A Host↔Host peer connection is drawn as **two** solid blue arrows routed
    host→network→host through the shared Network node (`peerConnections`
    always references a network both Hosts belong to) rather than a single
    direct host-to-host line — there's no vis-network "waypoint" primitive,
    so `_peer_edges()` in `app/services/graph.py` builds the two segments
    explicitly, with the `AllowedIPs` label on only the first segment to
    avoid showing it twice. `AllowedIPs` edge labels resolve the connection's
    `allowedIpsSetId` to its `cidrs` string.
  - Hierarchical layout spacing (`levelSeparation`/`nodeSpacing`/
    `treeSpacing`/`sortMethod` in each template's `extra_scripts` block) is
    tuned generously to accommodate the fixed 170px-wide network boxes
    without nodes crowding/overlapping.
  - Graph-building logic lives in `app/services/graph.py`.

## Docker packaging

- `Dockerfile`: installs Python/Flask deps + MongoDB 7 server; `supervisord` config
  runs two programs: `mongod` (bound to localhost, data dir `/data/db`) and the
  Flask app via `gunicorn`.
- `gunicorn` is invoked as `gunicorn -w 2 --worker-class gthread --threads 4 --timeout 60
  -b 0.0.0.0:5000 wsgi:app` — threaded (`gthread`) workers, not the default `sync`
  worker class. With `sync` workers, an idle keep-alive HTTP connection (browsers
  routinely hold several open per origin) ties up an entire worker process while it
  blocks waiting to read the next request; with only 2 workers total, it doesn't take
  much for both to be stuck that way, leaving nothing free to handle a real request
  until gunicorn's master kills the stalled worker for exceeding `--timeout` and boots
  a replacement — surfacing as intermittent `CRITICAL WORKER TIMEOUT` entries in
  `webapp.err.log` and a user-visible failed/hung request that clears up on refresh.
  `gthread` lets each worker run multiple threads, so an idle connection occupies one
  thread instead of the whole worker.
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
