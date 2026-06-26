# Container App Example

A minimal Python container for the Privasys confidential platform
(`enclave-os-virtual`, TDX). It **boots ready** — there is no configuration
step — and shows persistent state on the per-app **sealed volume** at
`/data`, whose encryption key is reconstructed from the Enclave Vault
constellation at boot. The host never sees the key or the plaintext.

## What it shows

| Endpoint            | Behaviour                                            |
|---------------------|-----------------------------------------------------|
| `GET /health`       | 200 healthy (the manager's readiness probe)         |
| `GET /version`      | 200 + `APP_VERSION`                                  |
| `GET /`             | 200 + app info                                      |
| `POST /store`       | `{"key","value"}` → write to the sealed volume      |
| `POST /fetch`       | `{"key"}` → read it back                             |

`store` and `fetch` are declared as MCP tools in
[`privasys.json`](./privasys.json), so they appear in the developer
portal's **API Testing** / **AI Tools** tabs and are callable by agents.

Data written under `/data` survives restarts and platform/app upgrades
(after the app owner approves the new measurement).

## Variants

- **This repo** — no configuration; the app serves immediately.
- **[container-app-example-with-config](https://github.com/Privasys/container-app-example-with-config)**
  — the *configure-then-freeze* pattern: the app boots frozen (every path
  returns HTTP 503) until the deployer injects a secret via `POST /configure`,
  which is committed to the per-container RA-TLS leaf. That variant also
  demonstrates the data-owner / enclave-upgrade approval flows.

## Build

A `v*` tag builds `ghcr.io/privasys/container-app-example:<tag>` via the
GitHub Action in [`.github/workflows/build.yml`](./.github/workflows/build.yml).
