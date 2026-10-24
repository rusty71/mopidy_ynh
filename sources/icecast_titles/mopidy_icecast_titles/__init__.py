"""Publish the title of the playing track to an Icecast mount.

Mopidy streams to Icecast through GStreamer's ``shout2send``.  That element
turns every tag event into an Icecast metadata update, and a tagless stream
(Qobuz serves tagless FLAC) would keep announcing ``Unknown - Unknown``.  The
audio patch of the Mopidy fork drops those tag events on their way to the
output; this frontend then tells Icecast the real title.
"""

import pathlib

from mopidy import config, ext

__version__ = "1.0.0"


class Extension(ext.Extension):
    dist_name = "Mopidy-IcecastTitles"
    ext_name = "icecast_titles"
    version = __version__

    def get_default_config(self):
        return config.read(pathlib.Path(__file__).parent / "ext.conf")

    def get_config_schema(self):
        schema = config.ConfigSchema(self.ext_name)
        schema["enabled"] = config.Boolean()
        schema["url"] = config.String()
        schema["mount"] = config.String()
        schema["username"] = config.String()
        schema["password"] = config.Secret()
        return schema

    def setup(self, registry):
        from .frontend import IcecastTitlesFrontend

        registry.add("frontend", IcecastTitlesFrontend)
