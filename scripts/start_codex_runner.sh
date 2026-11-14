#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
mkdir -p runtime

# GUI-launched shells and launchd often do not inherit Homebrew/ChatGPT paths.
# Pin ChatGPT.app's bundled CLI when no explicit path was supplied.
if [ -z "${CODEX_CLI_PATH:-}" ] && [ -x /Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex ]; then
  export CODEX_CLI_PATH=/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex
elif [ -z "${CODEX_CLI_PATH:-}" ] && [ -x /Applications/ChatGPT.app/Contents/Resources/codex ]; then
  export CODEX_CLI_PATH=/Applications/ChatGPT.app/Contents/Resources/codex
fi

exec /usr/bin/env python3 scripts/codex_runner.py "$@"
