#!/usr/bin/env bash
# Creates a dedicated read-only account for front-door acceptance, no secret output.
set -euo pipefail
umask 077
[[ $EUID -eq 0 ]] || { echo 'Run with sudo.' >&2; exit 1; }
root=/opt/kumi-care/mosquitto
client_dir=/home/che/.config/kumi-care
exec 9>"$root/observer-setup.lock"
flock -n 9 || { echo 'Observer setup already running.' >&2; exit 1; }
if [[ -f "$client_dir/observer.env" ]]; then
  docker kill --signal HUP kumi-care-mosquitto >/dev/null
  echo 'Observer profile already exists; broker config reloaded.'
  exit 0
fi
if grep -q '^user care-observer$' "$root/config/acl" || grep -q '^care-observer:' "$root/config/passwords"; then
  echo 'Partial observer setup found; inspect before retry.' >&2; exit 1
fi
work=$(mktemp -d "$root/observer-setup.XXXXXX")
trap 'rm -rf "$work"' EXIT
python3 - "$work" <<'PY'
from pathlib import Path
import secrets, sys
p=Path(sys.argv[1]); password=secrets.token_hex(32)
(p/'passwords').write_text('care-observer:'+password+'\n')
(p/'observer.env').write_text('MQTT_HOST=kumi.kumiland.cloud\nMQTT_PORT=8883\nMQTT_USERNAME=care-observer\nMQTT_PASSWORD='+password+'\nCARE_HOME_ID=main\nCARE_DEVICE_ID=front-door\n')
PY
image=$(docker inspect --format '{{.Config.Image}}' kumi-care-mosquitto)
docker run --rm --network none --user 0:0 --entrypoint mosquitto_passwd \
  -v "$work:/work" "$image" -U /work/passwords
cp -p "$root/config/passwords" "$root/config/passwords.before-observer"
cp -p "$root/config/acl" "$root/config/acl.before-observer"
cat "$root/config/passwords" "$work/passwords" > "$work/passwords.all"
cat "$root/config/acl" > "$work/acl"
printf '\nuser care-observer\ntopic read care/v1/home/main/device/front-door/event\n' >> "$work/acl"
install -o 1883 -g 1883 -m 640 "$work/passwords.all" "$root/config/passwords.next"
install -o 1883 -g 1883 -m 640 "$work/acl" "$root/config/acl.next"
mv "$root/config/passwords.next" "$root/config/passwords"
mv "$root/config/acl.next" "$root/config/acl"
docker kill --signal HUP kumi-care-mosquitto >/dev/null
install -o che -g che -m 600 "$work/observer.env" "$client_dir/observer.env"
printf 'Observer ready: front-door read-only; credentials not printed.\n'
