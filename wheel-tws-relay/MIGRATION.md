# TWS Relay Migration Inventory

Generated for the first architecture-decoupling phase. This record intentionally omits private network addresses, device identifiers, account identifiers, credentials, positions, and financial values.

## Current production contract

- Owner: the vendor-managed Hermes Agent compose (temporary state).
- Service name: `tws_relay`.
- Runtime image: coupled to the active Hermes Agent image.
- Network mode: host.
- Local transport: one explicitly bound private IPv4 endpoint using the standard live TWS client port.
- Upstream transport: one private Tailscale IPv6 endpoint on the Mac-side adapter port.
- Connect timeout: five seconds.
- Payload behavior: bidirectional forwarding with no parsing or payload logging.
- Code source: a read-only scripts-directory mount from persistent Hermes data.
- Application consumer: `/opt/data/ibkr-live/ibkr_readonly.py` with the dedicated virtual environment and Tailscale live configuration.
- Authorization boundary: TWS Read-Only API mode.
- Current upstream condition: TCP acceptance has been observed, but the TWS application handshake currently times out.

## Confirmed architecture risks

- A Hermes app update can replace the service definition.
- The relay currently inherits the Hermes image lifecycle even though it does not need Hermes.
- A relay mount error can prevent the full Hermes app from starting.
- The repair guard currently expects the relay to exist inside the Hermes compose.

## Target contract

- Dedicated app ID: `wheel-tws-relay`.
- Independent pinned Python image.
- No Hermes data mount.
- No credentials.
- Private endpoints supplied only as local service environment variables.
- Read-only root filesystem, all capabilities dropped, no-new-privileges.
- No public listener and no wildcard bind.
- Payload-free status page behind Umbrel app-proxy authentication.
- Unix-socket healthcheck that does not generate upstream broker sessions.
- Existing Hermes relay retained as rollback until end-to-end verification succeeds.

## Cutover gates

- [ ] Standalone package validates.
- [ ] Unit and loopback integration tests pass.
- [ ] Host endpoint preflight confirms no listener conflict.
- [ ] Standalone relay starts on a non-conflicting private endpoint.
- [ ] Container health is healthy.
- [ ] Status page reports ready without sensitive identifiers.
- [ ] TCP forwarding test passes.
- [ ] Read-only TWS application handshake passes.
- [ ] Client config switch is backed up and verified.
- [ ] Hermes remains ready after its embedded relay is removed.
- [ ] Post-cutover rollback test succeeds.
