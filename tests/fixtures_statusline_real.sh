#!/usr/bin/env bash
input=$(cat)
# >>> BOOST-STATUS-LINE
# Managed by Boost (boost-claude-status-line). Do not edit this block.
if [ -z "${input+x}" ]; then
  input=$(cat)
fi
printf '%s' "$input" | boost status-line 2>/dev/null || true
# <<< BOOST-STATUS-LINE
# >>> local-tts statusline hook (managed by `tts hooks`) — do not edit by hand
mkdir -p '$HOME/.local/share/local-tts/hooks' 2>/dev/null
date +%s > '$HOME/.local/share/local-tts/hooks/claude-code.heartbeat' 2>/dev/null
if command -v tts >/dev/null 2>&1; then
  __localtts_bar="$(tts playback --compact 2>/dev/null)"
  if [ -n "$__localtts_bar" ]; then printf ' · %s' "$__localtts_bar"; fi
fi
# <<< local-tts statusline hook
# >>> claude-statusline (managed by JairoTorregrosa/claude-statusline install) — do not edit by hand
if [ -x "$HOME/.local/bin/claude-statusline" ]; then
  printf '\n'
  printf '%s' "$input" | "$HOME/.local/bin/claude-statusline" 2>/dev/null || true
fi
# <<< claude-statusline
