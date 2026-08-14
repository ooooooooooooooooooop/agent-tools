#!/usr/bin/env bash

while IFS= read -r line; do
  received_hex=$(printf '%s' "$line" | od -An -tx1 | tr -d ' \n')
  printf '\033]0;RECEIVED_HEX_%s\007' "$received_hex"
  if [[ "$line" == "SWITCHBOARD_PTY_TEST_OK" || "$line" == "SWITCHBOARD_终端_OK" ]]; then
    exit 0
  fi
done
