#!/bin/sh
set -e

if [ -f /home/user/app/secrets/proxy.env ]; then
  set -a
  . /home/user/app/secrets/proxy.env
  set +a
fi

exec uvicorn server:app --host 0.0.0.0 --port "${PORT:-8000}"
