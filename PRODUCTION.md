# HalalStream Production Notes

## Reliable YouTube Links

Some YouTube links reject datacenter IPs with a "not a bot" check. The app is ready for a paid proxy and browser cookies without changing code.

Create this file on the server only:

```sh
/opt/halalstream/secrets/proxy.env
```

Example content:

```sh
HALALSTREAM_YTDLP_PROXY=http://USER:PASSWORD@HOST:PORT
```

Then restart:

```sh
cd /opt/halalstream
docker compose up -d
```

Use a dedicated YouTube account for cookies. Do not commit `secrets/`.

## Domain

Current HTTPS URL:

```text
https://165.22.71.86.sslip.io
```

For a real domain, point an `A` record to:

```text
165.22.71.86
```

Then replace `165.22.71.86.sslip.io` in `Caddyfile` with the domain and run:

```sh
cd /opt/halalstream
docker compose up -d
```
