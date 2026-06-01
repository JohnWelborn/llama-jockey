# llama-jockey


CLI to control a [llama-server](https://github.com/ggerganov/llama.cpp/releases) running in router mode with multiple models
```
llama-jocket -> llama-server
```

Optionally over a local network, with [Caddy](https://github.com/caddyserver/caddy/releases) providing TLS termination on the server side.

```
llama-jockey  ──(HTTPS, local network)──▶  Caddy  ──(HTTP, localhost)──▶  llama-server
  (client)                                (TLS proxy)                      (GPU machine)
```

## CLI Setup

```
cp llama-jockey.example.yaml llama-jockey.yaml
# edit llama-jockey.yaml with your server URL and API key
```

## CLI Usage

```
llama-jockey.py models           # list models
llama-jockey.py load <model>     # load by name or list number
llama-jockey.py unload [model]   # unload one model, or all
llama-jockey.py chat "<prompt>"  # one-shot chat with the active model
```

If you use Caddy for TLS termination but you don't install the Caddy root certificate, on first HTTPS connection to an untrusted certificate, llama-jockey will prompt to trust and pin the server's SHA-256 fingerprint into `llama-jockey.yaml`.

## Server setup (Windows)

### Caddy (optional)

Download a pre-built release from the [Caddy releases page](https://github.com/caddyserver/caddy/releases).

Create a `CaddyFile` alongside `caddy.exe`.

Optional: `skip_install_trust` avoids installing the self-signed cert into the Windows certificate store — llama-jockey handles trust via fingerprint pinning instead.

**`CaddyFile`**
```
{
    skip_install_trust
}

https://<server-ip>:8081 {
    tls internal
    reverse_proxy 127.0.0.1:8081
}
```

Start Caddy with:

**`START-caddy.bat`**
```bat
C:\path\to\caddy.exe run --config CaddyFile
pause
```

### llama-server


Download a pre-built release from the [llama.cpp releases page](https://github.com/ggml-org/llama.cpp/releases).

For Windows with an NVIDIA GPU, download both of these from the same release and extract them into the same folder:

- `llama-b9351-bin-win-cuda-13.1-x64.zip` = the server binary
- `cudart-llama-bin-win-cuda-13.1-x64.zip` = the CUDA runtime DLLs (required for GPU support)

The CUDA runtime DLLs must be in the same directory as the executable. You can verify GPU detection with:

```
llama-server.exe --list-devices
```

Create `model-presets.ini` to define per-model parameters. The `[*]` section applies defaults to all models.

**`model-presets.ini`**
```ini
[*]
n-gpu-layers = 99
ctx-size = 32768

[Mistral-Small-3.1-24B-Instruct-2503:Q6_K]
model = C:\AI\LLMs\Mistral-Small-3.1-24B-Instruct-2503-Q6_K.gguf

[TheDrummer_Cydonia-24B-v4_2_0:Q8_0]
model = C:\AI\LLMs\TheDrummer_Cydonia-24B-v4_2_0-Q8_0.gguf
```

Model files can be found on Hugging Face:
[unsloth/Mistral-Small-3.1-24B-Instruct-2503-GGUF](https://huggingface.co/unsloth/Mistral-Small-3.1-24B-Instruct-2503-GGUF)
[bartowski/TheDrummer_Cydonia-24B-v4.2.0-GGUF](https://huggingface.co/bartowski/TheDrummer_Cydonia-24B-v4.2.0-GGUF)

Start llama-server with:

**`START-llama-server-router.bat`**
```bat
C:\path\to\llama-server.exe --models-preset "C:\path\to\model-presets.ini" --models-max 1 --no-models-autoload --host 127.0.0.1 --port 8081 --ctx-size 32768 --n-gpu-layers 99 --api-key <your-api-key> --sleep-idle-seconds 180
pause
```

Key flags:
- `--models-max 1` — only one model loaded at a time
- `--no-models-autoload` — models must be explicitly loaded via the API (or llama-jockey)
- `--sleep-idle-seconds 180` — unloads the model from VRAM after 3 minutes of inactivity
