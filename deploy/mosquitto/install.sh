#!/usr/bin/env bash
# Run on kumi as: sudo bash /home/che/kumi-care-deploy/install.sh
# Never prints generated passwords. First install only; refuses overwrite.
set -euo pipefail
umask 077
[[ $EUID -eq 0 ]] || { echo 'Run this installer with sudo.' >&2; exit 1; }
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
root=/opt/kumi-care/mosquitto
client_dir=/home/che/.config/kumi-care
[[ ! -e "$root" && ! -e "$client_dir" ]] || {
  echo 'Existing deployment/client files found; refusing to overwrite.' >&2; exit 1;
}
[[ $(id -u che) -eq 1000 ]] || { echo 'Unexpected service operator.' >&2; exit 1; }
if ss -lntH | awk '{print $4}' | grep -Eq ':8883$'; then
  echo 'Port 8883 is already occupied.' >&2; exit 1
fi
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io docker-compose-v2 openssl
systemctl enable --now docker
# Resolve the maintained 2.x image once, then run by immutable digest.
docker pull eclipse-mosquitto:2
image=$(docker image inspect --format '{{index .RepoDigests 0}}' eclipse-mosquitto:2)
[[ "$image" == eclipse-mosquitto@sha256:* ]] || { echo 'Image digest resolution failed.' >&2; exit 1; }
install -d -m 750 "$root" "$root/config" "$root/certs" "$root/data"
install -d -m 700 "$root/ca" "$root/admin"
install -d -o che -g che -m 700 "$client_dir"
install -m 644 "$source_dir/compose.yaml" "$root/compose.yaml"
install -m 640 "$source_dir/mosquitto.conf" "$root/config/mosquitto.conf"
install -m 640 "$source_dir/acl" "$root/config/acl"
printf 'MOSQUITTO_IMAGE=%s\n' "$image" > "$root/.env"

openssl req -x509 -newkey rsa:3072 -nodes -sha256 -days 3650 \
  -subj '/CN=Kumi Care MQTT CA' \
  -addext 'basicConstraints=critical,CA:TRUE' \
  -addext 'keyUsage=critical,keyCertSign,cRLSign' \
  -keyout "$root/ca/ca.key" -out "$root/ca/ca.crt" 2>/dev/null
openssl req -newkey rsa:3072 -nodes -sha256 \
  -subj '/CN=kumi.kumiland.cloud' \
  -keyout "$root/certs/server.key" -out "$root/ca/server.csr" 2>/dev/null
cat > "$root/ca/server.ext" <<'EXT'
subjectAltName=DNS:kumi.kumiland.cloud
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
EXT
openssl x509 -req -in "$root/ca/server.csr" -CA "$root/ca/ca.crt" \
  -CAkey "$root/ca/ca.key" -CAcreateserial -days 365 -sha256 \
  -extfile "$root/ca/server.ext" -out "$root/certs/server.crt" 2>/dev/null
openssl verify -CAfile "$root/ca/ca.crt" -verify_hostname kumi.kumiland.cloud "$root/certs/server.crt"

# Secrets are written directly to files, never shell arguments or output.
python3 - "$root" "$client_dir" <<'PY'
import pathlib, secrets, sys
root, client = map(pathlib.Path, sys.argv[1:])
publisher, subscriber = secrets.token_hex(32), secrets.token_hex(32)
(root / 'config/passwords').write_text(f'door-sensor:{publisher}\ncare-backend:{subscriber}\n')
common = 'MQTT_HOST=kumi.kumiland.cloud\nMQTT_PORT=8883\n'
(client / 'bridge.env').write_text(common + f'MQTT_USERNAME=door-sensor\nMQTT_PASSWORD={publisher}\nCARE_HOME_ID=main\nCARE_DEVICE_ID=front-door\n')
(root / 'admin/backend.env').write_text(common + f'MQTT_USERNAME=care-backend\nMQTT_PASSWORD={subscriber}\n')
# Separate local-only subscriber profile for mosquitto_sub acceptance testing.
(root / 'admin/mosquitto_sub').write_text(f'-h kumi.kumiland.cloud\n-p 8883\n-u care-backend\n-P {subscriber}\n--cafile /mosquitto/ca.crt\n')
PY
# Convert plaintext password file to salted hashes before starting the broker.
docker run --rm --network none --user 0:0 \
  --entrypoint mosquitto_passwd -v "$root/config:/work" "$image" -U /work/passwords
install -o che -g che -m 644 "$root/ca/ca.crt" "$client_dir/ca.crt"
chown -R che:che "$client_dir"
chmod 600 "$client_dir/bridge.env"
chown -R 1883:1883 "$root/config" "$root/certs" "$root/data"
chmod 640 "$root/config/"* "$root/certs/"*
cd "$root"
docker compose config --quiet
docker compose up -d
sleep 2
docker inspect --format '{{.State.Status}}' kumi-care-mosquitto
# Docker host networking does not bypass UFW. Do not enable/reset an inactive
# firewall (which could disturb existing SSH/LINE); add only the required rule.
if command -v ufw >/dev/null && ufw status | grep -q '^Status: active'; then
  ufw allow 8883/tcp comment 'Kumi Care authenticated TLS MQTT'
fi
systemctl is-active caddy
runuser -u che -- env XDG_RUNTIME_DIR=/run/user/1000 systemctl --user is-active hermes-gateway
printf '\nInstalled. Credentials are protected under %s (publisher only).\n' "$client_dir"
printf 'Next: external TLS/auth/ACL smoke test. Server certificate expires in 365 days.\n'
