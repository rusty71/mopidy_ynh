#!/bin/bash

#=================================================
# COMMON VARIABLES AND CUSTOM HELPERS
#=================================================

# The components of the stack.  They are installed as editable git
# checkouts, so the code can be changed on the server and the services
# restarted.
#
# Each one is pinned to a tag *and* to the commit it points at: the tag
# alone could be moved, the commit cannot.
MOPIDY_REPO="https://github.com/rusty71/mopidy.git"
MOPIDY_TAG="ynh-1.0"
MOPIDY_COMMIT="c120c2a24efd7755b25818702cc5e45ae4be2da8"

IRIS_REPO="https://github.com/rusty71/mopidy-iris.git"
IRIS_TAG="ynh-1.0"
IRIS_COMMIT="610822d2f107d0ec6d9195d2a00aa0108913f212"

QOBUZ_REPO="https://github.com/rusty71/mopidy-qobuz.git"
QOBUZ_TAG="ynh-1.0"
QOBUZ_COMMIT="0ef9245a1322319081f9cb2f53dde1b5debb9000"

# Mopidy-Local is not patched, so it comes from PyPI.
LOCAL_VERSION="3.3.0"

# The Icecast mounts the publisher fills.
MOUNT_LOSSY="/low.mp3"
MOUNT_HIRES="/high.ogg"

cmd_file="/usr/local/bin/mopidyctl"

media_dir="/home/yunohost.multimedia/share/Music"

#=================================================
# CUSTOM HELPERS
#=================================================

_mopidy_fetch_component() {
    # Fetch a git checkout and check that it is the expected commit.
    local url="$1"
    local tag="$2"
    local commit="$3"
    local destination="$4"

    if [ -d "$destination/.git" ]; then
        git -C "$destination" fetch --tags --force origin
    else
        ynh_safe_rm "$destination"
        git clone --depth 1 --branch "$tag" "$url" "$destination"
    fi

    git -C "$destination" fetch --depth 1 origin "$commit" 2>/dev/null || true
    git -C "$destination" checkout --force "$commit"

    local actual
    actual="$(git -C "$destination" rev-parse HEAD)"
    if [ "$actual" != "$commit" ]; then
        ynh_die "Wrong commit in $destination: expected $commit, got $actual"
    fi

    # The editable install writes the egg-info in the source directory, so it
    # has to belong to the app user.
    chown -R "$app:" "$destination"
}

_mopidy_install_iris_frontend() {
    # The Iris repository does not contain the built frontend: it is built
    # by the JavaScript toolchain at release time.  The published wheel
    # contains it, so take the assets from there.
    local wheel_dir="$install_dir/.iris-wheel"

    ynh_safe_rm "$wheel_dir"
    mkdir -p "$wheel_dir"

    ynh_exec_as_app "$install_dir/venv/bin/pip" download --no-deps --no-cache-dir \
        --dest "$wheel_dir" "Mopidy-Iris==3.70.0" >/dev/null

    local wheel
    wheel="$(find "$wheel_dir" -name 'mopidy_iris-*.whl' | head -1)"
    if [ -z "$wheel" ]; then
        ynh_die "Could not download the Mopidy-Iris wheel"
    fi

    mkdir -p "$wheel_dir/unpacked"
    ynh_exec_as_app unzip -q -o "$wheel" -d "$wheel_dir/unpacked"

    if [ ! -d "$wheel_dir/unpacked/mopidy_iris/static" ]; then
        ynh_die "The Mopidy-Iris wheel does not contain the frontend"
    fi

    ynh_safe_rm "$install_dir/src/mopidy-iris/mopidy_iris/static"
    cp -a "$wheel_dir/unpacked/mopidy_iris/static" \
        "$install_dir/src/mopidy-iris/mopidy_iris/static"

    ynh_safe_rm "$wheel_dir"
}

_mopidy_install_python() {
    # The Python environment of the app.  It sees the system packages, so
    # that PyGObject and GStreamer come from the distribution instead of
    # being built from source (which needs a recent girepository).
    ynh_exec_as_app python3 -m venv --system-site-packages "$install_dir/venv"
    ynh_hide_warnings ynh_exec_as_app "$install_dir/venv/bin/pip" install --upgrade --no-cache-dir pip setuptools wheel

    # The components of the stack, as editable checkouts.
    _mopidy_fetch_component "$MOPIDY_REPO" "$MOPIDY_TAG" "$MOPIDY_COMMIT" "$install_dir/src/mopidy"
    _mopidy_fetch_component "$IRIS_REPO" "$IRIS_TAG" "$IRIS_COMMIT" "$install_dir/src/mopidy-iris"
    _mopidy_fetch_component "$QOBUZ_REPO" "$QOBUZ_TAG" "$QOBUZ_COMMIT" "$install_dir/src/mopidy-qobuz"

    ynh_hide_warnings ynh_exec_as_app "$install_dir/venv/bin/pip" install --no-cache-dir \
        -e "$install_dir/src/mopidy" \
        -e "$install_dir/src/mopidy-iris" \
        -e "$install_dir/src/mopidy-qobuz" \
        "Mopidy-Local==$LOCAL_VERSION" \
        mutagen

    _mopidy_install_iris_frontend
}

_mopidy_deploy_publisher() {
    # The publisher keeps the Icecast session open across track changes.
    mkdir -p "$install_dir/run"
    chown -R "$app:" "$install_dir/run"

    ynh_config_add --template="../sources/extra_files/icecast-publisher.py" \
        --destination="$install_dir/icecast-publisher.py"

    chmod 755 "$install_dir/icecast-publisher.py"
}

_mopidy_load_config_vars() {
    # Read the Qobuz and Icecast settings from the existing configuration
    # file into the variables the template uses.  This is what keeps an
    # upgrade from wiping the credentials.
    local file="$install_dir/$app.conf"

    [ -f "$file" ] || return 1

    qobuz_email="$(_mopidy_config_get username "$file")"
    qobuz_auth_token="$(_mopidy_config_get auth_token "$file")"
    qobuz_app_id="$(_mopidy_config_get app_id "$file")"
    qobuz_secret="$(_mopidy_config_get secret "$file")"
    qobuz_quality="$(_mopidy_config_get quality "$file")"
    cache_enabled="$(_mopidy_config_get cache_enabled "$file")"
    cache_dir="$(_mopidy_config_get cache_dir "$file")"

    # The Icecast settings are in the systemd unit of the publisher.
    local unit="/etc/systemd/system/mopidy-publisher.service"
    if [ -f "$unit" ]; then
        icecast_host="$(sed -n 's/.*--icecast-host=\([^ ]*\).*/\1/p' "$unit" | head -1)"
        icecast_port="$(sed -n 's/.*--icecast-port=\([^ ]*\).*/\1/p' "$unit" | head -1)"
        icecast_password="$(sed -n 's/.*--password=\([^ ]*\).*/\1/p' "$unit" | head -1)"
        icecast_admin_user="$(sed -n 's/.*--admin-user=\([^ ]*\).*/\1/p' "$unit" | head -1)"
    fi
}

_mopidy_config() {
    # Write the configuration file of Mopidy and of the publisher.
    ynh_config_add --template="app.conf" --destination="$install_dir/$app.conf"

    chmod 400 "$install_dir/$app.conf"
    chown "$app:$app" "$install_dir/$app.conf"
}

#=================================================
# READING AND WRITING THE CONFIGURATION FILE
#=================================================

_mopidy_config_get() {
    # Print the value of a key of the [qobuz] section.
    local key="$1"
    local file="${2:-$install_dir/$app.conf}"

    [ -f "$file" ] || return 1

    sed -n '/^\[qobuz\]/,/^\[/p' "$file" \
        | grep -E "^[[:space:]]*${key}[[:space:]]*=" \
        | head -1 \
        | cut -d= -f2- \
        | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'
}

_mopidy_config_set() {
    # Set the value of a key of the [qobuz] section.
    local key="$1"
    local value="$2"
    local file="${3:-$install_dir/$app.conf}"

    [ -f "$file" ] || ynh_die "The configuration file $file does not exist"

    python3 - "$file" "$key" "$value" <<'EOF'
import re
import sys

path, key, value = sys.argv[1], sys.argv[2], sys.argv[3]

with open(path, encoding="utf-8") as handle:
    lines = handle.readlines()

output = []
in_section = False
replaced = False

for line in lines:
    if re.match(r"^\s*\[", line):
        in_section = line.strip() == "[qobuz]"
    if in_section and not replaced and re.match(rf"^\s*{re.escape(key)}\s*=", line):
        output.append(f"{key} = {value}\n")
        replaced = True
        continue
    output.append(line)

if not replaced:
    sys.exit(f"Key {key} not found in the [qobuz] section")

with open(path, "w", encoding="utf-8") as handle:
    handle.writelines(output)
EOF

    chown "$app:$app" "$file"
    chmod 400 "$file"
}
