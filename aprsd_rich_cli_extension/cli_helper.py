# Override the aprsd.cli_helper.process_standard_options function
# to allows us to inject a custom log handler.
import logging
import typing as t
from functools import update_wrapper

import aprsd
from aprsd.log import log
from aprsd.utils import trace
from oslo_config import cfg

from aprsd_rich_cli_extension.log import TextualLogHandler

CONF = cfg.CONF
F = t.TypeVar("F", bound=t.Callable[..., t.Any])


def process_standard_options(f: F) -> F:
    def new_func(*args, **kwargs):
        ctx = args[0]
        ctx.ensure_object(dict)
        config_file_found = True
        if kwargs["config_file"]:
            default_config_files = [kwargs["config_file"]]
        else:
            default_config_files = None

        try:
            CONF(
                [],
                project="aprsd",
                version=aprsd.__version__,
                default_config_files=default_config_files,
            )
        except cfg.ConfigFilesNotFoundError:
            config_file_found = False
        ctx.obj["loglevel"] = kwargs["loglevel"]
        # ctx.obj["config_file"] = kwargs["config_file"]
        ctx.obj["quiet"] = kwargs["quiet"]

        # Create the custom log handler entry
        custom_handler = {
            "sink": TextualLogHandler(),
            "serialize": False,
            "format": CONF.logging.logformat,
            "colorize": False,
            "level": ctx.obj["loglevel"],
        }
        log.setup_logging(
            ctx.obj["loglevel"],
            quiet=True,
            custom_handler=custom_handler,
        )
        if CONF.trace_enabled:
            trace.setup_tracing(["method", "api"])

        if not config_file_found:
            LOG = logging.getLogger("APRSD")  # noqa: N806
            LOG.error("No config file found!! run 'aprsd sample-config'")

        del kwargs["loglevel"]
        del kwargs["config_file"]
        del kwargs["quiet"]
        return f(*args, **kwargs)

    return update_wrapper(t.cast(F, new_func), f)
