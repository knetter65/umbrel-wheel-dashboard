# Private TWS Relay for umbrelOS

A minimal, payload-blind TCP relay that is lifecycle-independent from Hermes Agent.

## Security properties

- Binds only to an explicitly configured private, loopback, or link-local IP.
- Rejects wildcard, multicast, and public endpoints.
- Logs status events but never application payloads.
- Runs as UID/GID 1000 with all Linux capabilities dropped.
- Uses a read-only root filesystem and `no-new-privileges`.
- Provides health through a private Unix socket, so healthchecks do not open broker connections.
- Caps concurrent relay sessions.
- Contains no broker credentials or order API.

The broker-enforced **Read-Only API** option in TWS remains the hard authorization boundary.

## Required service environment

Configure these four variables on the `relay` service through umbrelOS app settings:

- `TWS_RELAY_LISTEN_HOST`
- `TWS_RELAY_LISTEN_PORT`
- `TWS_RELAY_REMOTE_HOST`
- `TWS_RELAY_REMOTE_PORT`

Optional:

- `TWS_RELAY_CONNECT_TIMEOUT` (default `5`)
- `TWS_RELAY_MAX_CONNECTIONS` (default `8`)

Do not commit local endpoint values or secrets to this app package.

## Migration safety

The existing relay embedded in the Hermes Agent compose remains the production fallback until:

1. the standalone relay is installed on a non-conflicting private endpoint;
2. container and Unix-socket healthchecks pass;
3. a raw TCP test passes;
4. the read-only TWS application handshake passes;
5. client configuration is switched and verified;
6. rollback to the old endpoint has been tested.

Only after these gates may the old `tws_relay` service be removed from the Hermes compose.
