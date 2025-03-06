### Using APRSD Terminal Chat

APRSD rich chat requires a properly configured aprsd.conf.

You can generate a sample config file by running

```bash
aprsd sample-config > ~/.config/aprsd/aprsd.conf
```

Then edit the file and set your callsign, and the login for the aprs_network.

A sample config is blow.

```yaml
[DEFAULT]

#
# From aprsd.conf
#

# Callsign to use for messages sent by APRSD (string value)
#callsign = <None>

# Latitude for the GPS Beacon button.  If not set, the button will not
# be enabled. (string value)
#latitude = <None>

# Longitude for the GPS Beacon button.  If not set, the button will
# not be enabled. (string value)
#longitude = <None>

[aprs_network]

#
# From aprsd.conf
#

# Set enabled to False if there is no internet connectivity.This is
# useful for a direwolf KISS aprs connection only. (boolean value)
#enabled = true

# APRS Username (string value)
#login = NOCALL

# APRS Password Get the passcode for your callsign here:
# https://apps.magicbug.co.uk/passcode (string value)
#password = <None>

# The APRS-IS hostname (host address value)
#host = noam.aprs2.net

# APRS-IS port (port value)
# Minimum value: 0
# Maximum value: 65535
#port = 14580


[kiss_serial]

#
# From aprsd.conf
#

# Enable Serial KISS interface connection. (boolean value)
#enabled = false

# Serial Device file to use.  /dev/ttyS0 (string value)
#device = <None>

# The Serial device baud rate for communication (integer value)
#baudrate = 9600

# The APRS path to use for wide area coverage. (list value)
#path = WIDE1-1,WIDE2-1


[kiss_tcp]

#
# From aprsd.conf
#

# Enable Serial KISS interface connection. (boolean value)
#enabled = false

# The KISS TCP Host to connect to. (host address value)
#host = <None>

# The KISS TCP/IP network port (port value)
# Minimum value: 0
# Maximum value: 65535
#port = 8001

# The APRS path to use for wide area coverage. (list value)
#path = WIDE1-1,WIDE2-1
```

### Getting Help

To view all command-line options for aprsd and all installed extensions, after installation, simply type:

```bash
aprsd --help
```

### APRSD Rich cli extension help

[GitHub Issues](https://github.com/hemna/aprsd-rich-cli-extension/issues) are the best place to report bugs.

[GitHub Discussions](https://github.com/hemna/aprsd-rich-cli-extension/discussions) are a good place to start with other issues, feature requests, etc.

### APRSD help

[GitHub Issues](https://github.com/craigerl/aprsd/issues) are the best place to report bugs.

[GitHub Discussions](https://github.com/craigerl/aprsd/discussions) are a good place to start with other issues, feature requests, etc.
