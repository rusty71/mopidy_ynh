#!/bin/bash

#=================================================
# COMMON VARIABLES AND CUSTOM HELPERS
#=================================================

# The components of the stack.  They are installed as editable git
# checkouts, so the code can be changed on the server and the services
# restarted.
#
# Each one is pinned to a tag *and* to the commit the tag points at: the tag
# alone could be moved, the commit cannot.  The commit is the one the tag
# points at, not the tag object itself.
MOPIDY_REPO="https://github.com/rusty71/mopidy.git"
MOPIDY_TAG="ynh-1.1"
MOPIDY_COMMIT="bf058a448ad32cfa923df13b68f554f315132ce3"

IRIS_REPO="https://github.com/rusty71/mopidy-iris.git"
IRIS_TAG="ynh-1.1"
IRIS_COMMIT="7229e3c2c04c0d586e4d8dc083c7067b526301ec"

QOBUZ_REPO="https://github.com/rusty71/mopidy-qobuz.git"
QOBUZ_TAG="ynh-1.0"
QOBUZ_COMMIT="ccfa187cbe58442a56fa03349abf9a98af903091"

# Mopidy-Local is not patched, so it comes from PyPI.
LOCAL_VERSION="3.3.0"

# The Icecast mount Mopidy publishes on.
MOUNT="/low.mp3"

cmd_file="/usr/local/bin/mopidyctl"

media_dir="/home/yunohost.multimedia/share/Music"

#=================================================
# CUSTOM HELPERS
#=================================================

_mopidy_fetch_component() {
    # Fetch a git checkout and check that it is the expected commit.
    #
    # The git commands run as the app user: the checkout belongs to it, and
    # git refuses to work on a repository of another user ("dubious
    # ownership").
    local url="$1"
    local tag="$2"
    local commit="$3"
    local destination="$4"

    if [ -d "$destination/.git" ]; then
        ynh_exec_as_app git -C "$destination" fetch --tags --force origin
    else
        ynh_safe_rm "$destination"
        ynh_exec_as_app git clone --depth 1 --branch "$tag" "$url" "$destination"
    fi

    ynh_exec_as_app git -C "$destination" fetch --depth 1 origin "$commit" 2>/dev/null || true
    ynh_exec_as_app git -C "$destination" checkout --force "$commit"

    local actual
    actual="$(ynh_exec_as_app git -C "$destination" rev-parse HEAD)"
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
    # The download is done as the app user, so the directory has to belong to
    # it as well.
    ynh_exec_as_app mkdir -p "$wheel_dir"

    ynh_hide_warnings ynh_exec_as_app "$install_dir/venv/bin/pip" download --no-deps --no-cache-dir \
        --dest "$wheel_dir" "Mopidy-Iris==3.70.0" >/dev/null

    local wheel
    wheel="$(find "$wheel_dir" -name 'mopidy_iris-*.whl' | head -1)"
    if [ -z "$wheel" ]; then
        ynh_die "Could not download the Mopidy-Iris wheel"
    fi

    ynh_exec_as_app mkdir -p "$wheel_dir/unpacked"
    ynh_exec_as_app unzip -q -o "$wheel" -d "$wheel_dir/unpacked"

    if [ ! -d "$wheel_dir/unpacked/mopidy_iris/static" ]; then
        ynh_die "The Mopidy-Iris wheel does not contain the frontend"
    fi

    ynh_safe_rm "$install_dir/src/mopidy-iris/mopidy_iris/static"
    cp -a "$wheel_dir/unpacked/mopidy_iris/static" \
        "$install_dir/src/mopidy-iris/mopidy_iris/static"

    _mopidy_patch_iris_frontend

    ynh_safe_rm "$wheel_dir"
}

_mopidy_streams_url() {
    # The address of the streams, for the link that the page of the interface
    # gets.  When it is not set, the Icecast app is looked for.
    if [ -n "${streams_url:-}" ]; then
        echo "$streams_url"
        return 0
    fi

    local settings
    for settings in /etc/yunohost/apps/*/settings.yml; do
        [ -f "$settings" ] || continue
        grep -q "^id: icecast2" "$settings" || continue

        local domain port
        domain="$(sed -n 's/^domain: //p' "$settings" | head -1)"
        port="$(sed -n 's/^public_port: //p' "$settings" | head -1)"
        [ -n "$domain" ] || continue
        echo "https://$domain:${port:-8000}"
        return 0
    done

    echo ""
}

_mopidy_patch_iris_frontend() {
    # The Iris frontend is built for the path "/iris": the router uses it as
    # its basename, the pusher websocket and the http endpoints are hardcoded
    # to it, and the service worker is registered on it.
    #
    # When the app is installed under a path, all of that has to follow,
    # otherwise the router matches no route at all: the page stays empty (a
    # black screen), the application never renders and never connects to
    # Mopidy.
    local base="iris"
    if [ -n "${path:-}" ] && [ "$path" != "/" ]; then
        base="${path#/}/iris"
    fi

    ynh_print_info "Pointing the Iris frontend at /$base/"

    # The basename of the router needs its leading slash.
    local basename="/$base"

    local hide_volume="${hide_volume:-true}"

    python3 - "$install_dir/src/mopidy-iris/mopidy_iris/static" "$base" "$basename" "$hide_volume" <<'EOF'
import pathlib
import re
import sys

static = pathlib.Path(sys.argv[1])
base = sys.argv[2]
basename = sys.argv[3]
hide_volume = sys.argv[4] not in ("false", "0", "no", "off", "")

# The base path of the page: the browser uses it to find the assets.
index = static / "index.html"
index_text = ""
if index.exists():
    index_text = index.read_text(encoding="utf-8")
    index_text = index_text.replace('href="/iris/"', f'href="/{base}/"')
    index_text = index_text.replace(
        "register('/iris/service-worker.js')",
        f"register('/{base}/service-worker.js')",
    )

# The router, the pusher websocket and the http endpoints of the frontend.
replacements = [
    (
        "window.location.pathname.indexOf('/iris') > -1 ? '/iris' : '/'",
        f'"{basename}"',
    ),
    (
        'window.location.pathname.indexOf("/iris")>-1?"/iris":"/"',
        f'"{basename}"',
    ),
    ('"/iris/ws/"', f'"/{base}/ws/"'),
    ('"/iris/http/', f'"/{base}/http/'),
]

for name in ("app.js", "app.min.js"):
    path = static / name
    if not path.exists():
        continue
    text = path.read_text(encoding="utf-8")
    for old, new in replacements:
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8")

# The volume of the server is for everyone who listens: the control is
# hidden, so two listeners do not fight over it.
volume_rule = (
    "\n/* The volume control is hidden: the volume belongs to the "
    "server, not to the listener. */\n"
    ".playback-controls section.volume { display: none !important; }\n"
)

for name in ("app.css", "app.min.css"):
    path = static / name
    if not path.exists():
        continue
    text = path.read_text(encoding="utf-8")
    # Take the rule away first, so it is not there twice.
    text = text.replace(volume_rule, "")
    text = text.replace(
        ".playback-controls section.volume { display: none !important; }", ""
    )
    if hide_volume:
        text += volume_rule
    path.write_text(text, encoding="utf-8")

# The build number is the cache buster of the assets: the browser keeps them
# for years, so it has to change when the files are patched.  Otherwise the
# old ones are used and the change stays invisible.
import hashlib

digest = hashlib.sha256()
for name in ("app.js", "app.min.js", "app.css", "app.min.css"):
    path = static / name
    if path.exists():
        digest.update(path.read_bytes())

build = str(int(digest.hexdigest()[:9], 16))
index_text = re.sub(
    r'var build = "[0-9]+";', f'var build = "{build}";', index_text
)

if index.exists():
    index.write_text(index_text, encoding="utf-8")
EOF

    chown -R "$app:" "$install_dir/src/mopidy-iris/mopidy_iris/static"
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

_mopidy_install_titles() {
    # The frontend that publishes the title of the playing track to
    # Icecast.  It lives in this package, so it is copied into the source
    # tree and installed in the venv like the other components.
    ynh_safe_rm "$install_dir/src/icecast-titles"
    cp -a "../sources/icecast_titles" "$install_dir/src/icecast-titles"
    chown -R "$app:" "$install_dir/src/icecast-titles"

    ynh_hide_warnings ynh_exec_as_app "$install_dir/venv/bin/pip" install \
        --no-cache-dir --no-build-isolation --no-deps \
        -e "$install_dir/src/icecast-titles"
}

_mopidy_icecast_app_setting() {
    # Read a key from the settings of the Icecast app.  The two apps are
    # installed together; Mopidy needs the local source port and the admin
    # user from there.
    local key="$1"
    local file

    for file in /etc/yunohost/apps/*/settings.yml; do
        [ -f "$file" ] || continue
        grep -q "^id: icecast2$" "$file" || continue
        sed -n "s/^${key}: //p" "$file" \
            | head -1 \
            | sed -e "s/^'//" -e "s/'$//" -e 's/^"//' -e 's/"$//'
        return 0
    done
}

_mopidy_icecast_source_port() {
    # The plain socket of Icecast that the source connects to.  It is a
    # setting of the Icecast app; fall back to the port after the public
    # one when that app is not installed here.
    local source_port
    source_port="$(_mopidy_icecast_app_setting source_port)"
    [ -n "$source_port" ] || source_port=$(( ${icecast_port:-8000} + 1 ))
    echo "$source_port"
}

_mopidy_icecast_admin_user() {
    # The admin user of the Icecast app, used for the title updates.
    local admin
    admin="$(_mopidy_icecast_app_setting admin)"
    [ -n "$admin" ] || admin="${icecast_admin_user:-admin}"
    echo "$admin"
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

    # The config panel kept the credentials only in the file; the settings
    # are the fallback when the file lost them.
    qobuz_email="${qobuz_email:-$(ynh_app_setting_get --key=qobuz_email)}"
    qobuz_auth_token="${qobuz_auth_token:-$(ynh_app_setting_get --key=qobuz_auth_token)}"
    qobuz_app_id="${qobuz_app_id:-$(ynh_app_setting_get --key=qobuz_app_id)}"
    qobuz_secret="${qobuz_secret:-$(ynh_app_setting_get --key=qobuz_secret)}"

    # The Icecast settings are in the settings of the app.
    icecast_host="$(ynh_app_setting_get --key=icecast_host)"
    icecast_port="$(ynh_app_setting_get --key=icecast_port)"
    icecast_password="$(ynh_app_setting_get --key=icecast_password)"
    icecast_admin_user="$(ynh_app_setting_get --key=icecast_admin_user)"

    # An installation from before the publisher was dropped kept them in
    # the unit of the publisher.
    local unit="/etc/systemd/system/mopidy-publisher.service"
    if [ -z "$icecast_password" ] && [ -f "$unit" ]; then
        icecast_host="$(sed -n 's/.*--icecast-host=\([^ ]*\).*/\1/p' "$unit" | head -1)"
        icecast_port="$(sed -n 's/.*--icecast-port=\([^ ]*\).*/\1/p' "$unit" | head -1)"
        icecast_password="$(sed -n 's/.*--password=\([^ ]*\).*/\1/p' "$unit" | head -1)"
        icecast_admin_user="$(sed -n 's/.*--admin-user=\([^ ]*\).*/\1/p' "$unit" | head -1)"
    fi
}

_mopidy_config() {
    # Write the configuration file of Mopidy.
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

    # The file is only readable by the app user (mode 400), so it has to be
    # opened for writing before it is changed.
    chmod 600 "$file"

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
