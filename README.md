# Pinchana Pinterest

Standalone Pinterest pin scraper for the Pinchana API. It resolves normal `pinterest.com` pin links and `pin.it` short links, downloads the media into a bounded local cache, and exposes both the current Pinchana contract and the legacy bot endpoint.

Boards, profiles, and search pages are intentionally rejected. Only individual pins are supported.

## API

```bash
curl -X POST http://localhost:8090/scrape \
  -H 'content-type: application/json' \
  -d '{"url":"https://www.pinterest.com/pin/123456789/"}'
```

Compatibility endpoint:

```bash
curl 'http://localhost:8090/v2/pinterest?link=https://www.pinterest.com/pin/123456789/'
```

Routes:

- `POST /scrape`
- `GET /v2/pinterest?link=...`
- `GET /media/pinterest/{pin_id}/{filename}`
- `GET /health`

## Configuration

| Variable | Default | Purpose |
| --- | ---: | --- |
| `CACHE_PATH` | `./cache` | Persistent cache directory |
| `CACHE_MAX_SIZE_GB` | `2` | Hard target for completed cached entries |
| `MAX_MEDIA_SIZE_MB` | `100` | Maximum size of one downloaded media file |
| `PINTEREST_TIMEOUT_SECONDS` | `30` | Pinterest page request timeout |
| `PINTEREST_API_KEY` | unset | If set, protects the legacy endpoint using `x-api-key` |
| `PUBLIC_BASE_URL` | request origin | Public URL used by the legacy response |

The service enforces both a total-cache limit and a per-file limit. Old completed entries are evicted first. Partial downloads use `.part` files and are removed on failure.

## Docker

```bash
docker build -t pinchana-pinterest .
docker run --rm -p 8090:8090 \
  -e CACHE_MAX_SIZE_GB=2 \
  -v pinterest-cache:/app/cache \
  pinchana-pinterest
```

Published images are built as `ghcr.io/pinchana/pinchana-pinterest:latest` after changes reach `main`.

## Development

```bash
uv sync
uv run pytest
uv run uvicorn pinchana_pinterest.main:app --reload --port 8090
```
