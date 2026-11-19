# Contract: PAC configuration

The PAC in use is configured in each instance's `.env`, read by `app/core/config.py`. An environment variable of the same name overrides it, as for every setting. `PAC_PROVIDER` is required: an instance whose configuration does not name its PAC refuses to start. Every name below is new except `APP_ENV` and `PAC_PROVIDER`.

| Variable | Values | Default | Required when |
|---|---|---|---|
| `APP_ENV` | `development`, `staging`, `production` | `production` | (spec 039) |
| `PAC_PROVIDER` | `none`, `simulated`, `multifacturas` | — (no default) | always |
| `MULTIFACTURAS_USER` | the PAC account | — | `PAC_PROVIDER=multifacturas` |
| `MULTIFACTURAS_PASSWORD` | the PAC account's password | — | `PAC_PROVIDER=multifacturas` |
| `MULTIFACTURAS_PRODUCTION` | `true`, `false` | `false` | |
| `MULTIFACTURAS_URL` | an `https://` base URL | `https://ws.multifacturas.com` | |
| `MULTIFACTURAS_CONNECT_TIMEOUT` | seconds, > 0 | `10` | |
| `MULTIFACTURAS_READ_TIMEOUT` | seconds, > 0 | `60` | |

## Startup refusals

The application and the resolve job refuse to start in the cases below. Each message names the settings involved and never their values.

| Condition | Message names |
|---|---|
| `PAC_PROVIDER` not set | `PAC_PROVIDER` |
| `PAC_PROVIDER=simulated` and `APP_ENV=production` | `PAC_PROVIDER`, `APP_ENV` (spec 039, unchanged) |
| `PAC_PROVIDER=multifacturas` and the user or password is missing | the missing variable(s) |
| `PAC_PROVIDER=multifacturas`, `MULTIFACTURAS_PRODUCTION=true`, and `APP_ENV` is not `production` | `MULTIFACTURAS_PRODUCTION`, `APP_ENV` |
| `PAC_PROVIDER=multifacturas`, `MULTIFACTURAS_PRODUCTION=false`, and `APP_ENV=production` | `MULTIFACTURAS_PRODUCTION`, `APP_ENV` |
| `PAC_PROVIDER=multifacturas` and the URL is not `https://`, or a timeout is not positive | the variable |

## Per instance after this spec

| Instance | `APP_ENV` | `PAC_PROVIDER` | Mode |
|---|---|---|---|
| dev :8765 | `development` | `simulated` (the library's simulator, state in `pac_simulator_store`) | n/a |
| test :8766 | `staging` | `multifacturas` (user's decision 2026-11-18; set with confirmation) | test |
| production | `production` | `none`, written explicitly in its `.env` before this code reaches it; until the cutover (#57) | n/a |

## Not configured here

- **SAT's status service** uses the library's defaults: SAT's public URL, 10 s connect, 60 s read.
- **The issuer's CSD** comes from `taxpayer_certificate` for each request, as in spec 039.
