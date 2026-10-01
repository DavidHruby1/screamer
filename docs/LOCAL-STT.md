# Local STT with Speaches

## Summary

Screamer can send STT requests to an endpoint on the local machine with an empty STT
API key. This page records one **actual Linux CPU smoke configuration** for Speaches
v0.8.3 and gives Docker Desktop/WSL2 setup instructions for Windows that have not been
run or certified on Windows. It is a reproducible starting point, not a general
Speaches compatibility guarantee, an offline Windows acceptance result, or a privacy
firewall. Screamer's maintained STT/configuration contract remains in
[IMPLEMENTATION.md](IMPLEMENTATION.md).

## Start Here

- [`src/config.py`](../src/config.py): keyless STT completeness requires base URL and
  model; LLM credentials retain their separate key requirement.
- [`src/stt.py`](../src/stt.py): builds the multipart request, endpoint, optional auth,
  response parsing and fallback behavior used by the app.
- [`src/settings_dialog.py`](../src/settings_dialog.py): the STT tab accepts a blank
  key and expects a base URL rather than a completed transcription route.
- [`src/main.py`](../src/main.py): the app passes the recording-start config snapshot to
  `transcribe()` and may subsequently invoke the separately configured rewrite step.

## Tested Linux Configuration

Observed smoke environment:

| Item | Tested value |
| --- | --- |
| Speaches release source | `v0.8.3`, source commit `c78b77d` |
| Container | `ghcr.io/speaches-ai/speaches@sha256:1d4f852ff5b148d675bcd751f414835c0bcef2541c1df8ffdeb43714968aafe6` |
| Manifest/platform | Linux `amd64`; digest resolved from the v0.8.3 CPU image |
| Docker | 29.1.3 on Linux |
| Compute | CPU, `int8` |
| Published endpoint | `127.0.0.1:18000` on host to port `8000` in container |
| Model | `Systran/faster-whisper-tiny.en` |
| Screamer settings | Base URL `http://127.0.0.1:18000/v1`, model above, empty STT key, language `en`, LLM rewrite off, STT fallback off |
| Result | PASS: real multipart-file/model/`verbose_json` request returned nonempty `text` |

The smoke caused the server's `POST /v1/models/Systran/faster-whisper-tiny.en` model
download operation, fetched the public JFK WAV from whisper.cpp v1.7.6
(`samples/jfk.wav`), and called Screamer's actual `transcribe()` against the running
container. The observed transcript was:

> And so, my fellow Americans ask not what your country can do for you ask what you can do for your country.

The container was stopped after the request. This verifies a Linux request/response
path for this one image digest and model. It does **not** measure transcription quality,
test Windows, prove operation without network access, verify custom-auth behavior on
the server, or certify other Speaches versions/models.

The source audit used the exact v0.8.3 tag sources rather than inferring routes from
project name:

- [STT router](https://raw.githubusercontent.com/speaches-ai/speaches/v0.8.3/src/speaches/routers/stt.py)
- [API types](https://raw.githubusercontent.com/speaches-ai/speaches/v0.8.3/src/speaches/api_types.py)
- [Dependencies](https://raw.githubusercontent.com/speaches-ai/speaches/v0.8.3/src/speaches/dependencies.py)
- [Configuration](https://raw.githubusercontent.com/speaches-ai/speaches/v0.8.3/src/speaches/config.py)
- [Application setup](https://raw.githubusercontent.com/speaches-ai/speaches/v0.8.3/src/speaches/main.py)
- [Model router](https://raw.githubusercontent.com/speaches-ai/speaches/v0.8.3/src/speaches/routers/models.py)

The tag's FastAPI version metadata reports v0.8.2; that stale metadata was not used to
select the image. The recorded image digest is the runtime pin. Do not substitute a
mutable `latest` tag for it when reproducing this specific configuration.

## Windows Docker Desktop Instructions (Not Yet Verified)

These are instructions based on the tested Linux container settings, not evidence of a
Windows dictation. Install Docker Desktop with its WSL2/Linux-container backend first.
Docker Desktop must remain running while using the endpoint. The container is explicitly
Linux `amd64`; Docker Desktop supplies the Linux guest while Screamer runs on Windows
and connects through the published host loopback port.

In PowerShell, start the pinned CPU container:

```powershell
docker run --rm --name screamer-speaches --platform linux/amd64 `
  --publish 127.0.0.1:18000:8000 `
  --volume screamer-speaches-models:/home/ubuntu/.cache/huggingface/hub `
  --env WHISPER__INFERENCE_DEVICE=cpu `
  --env WHISPER__COMPUTE_TYPE=int8 `
  ghcr.io/speaches-ai/speaches@sha256:1d4f852ff5b148d675bcd751f414835c0bcef2541c1df8ffdeb43714968aafe6
```

The named volume retains downloaded model files across container removal. It does not
include the model in the image. While connected to the internet, request the model
download operation, then wait for the server to finish before attempting offline use:

```powershell
curl.exe -X POST http://127.0.0.1:18000/v1/models/Systran/faster-whisper-tiny.en
```

The model-download call is based on the v0.8.3 model-router source; the Windows command
itself has not been exercised. Keep the container running during model download and
dictation. To stop it, use `Ctrl+C` in the terminal running `docker run`, or from a
second shell run `docker stop screamer-speaches`.

In Screamer Settings > STT, configure:

| Field | Value |
| --- | --- |
| Base URL | `http://127.0.0.1:18000/v1` |
| Model | `Systran/faster-whisper-tiny.en` |
| API key | Leave blank |
| Language | `en` (or Auto if desired) |
| STT fallback | Off for an offline attempt |
| AI Rewrite | Off for an offline attempt |

Screamer appends `/audio/transcriptions`; therefore enter the base URL shown above, not
the full route. With the `tiny.en` model, `en` is the tested language setting. Other
language/model combinations have not been evaluated here. The optional vocabulary STT
prompt is an endpoint-specific hint, not a guarantee that the model recognizes terms;
leave it disabled unless deliberately testing that feature. This example does not
establish prompt behavior or transcription quality.

Empty provider fields can still be backfilled from an existing `.env` at startup.
For a deliberately keyless setup, remove any `STT_API_KEY` backfill for that provider
and check custom headers too; otherwise a configured key generates Bearer auth even
when the endpoint is local. An explicitly saved Auto language is not backfilled.

For offline use, download the model before disconnecting external networking, retain
the model volume, turn AI Rewrite off, and disable any cloud STT fallback. Local STT by
itself does not constrain egress: if cloud fallback or LLM rewrite remains enabled,
Screamer can still send requests to those configured services. A loopback URL setting
is not a privacy enforcement mechanism.

## Request Boundary and Limits

Screamer sends a multipart WAV request to the configured base URL plus
`/audio/transcriptions`. It includes `model`, uses `verbose_json` for non-Groq providers,
adds `language` only when configured, and omits generated `Authorization: Bearer ...`
when the key is empty. Custom headers remain separately configured and can supply
alternative authorization. The keyless behavior belongs to STT only; LLM validation
still requires key, URL and model. Existing STT fallback rules remain in force.

No claim is made here about `whisper.cpp` servers, other Speaches versions, alternative
models, server authentication, installation-specific routing, offline operation on
Windows, or model quality. Screamer does not discover servers, download models itself,
or prevent non-local traffic. Configure and verify any changed version or endpoint
independently.

## Sources

- [`src/config.py`](../src/config.py): STT-specific URL/model completeness, validation,
  and the distinct LLM key requirement.
- [`src/stt.py`](../src/stt.py): multipart fields, appended endpoint, auth omission,
  language, response parsing and fallback behavior.
- [`src/settings_dialog.py`](../src/settings_dialog.py): blank-key guidance and base-URL
  instructions in STT settings.
- [`src/main.py`](../src/main.py): per-session config snapshot and subsequent worker
  pipeline behavior.
- [Speaches v0.8.3 router and model source links above](https://raw.githubusercontent.com/speaches-ai/speaches/v0.8.3/src/speaches/routers/stt.py): exact tagged route/model behavior inspected for this recipe.
