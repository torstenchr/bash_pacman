#!/usr/bin/env bash

export PS4='+ $(date "+%s.%N") ${FUNCNAME[0]:+${FUNCNAME[0]}(): }'
exec 3>&2 2>trace.log
set -x

bash -x -- "$@"

set +x
exec 2>&3 3>&- 