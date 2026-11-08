#!/bin/sh
# HTTP input is never evaluated, interpolated into a command or used as a path.
export PATH=/bin
if [ "$REQUEST_METHOD" != GET ] || [ -n "$QUERY_STRING" ]; then
    printf 'Status: 400 Bad Request\r\nContent-Type: text/plain\r\n\r\nGET without query required\n'
    exit 0
fi
printf 'Content-Type: application/json\r\nCache-Control: no-store\r\n\r\n'
cat /run/status.json
