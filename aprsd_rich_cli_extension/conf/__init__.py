from oslo_config import cfg

from aprsd_rich_cli_extension.conf import main


CONF = cfg.CONF
main.register_opts(CONF)