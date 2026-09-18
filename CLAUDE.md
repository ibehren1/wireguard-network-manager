<!-- Copyright © 2026 Isaac Behrens. All rights reserved. -->

# WireGuard Network Manager

Web app to manage WireGuard Hosts, Clients, Networks (IPAM), and Keys, with visual
association management and tunnel config file export.

## Stack

- Flask, server-rendered (Jinja2 + Bootstrap/HTMX) — no separate SPA build.
- MongoDB 7, accessed via **PyMongo** directly (no ORM).
- Served by `gunicorn`, managed by `supervisord` alongside `mongod` in a single container.
- Packaged as a Docker container (Dockerfile) + `docker-compose.yml`.
- Dependencies managed by **uv** — `pyproject.toml` (declared deps) + `uv.lock`
  (resolved/pinned versions), not `requirements.txt`/pip. See "Docker packaging"
  below for how the image builds against it.

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
- The public and private key values themselves (decrypted via
  `decrypt_private_key()`, never stored decrypted) are only ever shown in two
  places: a Client's detail page (its one key), and the key's own edit page
  (`/keys/<id>/edit`, alongside the editable `name`). Everywhere else a key is
  referenced — the Keys list, and a Host interface's row in the Interfaces
  table — shows just its `name` as a link to `/keys/<id>/edit`, not the raw
  key material inline.
  - Public key: shown outright, in Bootstrap's default `<code>` styling
    (monospace, pink) — not sensitive.
  - Private key: masked by default (a fixed-width bullet placeholder) with a
    click-to-reveal eye-icon button beside it, using the shared
    `secret_reveal(value, uid)` Jinja macro (`app/templates/macros.html`) and
    the global `toggleSecret()` JS helper in `base.html` — swaps two `<span>`s
    (placeholder/value, one always `d-none`) and flips the icon. `uid` just
    needs to be unique on the page (the owning Key's or Client's `_id` is
    enough, since each of these two pages only ever shows one private key).
- Every key has a required `name` (shown wherever a key is displayed, e.g.
  `office-gw (Vr5M...)`, and used as the label in any dropdown that lists keys).
  The name can be edited after creation via `/keys/<id>/edit`, regardless of
  whether the key is in use — the keypair itself (`public_key`/`private_key`)
  is immutable once created; there's no edit path for it, only rotate (Client
  detail page, or per-interface on a Host's detail page) or assign a different
  key.
- Only the private key is ever provided by a user — the public key is always
  derived from it (`derive_public_key` in `app/utils/crypto.py`). Forms that let
  you source a key (`app/keys/forms.py`, `app/clients/forms.py`) take a single
  optional `private_key` field: blank means generate a new keypair, a provided
  value is validated with `is_valid_wg_key()` and its public key derived. There
  is no "paste a public+private keypair" flow. A Host has no key-sourcing form
  at all — a new interface (`network_memberships` entry) is created with
  `active_key_id: None`, then a key is attached afterward via that interface's
  own "Assign Existing Key" or "Rotate Key" action on the Host's detail page
  (`client_non_wg` interfaces never get one — same treatment as their other
  WG-only fields).
- **No exclusive ownership.** A Key document has no owner/active fields — it's
  just `{_id, name, public_key, private_key, created_at}`. "Usage" is derived,
  not stored: a key is in use by every Host interface whose
  `network_memberships[].active_key_id` equals that key's `_id`, and every
  Client whose `active_key_id` does (zero, one, or many usages total). Keys are
  created standalone via the Keys page (`/keys/new`), then attached "at will" —
  either from the Keys list's `/keys/<id>/assign` page (whose target dropdown
  lists every Client plus every WG-capable Host interface, the latter encoded
  as `hostiface:<host_id>:<network_id>`), from a Client's create form
  (`key_source=existing`, listing all keys by name — Hosts have no such
  create-time selection, see above), or by swapping a Host interface's or
  Client's current key from its own "Assign Existing Key" action
  (`/hosts/<host_id>/interfaces/<network_id>/assign-key`,
  `/clients/<id>/assign-key`). A key can be attached to more than one Host
  interface/Client simultaneously ("while not ideal") — assigning a key that's
  already in use elsewhere shows a warning ("This key is already used by: X,
  Y — it will now also be used by Z. This isn't recommended...") but doesn't
  block it. Rotating a Host interface's key
  (`/hosts/<host_id>/interfaces/<network_id>/rotate-key`) or a Client's key
  just generates a new key and repoints that `active_key_id`; the old key
  document is left alone (it may still be referenced elsewhere). A key can only
  be deleted once it has zero current usages.

## IPAM

- IPv4 only.
- A Network has a CIDR. On attaching a Host/Client to a Network, auto-assign the
  next free IP in that CIDR; user can edit the assignment afterward.
- Validate: no duplicate IP within a Network, CIDR format correctness.

## Topology / peering rules

- **Clients only ever connect to Hosts** — never Client-to-Client.
- **Hosts can connect to other Hosts** — covers `/30` point-to-point links; either
  side may be the one dialing out (both can have `hostname` set on their `p2p`
  interface). Host-Host peering only rides over a Host's `p2p`-type interfaces.
- No PresharedKey support (deferred — not implemented yet).

## Data model

**WireGuardKey**
- `name`, `publicKey`, `privateKey` (Fernet-encrypted), `createdAt`.
- No owner/active fields — a key is not exclusively owned. It's "in use" by
  every Host interface (`networkMemberships[].activeKeyId`) or Client
  (`activeKeyId`) that points at it (derived via reverse lookup, not stored on
  the key). Multiple Host interfaces/Clients may share a key (the UI warns but
  doesn't block this); a key can only be deleted once nothing references it.

**WireGuardNetwork**
- `name`, `cidr` (IPv4), `description`.
- Pure IPAM pool; tracks which IPs in the CIDR are assigned to which Host/Client.
- `networkType` — one of `p2p` (peer-to-peer/point-to-point), `ipam` (high-level CIDR
  block for IPAM organization), or `host_network` (contains IPs used for clients,
  managed by a Host — WireGuard or DHCP). Docs predating this field have no
  `networkType` set — treat missing as `"ipam"` (`net.get("network_type", "ipam")`).
- `managingHostId` — nullable, references a `WireGuardHost`; required (and only
  meaningful) when `networkType == "host_network"`, `None` for the other two types.

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
- `name`. Created with just that (name-only) — no hostname/port/DNS/MTU/key selection at
  creation time; those all live per-interface (below), and a key is attached to an
  interface afterward via that interface's own "Assign Existing Key" or "Rotate Key"
  action on the Host's detail page. A Host has no key of its own — see `activeKeyId`
  below, per interface.
- `networkMemberships`: `[{networkId, ip, interfaceName, interfaceType, hostname,
  listenPort, dnsServerId, mtu, activeKeyId}, ...]` — a Host can belong to multiple
  Networks, one IP per Network, and each membership now models one full interface
  (WireGuard or not):
  - `interfaceName` (e.g. `wg0`, `wg-lan`) is freely editable and defaults to `wg<index>`
    (based on the Host's current membership count) when adding a new membership via the
    UI; used by tunnel-config generation to name each per-interface config.
  - `interfaceType` — one of `p2p`, `client`, `client_non_wg`. Constrains which
    `WireGuardNetwork.networkType` the interface may attach to: `p2p` → a `p2p` Network
    (this is what a Host↔Host `peerConnections` entry rides over); `client` and
    `client_non_wg` → a `host_network` Network. An `ipam`-type Network is never a valid
    attachment target for any interface type. Memberships created before this field
    existed have no `interfaceType` set — treat missing as `"client"`
    (`m.get("interface_type", "client")`).
  - `hostname` (bare hostname/IP, no port, set when this interface should be dialable),
    `listenPort`, `dnsServerId` (optional, references DnsServer), `mtu` (optional),
    `activeKeyId` — all per-interface, since peer identity (like listen port/DNS/MTU) is
    a property of the interface, not the machine: a Host with two interfaces needs two
    distinct keypairs, not one shared PrivateKey/PublicKey exported on both. All of these
    are meaningless (and force-cleared to `null` server-side regardless of what's
    submitted, hidden in the UI, with no key actions shown) when
    `interfaceType == "client_non_wg"`, since that type is a pure IPAM record with no
    WireGuard config of its own (e.g. a plain LAN NIC on a DHCP segment) — no
    `[Interface]`/config export for it. A new interface starts with `activeKeyId: None`;
    a key is attached afterward via `POST
    /hosts/<host_id>/interfaces/<network_id>/rotate-key` (generates a new key named
    `f"{host['name']} {interface_name} key"`) or `GET/POST
    /hosts/<host_id>/interfaces/<network_id>/assign-key` (pick an existing key by name).
    The peer "Endpoint" value (`host:port`) is never stored directly — it's always
    computed per-interface as `f"{hostname}:{listenPort}"` when both are set on that
    membership (a per-connection `endpointOverride` can still override this at the
    connection level).
  - Add/edit at `/hosts/<id>/interfaces/add` and `/hosts/<id>/interfaces/<network_id>/edit`
    (`InterfaceForm` in `app/hosts/forms.py`); the Network dropdown excludes `ipam`-type
    Networks and is further filtered client-side to the interfaceType's matching
    `networkType`.
- `peerConnections` (Host↔Host, for P2P links, one per `p2p`-type interface):
  `[{peerHostId, allowedIpsSetId, endpointOverride?, persistentKeepalive?}, ...]`.
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
  membership has no `interfaceName`). If the Host's membership for that `network_id` has
  `interfaceType == "client_non_wg"`, `render_host_interface_config` raises `ValueError`
  ("This interface has no WireGuard configuration to export.") instead of exporting
  anything — the route's existing `except ValueError` flashes it.

**Host interface config** — filters to only the Client connections and Host
`peerConnections` whose `networkId` matches this interface's network. `PrivateKey`/
`ListenPort`/`DNS`/`MTU` all come from THIS membership, not from any host-level field
(there is none); similarly a peer's `PublicKey` and `Endpoint` are resolved from that
specific peer Host's own membership for the shared network (`peer_membership`), not a
host-level key/hostname/port:
```
[Interface]
PrivateKey = <host private key>
Address = <this membership's ip>/<prefixlen>
ListenPort = <this membership's listenPort>

[Peer]   # one per Client connected to the Host on THIS network
PublicKey = <client public key>
AllowedIPs = <client ip>/32
PersistentKeepalive = <if set>

[Peer]   # one per Host-Host peerConnection on THIS network
PublicKey = <peer host public key>
Endpoint = <computed as peer's own membership hostname:listenPort for this network, or endpointOverride if set>
AllowedIPs = <per-connection allowedIps>
```

**Client interface config** — includes every `connections` entry whose `networkId`
matches this interface's network; this can be zero, one, or more than one `[Peer]`
block (e.g. two Hosts on the same Network for redundancy). Each `[Peer]` block's
`PublicKey` is resolved from that specific Host's own membership for this connection's
network (`host_membership.activeKeyId`), not a host-level key — same per-interface
resolution as above:
```
[Interface]
PrivateKey = <client private key>
Address = <this membership's ip>/<prefixlen>
DNS = <if set>

[Peer]   # one per Host connection on THIS network (may repeat)
PublicKey = <host public key>
Endpoint = <computed as that Host's own membership hostname:listenPort for this network, if both set>
AllowedIPs = <per-connection allowedIps, default = network CIDR>
PersistentKeepalive = 10   # default, editable
```

Export UI lets the user copy or download the generated file, with an option to override
`AllowedIPs` at export time without necessarily persisting the override
(`allowed_ips_override` query param). Because an interface's export can contain multiple
`[Peer]` blocks, this override — when supplied — is applied to *every* peer block's
`AllowedIPs` line in that interface's export, not just a single connection's.

## Visual/UI conventions

- Icons (Bootstrap Icons via CDN): Hosts = `bi-hdd-rack` (a rack-server glyph —
  deliberately not `bi-server`, which reads as a database/stacked-disks icon
  to most people at a glance), Clients = `bi-laptop`, Networks = `bi-diagram-3`,
  Keys = `bi-key`. Used consistently everywhere an entity's name/link appears —
  every list table, every detail-page heading, the navbar, the dashboard's stat
  cards, and every form page whose heading names a specific Host/Client — so
  entity type is recognizable at a glance regardless of which page you're on.
- **Networks list/detail pages**: both show a "Type" column/row with the network's
  `networkType` display label; when the type is `host_network`, the managing Host's
  name is shown alongside it as a link to that Host's detail page (`bi-hdd-rack` icon).
  The list page's column order is CIDR, Name, Description, Type, Assigned IPs.
- **Host list/detail pages**: the Hosts list shows an "Interfaces" column (just the
  `network_memberships` count — no more Hostname/Listen Port columns, since those are
  per-interface now). The Host detail page has no top-level key `<dl>` at all — a Host
  has no key of its own. Its "Interfaces" section (renamed from "Network Memberships")
  table has Network/IP/Interface/Type/Hostname/Listen Port/DNS/MTU columns plus a single
  Key column (the key's `name`, linked to `/keys/<id>/edit` — see "Key handling" above;
  no raw key material inline here) and, per row, Edit / "Assign Existing Key" /
  "Rotate Key" / Remove actions; for a `client_non_wg` row the Key column shows "-" and
  the key actions are omitted (same treatment as its other hidden WG-only fields), and
  the separate "Tunnel Config (per interface)" table below shows "No config
  (non-WireGuard interface)" instead of View/Download links.
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
  - **Vertical ordering (dashboard/global graph)**: fixed tiers, top to bottom
    — `ipam`-type networks (their own tree, nested by CIDR containment depth,
    with an explicit supernet→subnet edge to each one's most specific
    containing `ipam` network) → Hosts, with `p2p`-type networks sharing that
    same row (drawn horizontally between the two Hosts they link, via
    `_peer_edges()`, exactly as on the Host detail page below) → `host_network`-
    type networks (one flat row, not nested among themselves) → Clients (one
    flat row below that). Unlike the per-entity graphs below, Hosts/Host
    Networks/Clients don't derive their level from CIDR containment — Hosts in
    particular have no CIDR relationship to `ipam` networks at all (an
    interface can only attach to a `p2p` or `host_network` network, never
    `ipam`) — the tiers are a fixed visual convention computed once from the
    `ipam` tree's depth (`build_full_graph()` in `app/services/graph.py`).
    - **An `ipam` network whose full set of CIDR-contained descendants (any
      depth) is non-empty and entirely `p2p`-type is left out of this graph
      entirely** (`_ipam_ids_with_only_p2p_descendants()`) — an IPAM block that
      exists only to organize P2P links is noise once those links are drawn on
      the Hosts' row instead of in the IPAM tree. Simplification: this checks
      every descendant regardless of type, so an `ipam` block containing only
      a nested `ipam` block that in turn contains only `p2p` networks is NOT
      excluded (the nested block itself isn't a `p2p` network) — not worth the
      extra complexity unless it comes up in practice.
    - **Every rendered `ipam` network connects down to Hosts, not just to
      other `ipam` networks**: for each Host, and for each of its `p2p`/
      `host_network` memberships, `_most_specific_ipam_ancestor()` finds the
      most specific surviving `ipam` network that CIDR-contains that
      membership's network, and a plain edge (`_hierarchy_edge()`, same solid
      gray no-arrow style as the existing supernet→subnet edges — that helper
      now backs both) is drawn from that `ipam` network straight to the Host
      (deduped per Host so multiple qualifying memberships under the same
      ancestor don't produce repeat edges). This is what keeps the CIDR-space
      tier visually attached to the rest of the graph instead of floating.
    - **P2P networks must render horizontally centered between their two
      member Hosts, not wherever vis-network's automatic ordering happens to
      put them**: vis-network's hierarchical layout only uses edges between
      *adjacent* levels to order nodes within a level, and a P2P network's
      edges to its Hosts are same-level, so the layout has no signal to
      center it. Each P2P network node carries a `member_host_ids` array
      (populated in `build_full_graph()`); `dashboard.html`'s
      `network.once("afterDrawing", ...)` handler runs once after the initial
      render, reads each member Host's actual rendered position via
      `network.getPositions()`, averages their x-coordinates (centroid if
      more than two — the data model doesn't strictly prevent a P2P network
      from having more than 2 members), and snaps the P2P node there via
      `network.moveNode()`. Only the dashboard graph does this; the P2P-
      network-between-two-Hosts placement on the Host detail page
      (`build_host_graph`) doesn't need it since that graph only ever has
      exactly one P2P network at level 0 between exactly two Hosts, positioned
      fine by the general layout there already.
  - **Vertical ordering (Host/Client detail-page graphs differs from the
    dashboard)**: `build_host_graph()` and `build_client_graph()` in
    `app/services/graph.py` use their own level rules, not the dashboard's
    network-on-top rule above.
    - **Host detail page**: the focal Host and any Host↔Host peer Hosts sit
      together at the top (`level` 0), displayed horizontally side by side.
      Any network that's the target of a peer connection ("host-to-host",
      typically a P2P `/30`) also sits at level 0, between the two Hosts it
      links (via `_peer_edges()`). Every other ("regular"/LAN-style) network
      the Host belongs to sits below the Host, starting at level 1 and going
      deeper for nested-CIDR hierarchy among just those regular networks
      (`_network_hierarchy()`, shifted down by 1). A Client connected to the
      Host sits one level below whichever regular network its connection
      runs over.
    - **Client detail page**: any Host(s) the Client connects to sit at the
      top (`level` 0), displayed horizontally. The Client's own network(s) sit
      below the hosts, starting at level 1 (deeper for nested-CIDR hierarchy
      among them, via `_network_hierarchy()` shifted down by 1). The Client
      itself sits one level below its network(s) (`_member_level()`, at the
      midpoint if attached to networks at different levels). Top-to-bottom
      order: Host(s) → Network(s) → Client.
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

## Licensing

- MIT License (`LICENSE` at repo root). Copyright line used everywhere in this
  project: `Copyright © 2026 Isaac Behrens. All rights reserved.`
- **Every file in this repo carries that line as a header comment, in the
  comment syntax appropriate to its type — and every new file created from
  now on must too, at creation time, not as a follow-up cleanup pass:**
  - Python (`.py`): `# Copyright © 2026 Isaac Behrens. All rights reserved.`
    as the first line (or the line right after a shebang, if present), then a
    blank line.
  - Shell (`.sh`): same `#` comment, placed after the shebang line (never
    before it — a shebang must be line 1 to work).
  - Jinja templates (`.html`): `{# Copyright © 2026 Isaac Behrens. All rights
    reserved. #}` as the first line — a Jinja comment, stripped at render
    time, so it never leaks into page output.
  - `Dockerfile`, `docker-compose.yml`, `Makefile`, `pyproject.toml`,
    `.gitignore`, `.env.example`: `#` comment, first line, then a blank line.
  - `supervisord.conf` (INI): `;` comment (INI's native comment char), first
    line, then a blank line.
  - Markdown (`README.md`, `CLAUDE.md`, `ARCHITECTURE.md`): an HTML comment
    (`<!-- Copyright © 2026 Isaac Behrens. All rights reserved. -->`) as the
    first line, then a blank line — invisible in rendered Markdown, present
    in source.
  - **Exception**: `VERSION` — it's read via a plain `.read().strip()` (see
    "Versioning" below) with no comment-line handling, so a header would
    corrupt the version string. Leave it as a bare version number, nothing
    else. Auto-generated/lockfile-style files (e.g. `uv.lock`) are also
    exempt — they get rewritten by tooling, not hand-authored.

## Versioning

- Single source of truth: the `VERSION` file at repo root (plain semver string,
  e.g. `0.1.0`, no `v` prefix, no trailing content beyond a newline).
- `app/config.py` reads it once at startup (`Config.VERSION`, via `_read_version()`;
  falls back to `"0.0.0"` if the file is missing rather than failing to start).
- Exposed to every template as `app_version` via a Flask context processor in
  `app/__init__.py` (`create_app`); shown in the page footer in `base.html`.
- Bump `VERSION` and tag the corresponding commit `vX.Y.Z` (git tag, "v" prefix)
  when cutting a release — nothing automated enforces this yet.
- Release image builds go through `scripts/build.sh [Local|Dev|PubDev|Prod]`
  (image name `wireguard-network-manager`, tags derived from `VERSION`), wrapped
  by `make local`/`dev`/`pubdev`/`prod`:
  - `local` — build only, tags `:${VERSION}` + `:latest`, no push.
  - `dev` — build + push to `$INTERNAL_REG`, tags `dev-latest` / `dev-${VERSION}`.
  - `pubdev`/`prod` — build + push to Docker Hub under `$DOCKER_USER` (auth via
    `$DOCKER_USER`/`$DOCKER_PAT`); `pubdev` tags `dev-latest`/`dev-${VERSION}`,
    `prod` tags `latest`/`${VERSION}`.
  - Distinct from the `make up`/`build`/`down` docker-compose dev loop above —
    that builds the same `wireguard-network-manager` image name locally
    (untagged with a version, just `:latest`, via `docker/docker-compose.yml`)
    rather than through `scripts/build.sh`.

## Docker packaging

- `Dockerfile` and `docker-compose.yml` both live under `docker/`, not the repo
  root. Always invoke compose with both `-f docker/docker-compose.yml` AND
  `--project-directory .` — the latter makes compose resolve `.env` lookup
  *and every relative path inside the compose file* (including `build.context`)
  from the repo root rather than from the compose file's own directory
  (`docker/`). That's why `build.context` is `.` (not `..`, which would be
  wrong once `--project-directory` is already pointing at repo root) with
  `build.dockerfile: docker/Dockerfile` — the Dockerfile's `COPY`s are written
  relative to repo root. The `Makefile`'s `COMPOSE` variable already
  does this; don't invoke `docker compose` directly from within `docker/`.
  The compose file pins `name: wireguard-network-manager` at the top level so
  the project/container name doesn't depend on which directory it's run from
  (it would otherwise default to `docker`, the compose file's own directory).
- `Dockerfile`: dependencies are managed by **uv** (`pyproject.toml` + `uv.lock`,
  not `requirements.txt`/pip) — the `uv` binary itself is copied in directly from
  the official `ghcr.io/astral-sh/uv` image (`COPY --from=`), then
  `uv sync --frozen --no-install-project` installs into `/app/.venv`
  (`UV_PROJECT_ENVIRONMENT=/app/.venv`) before the app code is copied in, for
  layer caching. `--no-install-project` is required because `[tool.uv]
  package = false` in `pyproject.toml` — this project isn't structured as an
  installable package (it's a Flask app run via `wsgi.py`, not a library), so
  `uv` only manages the dependency set, not the app itself. Installs MongoDB 7
  server too; `supervisord` config runs two programs: `mongod` (bound to
  localhost, data dir `/data/db`) and the Flask app via `gunicorn` (invoked as
  `/app/.venv/bin/gunicorn`, not a bare `gunicorn` off `$PATH`, since it's not
  installed system-wide).
  - To change dependencies: edit `pyproject.toml`'s `dependencies` list, then
    run `uv lock` locally (regenerates `uv.lock`) before rebuilding the image —
    `uv sync --frozen` in the Dockerfile will fail if `uv.lock` is out of sync
    with `pyproject.toml`.
- `gunicorn` is invoked as `/app/.venv/bin/gunicorn -w 2 --worker-class gthread
  --threads 4 --timeout 60 -b 0.0.0.0:5000 wsgi:app` — threaded (`gthread`) workers, not the default `sync`
  worker class. With `sync` workers, an idle keep-alive HTTP connection (browsers
  routinely hold several open per origin) ties up an entire worker process while it
  blocks waiting to read the next request; with only 2 workers total, it doesn't take
  much for both to be stuck that way, leaving nothing free to handle a real request
  until gunicorn's master kills the stalled worker for exceeding `--timeout` and boots
  a replacement — surfacing as intermittent `CRITICAL WORKER TIMEOUT` entries in
  `webapp.err.log` and a user-visible failed/hung request that clears up on refresh.
  `gthread` lets each worker run multiple threads, so an idle connection occupies one
  thread instead of the whole worker.
- `docker/docker-compose.yml`: builds the image, maps app port (bound to
  `127.0.0.1:8080` on the host, not all interfaces), mounts a named volume at
  `/data/db` for Mongo persistence, passes env vars (`ADMIN_USERNAME`,
  `ADMIN_PASSWORD`, `SECRET_KEY`, `ENCRYPTION_KEY`, `MONGO_URI` if needed).
  README.md's "Deployment" section carries a copy of this file for users
  deploying the published Docker Hub image (`image:` instead of `build:`,
  bound to all interfaces instead of just localhost since it's meant to run
  on a real host, not a laptop) — keep both in sync if either changes.

## Open items to decide while building

- Flask project layout (blueprints per entity: hosts/clients/networks/keys/auth).
- Exact form validation rules beyond IP/CIDR checks (e.g. key format validation for
  user-supplied keys).

## Verification

- `docker compose up`: confirm both `mongod` and Flask start under supervisor
  (check logs / `supervisorctl status`).
- Through the UI: create a Network, create a Host (just a name), add a `client`-type
  interface to it on a `host_network`-type Network (rotating/assigning that interface's
  own key separately via the Host detail page's per-interface actions), create a Client,
  attach the Client to that interface, download/view both generated tunnel configs,
  confirm valid `wg-quick` syntax and matching IPs/keys.
- Create a second Host, add `p2p`-type interfaces to both on a `/30` `p2p`-type
  Network (each with its own key), and a Host-Host P2P connection between them, confirm
  both sides' configs list each other correctly (Endpoint and PublicKey resolved from
  each side's own interface).
