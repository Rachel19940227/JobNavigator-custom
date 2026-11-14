# Codex CLI subscription provider

JobNavigator can use the locally installed Codex CLI for **job scoring** and
**resume tailoring**. This route uses the ChatGPT/Codex subscription authenticated
by `codex login`; it never reads `OPENAI_API_KEY` and does not use OpenAI API
credits.

## Host setup (Docker deployment)

1. Install the Codex CLI and authenticate on the Mac:

   ```sh
   codex login
   codex login status
   ```

2. Keep the required local fallback ready:

   ```sh
   ollama pull qwen3:8b
   ollama list
   ```

3. Start the loopback-only host runner before Docker:

   ```sh
   ./scripts/start_codex_runner.sh
   docker compose up -d --build
   ```

   On macOS the script automatically detects the Codex binary bundled inside
   `/Applications/ChatGPT.app`, even when the process has a minimal `PATH`.

   The runner creates `runtime/codex_runner_token` with mode `0600`. The runtime
   directory is gitignored. Do not copy `~/.codex`, `auth.json`, or the token into
   the repository, an image, logs, or a support request.

   Start this runner again after restarting or logging back into the Mac. When the
   repository is under macOS `Documents`, launchd may be denied access by macOS
   privacy controls; the start script is the reliable no-permission-change path.

4. In **Settings → Models**, set both **Scoring** and **Tailoring** overrides to
   `Codex CLI (ChatGPT subscription) / gpt-5.6-luna`. The status row shows
   CLI installation, login, version, Ollama reachability, and whether `qwen3:8b`
   is installed.

## Safety and fallback

The container can request only `score_light`, `score_full`,
`resume_tailoring`, or `cover_letter`; it cannot submit a shell command. Each CLI call receives its
prompt on standard input, runs in a new empty temporary directory with a
read-only sandbox, uses an output JSON Schema, and deletes temporary files when
done. Full prompts, resumes, job descriptions, and credentials are not logged.

Fallback is allowed only for these classified Codex failures:

- subscription quota/rate limit (`CODEX_QUOTA_EXCEEDED`)
- login/authentication failure (`CODEX_AUTH_REQUIRED`)
- timeout (`CODEX_TIMEOUT`)
- missing/crashed/non-zero process (`CODEX_PROCESS_FAILED`)
- missing, damaged, or schema-invalid result (`CODEX_INVALID_OUTPUT`)

Authentication and quota failures fall back immediately. Execution/timeout/
invalid-output failures retry Codex once, then fall back. The only fallback is
local `Ollama / qwen3:8b`; its invalid JSON receives one repair attempt. User
cancellation and business validation errors never fall back. If both providers
fail, the task fails explicitly and no score or resume is fabricated.

The subscription's own usage limits still apply. JobNavigator explicitly passes
`--model gpt-5.6-luna` to the installed CLI for scoring and tailoring.
