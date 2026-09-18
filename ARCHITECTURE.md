# Architecture

Onboarding reference for engineers new to this codebase. For day-to-day build
rules, exact form/field semantics, and the full topology-layout spec, see
`CLAUDE.md` — this document covers the shape of the system, not every detail
of its behavior.

## 1. Overview

WireGuard Network Manager is a server-rendered Flask app for administering WireGuard
infrastructure: Hosts (servers), Clients (peers), Networks (IPAM pools/CIDR
blocks), and Keys (Curve25519 keypairs). It manages the associations between
these (which Host a Client connects to, which Network an interface lives on,
which Key an interface uses), auto-assigns IPs from a Network's CIDR, renders
`wg-quick`-compatible `.conf` files per interface, and visualizes the whole
topology as an interactive graph. It's a single-admin internal tool — no
multi-tenancy, no RBAC.

## 2. Component architecture

The app is a single Flask application (`app/__init__.py:create_app`)
composed of nine blueprints, each in its own package under `app/`, plus
shared service/util modules with no blueprint of their own. All blueprints
talk to MongoDB directly via `app.extensions.get_db()` (a lazily-initialized
global `pymongo.MongoClient` — no per-request session object, no ODM).

**`auth`** (`url_prefix=/auth`) — login/logout. `app/auth/models.py` defines
the Flask-Login `User` wrapper and `user_loader` over the `users` collection.
Every other blueprint's routes are gated by `@login_required` against this.

**`main`** (no prefix, `/`) — the dashboard. Pulls collection counts and
embeds the global topology graph (fed by the `topology` blueprint's JSON
endpoint, client-side).

**`networks`** (`/networks`) — CRUD for Networks (CIDR + `network_type` +
optional `managing_host_id`), the per-network address table (`/networks/<id>`,
listing assignments up to 1024 addresses), and `/networks/<id>/next-free-ip`
(used by Host/Client forms' IP auto-fill). Uses `app/utils/ipam.py` for
CIDR/IP validation and free-IP search; queries `hosts`/`clients` directly to
compute IP usage (IPAM has no separate "assignment" collection — usage is
derived by scanning `network_memberships` on both).

**`hosts`** (`/hosts`) — CRUD for Hosts, and for each Host's *interfaces*
(`network_memberships` entries — one per Network the Host belongs to, each
with its own type, hostname/port/DNS/MTU, and key) and *peer connections*
(Host↔Host, for P2P links). Depends on `app/keys/service.py` (key
generation/assignment/rotation, per-interface), `app/services/tunnel.py`
(per-interface `.conf` rendering), `app/services/graph.py` (detail-page
graph), and `app/utils/ipam.py`.

**`clients`** (`/clients`) — CRUD for Clients, their network memberships, and
their `connections` (Client→Host, per Network). Same shared-service
dependencies as `hosts`, plus `app/utils/crypto.py` directly (for
`is_valid_wg_key`/`decrypt_private_key` on the detail/create pages) and a
direct import of `NetworkMembershipForm` from `app.hosts.forms` — Clients'
"add network membership" form is literally the Host interface form's base
class, reused across the blueprint boundary rather than duplicated.

**`keys`** (`/keys`) — standalone Key CRUD (create/edit-name/delete) and the
cross-entity `/keys/<id>/assign` picker (lists every Client and every
WireGuard-capable Host interface as assignment targets). Delegates all actual
storage/usage logic to `app/keys/service.py`. Also imports
`INTERFACE_TYPE_LABELS` from `app.hosts.forms` to label Host-interface
targets in that picker — another blueprint-boundary form/label reuse.

**`dns_servers`** (`/dns-servers`) and **`allowed_ips`** (`/allowed-ips`) —
small, near-identical CRUD blueprints for two reference collections consumed
by Hosts/Clients (DNS server presets, AllowedIPs CIDR presets). Each blocks
deletion while any Host/Client still references it (via a `_in_use` count
query against `hosts`/`clients`).

**`topology`** (`/topology`) — a single JSON endpoint,
`GET /topology/graph.json` (delegates to `build_full_graph()`), with no page
of its own. Feeds the dashboard's client-side vis-network render. Per-entity
graphs (`/hosts/<id>/graph.json`, `/clients/<id>/graph.json`) are instead
owned by the `hosts`/`clients` blueprints themselves, not this one.

**Shared, blueprint-less modules:**
- `app/services/tunnel.py` — builds one `wg-quick` `.conf` file (as a string)
  per interface; called by both `hosts` and `clients` routes.
- `app/services/graph.py` — builds vis-network-compatible node/edge JSON;
  called by `hosts`, `clients`, and `topology`.
- `app/keys/service.py` — key generation (PyNaCl), storage (Fernet-encrypted),
  assignment/rotation, and derived usage lookup; called by `hosts`, `clients`,
  and `keys` routes.
- `app/utils/crypto.py` — keypair generation/derivation, Fernet
  encrypt/decrypt, key-format validation. No Flask/Mongo dependency.
- `app/utils/ipam.py` — pure `ipaddress`-based CIDR/IP helpers. No Flask/Mongo
  dependency.
- `app/extensions.py` — process-global `MongoClient`/`Fernet`/`LoginManager`
  singletons and their `get_db()`/`get_fernet()` accessors.

```mermaid
flowchart TB
    subgraph BP["Blueprints"]
        auth["auth<br/>/auth"]
        main["main<br/>/"]
        networks["networks<br/>/networks"]
        hosts["hosts<br/>/hosts"]
        clients["clients<br/>/clients"]
        keys["keys<br/>/keys"]
        dns_servers["dns_servers<br/>/dns-servers"]
        allowed_ips["allowed_ips<br/>/allowed-ips"]
        topology["topology<br/>/topology"]
    end

    subgraph SVC["Shared services / utils (no blueprint)"]
        tunnel["services/tunnel.py"]
        graph["services/graph.py"]
        keysvc["keys/service.py"]
        crypto["utils/crypto.py"]
        ipam["utils/ipam.py"]
    end

    mongo[("MongoDB<br/>(via extensions.get_db())")]

    hosts --> keysvc
    hosts --> tunnel
    hosts --> graph
    hosts --> ipam
    hosts --> mongo

    clients --> keysvc
    clients --> tunnel
    clients --> graph
    clients --> crypto
    clients --> ipam
    clients --> mongo
    clients -. "imports NetworkMembershipForm" .-> hosts

    keys --> keysvc
    keys --> crypto
    keys --> mongo
    keys -. "imports INTERFACE_TYPE_LABELS" .-> hosts

    topology --> graph

    networks --> ipam
    networks --> mongo

    auth --> mongo
    main --> mongo
    dns_servers --> mongo
    allowed_ips --> mongo

    keysvc --> crypto
    keysvc --> mongo
    tunnel --> crypto
    tunnel --> mongo
    graph --> mongo
```

## 3. Request / deployment architecture

Everything — app server and database — runs in **one Docker container**,
supervised by `supervisord` (`docker/supervisord.conf`, copied into the image
by `docker/Dockerfile`). `supervisord` runs two long-lived programs:

- `mongod`, bound to `127.0.0.1:27017` only (not exposed outside the
  container), data directory `/data/db` (a named volume, `mongo_data`, so
  data survives container recreation).
- the Flask app under `gunicorn`:
  `gunicorn -w 2 --worker-class gthread --threads 4 --timeout 60 -b 0.0.0.0:5000 wsgi:app`.
  Note **`gthread`, not the default `sync` worker class** — with `sync`
  workers, an idle keep-alive connection ties up an entire worker process
  (browsers hold several open per origin), and with only 2 workers it doesn't
  take much for both to be stuck that way, starving real requests until
  gunicorn's `--timeout` kills the stalled worker (visible as `CRITICAL
  WORKER TIMEOUT` in `webapp.err.log`). `gthread` lets each worker run
  multiple threads, so an idle connection occupies a thread, not a whole
  worker.

A request from the browser hits gunicorn on the container's port 5000
(mapped to host `8080` by `docker-compose.yml`), is dispatched to a thread in
one of the two worker processes, routed through Flask's blueprint URL map,
and any DB access goes through the process-global PyMongo `MongoClient` (one
client shared by all threads/workers within a process — PyMongo pools
connections internally) to `mongod` over localhost.

```mermaid
flowchart LR
    Browser -->|"HTTP :8080 → :5000"| Gunicorn

    subgraph Container["Single Docker container"]
        subgraph Sup["supervisord (PID 1)"]
            Gunicorn["gunicorn master<br/>2 workers × gthread × 4 threads"]
            Mongod["mongod<br/>127.0.0.1:27017 only"]
        end
        Flask["Flask app (blueprints)"]
        PyMongo["PyMongo MongoClient<br/>(pooled, process-global)"]

        Gunicorn --> Flask
        Flask --> PyMongo
        PyMongo --> Mongod
    end

    Mongod --> Volume[("mongo_data volume<br/>/data/db")]
```

## 4. Data model

7 collections. Cross-references are almost all stored as **strings of the
referenced `ObjectId`** (e.g. `active_key_id`, `network_id`, `host_id`,
`peer_host_id`, `allowed_ips_set_id`, `dns_server_id`), matched at query time
with `str(...)` comparisons rather than casting back to `ObjectId` — the one
exception is `networks.managing_host_id`, which is stored as a real
`ObjectId`. Worth knowing before writing a raw Mongo query by hand.

**`users`** — `_id`, `username`, `password_hash`. Seeded once from
`ADMIN_USERNAME`/`ADMIN_PASSWORD` if empty (`app/bootstrap.py`). No other
collection references it.

**`networks`** — `_id`, `name`, `cidr`, `description`, `network_type` (one of
`p2p`, `ipam`, `host_network`; missing on old docs → treat as `"ipam"`),
`managing_host_id` (nullable `ObjectId` into `hosts`, only meaningful when
`network_type == "host_network"`). Pure IPAM pool — no stored "assignment"
subdocuments; a Network's used IPs are derived by scanning
`hosts.network_memberships` / `clients.network_memberships` for matching
`network_id`.

**`hosts`** — `_id`, `name`, plus two embedded arrays:
- `network_memberships[]`: one **interface** per entry — `network_id`, `ip`,
  `interface_name`, `interface_type` (`p2p` / `client` / `client_non_wg`;
  missing → `"client"`), `hostname`, `listen_port`, `dns_server_id`, `mtu`,
  `active_key_id`. `interface_type` constrains which `network_type` that
  interface may attach to (`p2p`→`p2p`, `client`/`client_non_wg`→
  `host_network`; `ipam` networks accept no interface). A Host has **no**
  top-level key/hostname/port/DNS/MTU — those all live per-interface, since
  each interface is a distinct WireGuard identity.
- `peer_connections[]`: Host↔Host P2P links, one per `p2p`-type interface —
  `peer_host_id`, `network_id`, `allowed_ips_set_id`, `endpoint_override`,
  `persistent_keepalive`. Created/removed reciprocally on both Hosts.

**`clients`** — `_id`, `name`, `active_key_id`, `dns_server_id`, plus:
- `network_memberships[]`: `network_id`, `ip`, `interface_name` (no
  `interface_type` — Clients only ever attach to `host_network` Networks and
  connect to Hosts, never Host-network-type-gated the way Host interfaces
  are).
- `connections[]`: `host_id`, `network_id`, `allowed_ips_set_id`,
  `persistent_keepalive` — one per Host the Client connects to, on a given
  Network; can have more than one entry for the same `network_id` (e.g. two
  Hosts on the same Network for redundancy).

**`keys`** — `_id`, `name`, `public_key`, `private_key` (Fernet-encrypted),
`created_at`. **No owner/back-reference field at all** — a key document
never records who uses it. "In use by" is derived entirely by reverse query
(`key_usages()` in `app/keys/service.py`, scanning
`hosts.network_memberships.active_key_id` and `clients.active_key_id`).
Deliberate: a key can be attached to zero, one, or many Host
interfaces/Clients simultaneously.

**`dns_servers`** — `_id`, `name`, `ips` (raw comma-delimited string,
verbatim). Referenced by `hosts.network_memberships[].dns_server_id` and
`clients.dns_server_id`.

**`allowed_ips`** — `_id`, `name`, `cidrs` (raw comma-delimited string,
verbatim). Referenced by `hosts.peer_connections[].allowed_ips_set_id` and
`clients.connections[].allowed_ips_set_id`.

```mermaid
erDiagram
    USERS {
        ObjectId _id
        string username
        string password_hash
    }
    NETWORKS {
        ObjectId _id
        string name
        string cidr
        string network_type
        ObjectId managing_host_id
    }
    HOSTS {
        ObjectId _id
        string name
        array network_memberships
        array peer_connections
    }
    CLIENTS {
        ObjectId _id
        string name
        string active_key_id
        string dns_server_id
        array network_memberships
        array connections
    }
    KEYS {
        ObjectId _id
        string name
        string public_key
        string private_key
        datetime created_at
    }
    DNS_SERVERS {
        ObjectId _id
        string name
        string ips
    }
    ALLOWED_IPS {
        ObjectId _id
        string name
        string cidrs
    }

    HOSTS ||--o{ NETWORKS : "managing_host_id (host_network)"
    NETWORKS }o--o{ HOSTS : "network_memberships[].network_id"
    NETWORKS }o--o{ CLIENTS : "network_memberships[].network_id"
    HOSTS }o--o{ CLIENTS : "connections[].host_id"
    HOSTS }o--o{ HOSTS : "peer_connections[].peer_host_id"
    KEYS ||--o{ HOSTS : "network_memberships[].active_key_id"
    KEYS ||--o{ CLIENTS : "active_key_id"
    DNS_SERVERS ||--o{ HOSTS : "network_memberships[].dns_server_id"
    DNS_SERVERS ||--o{ CLIENTS : "dns_server_id"
    ALLOWED_IPS ||--o{ HOSTS : "peer_connections[].allowed_ips_set_id"
    ALLOWED_IPS ||--o{ CLIENTS : "connections[].allowed_ips_set_id"
```

Note the direction of the `KEYS` edges: they're drawn *from* `HOSTS`/`CLIENTS`
*into* `KEYS`, because that's where the actual field lives (`active_key_id`
on the host-interface/client side). `KEYS` itself carries no reciprocal
field — there's deliberately no arrow drawn originating at `KEYS`, since
querying "who uses this key" means scanning the referencing collections, not
reading anything off the key document.

## 5. Key architectural decisions

- **No ORM.** Every collection access is raw PyMongo (`get_db().<collection>`)
  — filters, `$set`/`$push`/`$pull`, `array_filters` for nested-array updates.
  No Mongoengine/ODM layer, no schema enforcement beyond what routes/forms
  check before writing.
- **Keys are not exclusively owned.** A key can be attached to zero, one, or
  many Host interfaces/Clients at once; the UI warns but never blocks this.
  Usage is always derived by reverse query, never stored on the key.
- **Tunnel configs are generated per interface, not per Host/Client.** Each
  `network_memberships` entry is independently exportable; there is no
  single "everything this Host/Client is attached to" config file.
- **Three Network types gate what can attach.** `p2p`, `ipam`, and
  `host_network` aren't just labels — `ipam` networks accept no Host/Client
  interface at all (pure CIDR-space bookkeeping), `p2p` networks only accept
  a Host's `p2p`-type interface (and back Host↔Host peering), and
  `host_network` networks are the only valid target for Client attachment
  or a Host's `client`/`client_non_wg` interface.
- **Private keys are shown in the clear**, decrypted on the fly, no masking
  or reveal step — on a Client's detail page and a Key's own edit page.
  Deliberate: single-admin internal tool, no untrusted-viewer concern.
- **Everything runs in one container.** `mongod` and the Flask app are
  supervised as sibling processes by `supervisord`, not split into separate
  services/containers — simplicity over isolation, appropriate for a
  single-admin internal tool.

## 6. Topology visualization

`app/services/graph.py` builds vis-network-compatible `{nodes, edges}` JSON
consumed client-side (CDN `vis-network`, no build step, `physics: false`,
hierarchical layout with explicit per-node `level`). Three entry points:
`build_full_graph()` (dashboard's global graph, fed by
`GET /topology/graph.json`), `build_host_graph(host_id)` and
`build_client_graph(client_id)` (one-hop neighborhoods on each entity's
detail page, fed by `/hosts/<id>/graph.json` / `/clients/<id>/graph.json`).
Nodes are Networks (gray boxes, labeled by CIDR), Hosts (blue boxes), and
Clients (green ellipses); edges represent network membership, Client→Host
connections, Host↔Host peering (drawn as two segments through the shared
Network node), and Network supernet→subnet containment. The three graphs use
different, non-interchangeable vertical-tier rules (Networks-on-top globally
vs. Hosts-on-top on the per-entity pages) — see CLAUDE.md's "Visual/UI
conventions" section for the full layout spec; this section is intentionally
just the map of what lives where.
