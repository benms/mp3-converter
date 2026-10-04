"""Apply a per-file kernel limit before executing a conversion tool on Linux."""

import os
import resource
import sys

if __name__ == "__main__":
    limit = int(sys.argv[1])
    resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))
    os.execvp(sys.argv[2], sys.argv[2:])
